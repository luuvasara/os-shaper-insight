// Copyright (C) 2026 os-shaper-insight contributors
// All rights reserved.
//
// Redistribution and use in source and binary forms, with or without
// modification, are permitted provided that the following conditions are met:
//
// 1. Redistributions of source code must retain the above copyright notice,
//    this list of conditions and the following disclaimer.
//
// 2. Redistributions in binary form must reproduce the above copyright
//    notice, this list of conditions and the following disclaimer in the
//    documentation and/or other materials provided with the distribution.
//
// THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
// INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
// AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
// AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
// OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
// SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
// INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
// CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
// ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
// POSSIBILITY OF SUCH DAMAGE.

/*
 * Every table on the rendered pages follows the plugin's table convention (user feedback 2026-11-22): built by
 * SA.table on core's Tabulator, so each table
 *   - sorts: at least one sortable header, and every sortable header is keyboard-reachable
 *     (tabindex 0) and carries aria-sort; pressing Enter on it changes the order of the rows;
 *   - has an empty state (Tabulator placeholder text) when there are no rows;
 *   - fits 390 px: no horizontal page scroll at 390x844;
 *   - shows a swatch in rows the page says have a chart series (data-sa-swatch tables);
 *   - links object names the core way (an .sa-ref with a tooltip; a link icon with an accessible name).
 * A plain <table> is a FAIL unless it carries data-sa-form="<why>" (an input form, where sorting would
 * move fields while they are typed in), or it belongs to core (a model grid's bootgrid, a settings form).
 *   SA_PW=... node tables-check.js https://127.0.0.1:18444 [/ui/shaperinsight/... ...]
 *   SA_TABLES_NEGATIVE=1 ... turns the sortable headers off first: every page with a table must FAIL
 * Exit 1 when anything is missing.
 */
const { chromium } = require('playwright-core');

(async () => {
    const [base, ...pages] = process.argv.slice(2);
    const list = pages.length ? pages : ['/ui/shaperinsight/', '/ui/shaperinsight/sharing', '/ui/shaperinsight/wizard',
        '/ui/shaperinsight/priorities', '/ui/shaperinsight/bufferbloat'];
    const browser = await chromium.launch({ channel: 'chrome', args: ['--ignore-certificate-errors'] });
    const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1920, height: 1080 } });
    const page = await ctx.newPage();
    await page.goto(base + '/');
    await page.fill('#usernamefld', 'root');
    await page.fill('#passwordfld', process.env.SA_PW);
    await Promise.all([page.waitForNavigation(), page.click('button[type=submit], input[type=submit]')]);
    let bad = 0;
    for (const p of list) {
        const problems = [];
        let counted = 0;
        for (const vp of [{ width: 1920, height: 1080 }, { width: 390, height: 844 }]) {
            await page.setViewportSize(vp);
            await page.goto(base + p, { waitUntil: 'load' });
            await page.waitForSelector('[data-sa-ready="1"]', { timeout: 30000 }).catch(() => {});
            await page.waitForTimeout(3000);
            // open collapsed sections so their tables are measured too
            await page.evaluate(() => document.querySelectorAll('main details, .page-content details').forEach((d) => { d.open = true; }));
            await page.waitForTimeout(500);
            if (process.env.SA_TABLES_NEGATIVE) {
                await page.evaluate(() => document.querySelectorAll('.tabulator-col').forEach((h) => {
                    h.removeAttribute('tabindex'); h.removeAttribute('aria-sort'); h.classList.remove('tabulator-sortable');
                }));
            }
            const r = await page.evaluate(() => {
                const out = { grids: 0, issues: [], scroll: document.documentElement.scrollWidth - window.innerWidth };
                const root = document.querySelector('.page-content-main') || document.body;
                root.querySelectorAll('table').forEach((t) => {
                    // core's own: a model grid (bootgrid) and a settings form (base_form lays its fields out in a table)
                    if (t.closest('.tabulator, .bootgrid-table, [data-sa-form], #mainmenu, .modal, form[id^="frm_"]') || t.hasAttribute('data-sa-form')) { return; }
                    if (!t.offsetParent) { return; }
                    out.issues.push('plain <table id="' + (t.id || '?') + '"> (use SA.table, or data-sa-form with a reason)');
                });
                root.querySelectorAll('.sa-grid').forEach((g) => {
                    if (!g.offsetParent) { return; }
                    out.grids++;
                    const id = g.id || '?';
                    const heads = [...g.querySelectorAll('.tabulator-col.tabulator-sortable')];
                    if (!heads.length) { out.issues.push(id + ': no sortable column'); }
                    heads.forEach((h) => {
                        const name = h.textContent.trim();
                        if (h.getAttribute('tabindex') !== '0') { out.issues.push(id + ': header "' + name + '" not keyboard-reachable'); }
                        if (!h.hasAttribute('aria-sort')) { out.issues.push(id + ': header "' + name + '" has no aria-sort'); }
                    });
                    const rows = g.querySelectorAll('.tabulator-row').length;
                    if (!rows) {
                        const ph = g.querySelector('.tabulator-placeholder');
                        if (!ph || !ph.textContent.trim()) { out.issues.push(id + ': no rows and no empty-state text'); }
                    }
                    if (g.hasAttribute('data-sa-swatch') && rows && !g.querySelector('.tabulator-row .sa-swatch')) {
                        out.issues.push(id + ': rows have chart series but no swatch');
                    }
                    g.querySelectorAll('.sa-ref').forEach((ref) => {
                        if (!ref.getAttribute('title') && !ref.getAttribute('data-original-title')) { out.issues.push(id + ': link name without hover content'); }
                    });
                    g.querySelectorAll('a').forEach((a) => {
                        if (!(a.getAttribute('aria-label') || a.textContent.trim())) { out.issues.push(id + ': link without an accessible name'); }
                    });
                });
                return out;
            });
            counted = Math.max(counted, r.grids);
            r.issues.forEach((x) => problems.push(vp.width + ': ' + x));
            if (vp.width === 390 && r.scroll > 1) { problems.push('390: page scrolls sideways by ' + r.scroll + ' px'); }
            // Enter on the first sortable header of the first multi-row grid must change the sort state
            if (vp.width === 1920) {
                const moved = await page.evaluate(async () => {
                    const g = [...document.querySelectorAll('.sa-grid')].find((x) => x.offsetParent && x.querySelectorAll('.tabulator-row').length > 1);
                    if (!g) { return null; }
                    const h = g.querySelector('.tabulator-col.tabulator-sortable[tabindex="0"]');
                    if (!h) { return false; }
                    const before = h.getAttribute('aria-sort');
                    h.focus();
                    h.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
                    await new Promise((res) => setTimeout(res, 300));
                    return h.getAttribute('aria-sort') !== before;
                });
                if (moved === false) { problems.push('1920: Enter on a header did not sort'); }
            }
        }
        if (problems.length || (process.env.SA_TABLES_NEGATIVE && !counted)) {
            bad++;
            console.log('FAIL ' + p + ' (' + counted + ' tables)\n  ' + [...new Set(problems)].slice(0, 12).join('\n  '));
        } else {
            console.log('PASS ' + p + ' (' + counted + ' tables, sortable by keyboard, empty states, 390 px)');
        }
    }
    await browser.close();
    process.exitCode = bad ? 1 : 0;
})().catch((e) => { console.error(e); process.exit(1); });
