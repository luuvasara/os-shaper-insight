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
 * Every technical term a page shows carries a glossary chip (user feedback 2026-11-22: "WF2Q+ has
 * no tooltip, nor why?"). Two rules, on the rendered pages:
 *   cell: a table cell that names a term has a chip for it (status and plan tables are read
 *         row by row, so the chip must be in the row);
 *   page: a term named anywhere in the page text has at least one chip on that page.
 * Raw text (pre, code), the glossary page itself, form controls and the menu are left out.
 *   SA_PW=... node terms-check.js https://127.0.0.1:18444 [/ui/shaperinsight/... ...]
 *   SA_TERMS_NEGATIVE=1 ... removes the chips first: every page must then FAIL (the check can fail)
 * Exit 1 when anything is missing.
 */
const { chromium } = require('playwright-core');
const fs = require('fs');
const path = require('path');

const GLOSSARY = path.join(__dirname, '..', '..', 'net', 'shaper-insight', 'src', 'opnsense', 'mvc', 'app', 'library',
    'OPNsense', 'ShaperInsight', 'glossary.json');
// the words as the pages write them -> glossary id; schedulers first (the defect that started this)
const WORDS = {
    'WF2Q+': 'wf2q', 'QFQ': 'qfq', 'Deficit Round Robin': 'drr', 'FQ-PIE': 'fq-pie', 'FQ-CoDel': 'fq-codel',
    'CAKE': 'cake', 'FIFO': 'fifo-tail-drop', 'PRIO': 'priority-strict', 'DSCP': 'dscp', 'ECN': 'ecn', 'AQM': 'aqm'
};

(async () => {
    const [base, ...pages] = process.argv.slice(2);
    const list = pages.length ? pages : ['/ui/shaperinsight/', '/ui/shaperinsight/sharing', '/ui/shaperinsight/wizard',
        '/ui/shaperinsight/priorities', '/ui/shaperinsight/bufferbloat'];
    const glossary = JSON.parse(fs.readFileSync(GLOSSARY, 'utf8'));
    Object.values(WORDS).forEach(function (id) {
        if (!glossary[id]) { console.log('FAIL glossary has no entry ' + id); process.exitCode = 1; }
    });
    const browser = await chromium.launch({ channel: 'chrome', args: ['--ignore-certificate-errors'] });
    const page = await (await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1920, height: 1080 } })).newPage();
    await page.goto(base + '/');
    await page.fill('#usernamefld', 'root');
    await page.fill('#passwordfld', process.env.SA_PW);
    await Promise.all([page.waitForNavigation(), page.click('button[type=submit], input[type=submit]')]);
    let bad = 0;
    for (const p of list) {
        await page.goto(base + p, { waitUntil: 'load' });
        await page.waitForSelector('[data-sa-ready="1"]', { timeout: 30000 }).catch(() => {});
        await page.waitForTimeout(2000);
        if (process.env.SA_TERMS_NEGATIVE) {
            // control: the check must fail when the chips are gone (they turn back into plain words)
            await page.evaluate(() => {
                window.MutationObserver = undefined;
                document.querySelectorAll('.sa-term').forEach((b) => b.replaceWith(document.createTextNode(b.textContent)));
            });
        }
        const found = await page.evaluate((words) => {
            const root = document.querySelector('main') || document.body;
            const skip = (el) => el.closest('pre, code, select, option, script, style, nav, #mainmenu, .sa-term, ' +
                'textarea, canvas, .modal, .bootstrap-dialog, header, footer');
            const re = (w) => new RegExp('(^|[^A-Za-z0-9+-])' + w.replace(/[+]/g, '\\+') + '(?![A-Za-z0-9+])');
            const out = { cell: [], page: {} };
            const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
            let n;
            while ((n = walker.nextNode())) {
                const el = n.parentElement;
                if (!el || skip(el) || !el.offsetParent && el.tagName !== 'SUMMARY') { continue; }
                for (const [w, id] of Object.entries(words)) {
                    if (!re(w).test(n.textContent)) { continue; }
                    out.page[id] = out.page[id] || { word: w, chips: 0 };
                    const cell = el.closest('td, th, .tabulator-cell');
                    if (cell && !cell.querySelector('.sa-term[data-term="' + id + '"]')) {
                        out.cell.push(w + ' in "' + cell.textContent.trim().slice(0, 80) + '"');
                    }
                }
            }
            for (const id of Object.keys(out.page)) {
                out.page[id].chips = root.querySelectorAll('.sa-term[data-term="' + id + '"]').length;
            }
            return out;
        }, WORDS);
        const missing = Object.values(found.page).filter((x) => !x.chips).map((x) => x.word);
        const cells = [...new Set(found.cell)];
        if (missing.length || cells.length) {
            bad++;
            console.log('FAIL ' + p + (missing.length ? ' no chip on the page for: ' + missing.join(', ') : '') +
                (cells.length ? '\n  cells without a chip: ' + cells.slice(0, 8).join(' | ') : ''));
        } else {
            console.log('PASS ' + p + ' (' + Object.values(found.page).map((x) => x.word + ' ' + x.chips).join(', ') + ')');
        }
    }
    await browser.close();
    process.exitCode = bad || process.exitCode ? 1 : 0;
})().catch((e) => { console.error(e); process.exit(1); });
