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

// Sharing page views (layouts x graphs): screenshots, console errors, axe and the bytes each view loads.
//   SA_PW=<root password> node sharing-views.js <base-url> <outdir> <tag> <what>
//     what: "page"   the page as it opens (the random view, or the 1.0.i page), no view parameters
//           "all"    every valid combination the page lists (SAviews.combos())
//           "l2:g4+g1,l4:g3"   these combinations
//   env: VP=1920x1080,390x844 (viewports)  SHOTS=0 (checks only)  FULL=1 (full-page captures)
//        OPEN=1 (open the "Recording settings" card first)
//        EXPLAIN=1 (open "How hosts are counted")  LIST=1 (open the hosts table's full list)  (2026-11-23)
//        PW=playwright (module to load; default playwright-core with the system Chrome)
// Writes <outdir>/<tag>-<combo>-<w>x<h>.png and <outdir>/report-<tag>.json.
const pw = require(process.env.PW || 'playwright-core');
const fs = require('fs');
const path = require('path');
const axeSource = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');

const VPS = (process.env.VP || '1920x1080,390x844').split(',').map((s) => {
  const [w, h] = s.split('x').map(Number);
  return { width: w, height: h };
});

(async () => {
  const [base, out, tag, what] = process.argv.slice(2);
  fs.mkdirSync(out, { recursive: true });
  const launch = { args: ['--ignore-certificate-errors'] };
  if (!process.env.PW) { launch.channel = 'chrome'; }
  const browser = await pw.chromium.launch(launch);
  const report = { tag, when: new Date().toISOString(), runs: [] };
  for (const vp of VPS) {
    const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: vp });
    const page = await ctx.newPage();
    await page.goto(base + '/');
    await page.fill('#usernamefld', 'root');
    await page.fill('#passwordfld', process.env.SA_PW);
    await Promise.all([page.waitForNavigation(), page.click('button[type=submit], input[type=submit]')]);
    let combos;
    if (what === 'page') {
      combos = [null];
    } else if (what === 'all') {
      await page.goto(base + '/ui/shaperinsight/sharing', { waitUntil: 'load' });
      await page.waitForSelector('[data-sa-ready="1"]', { timeout: 30000 });
      combos = await page.evaluate(() => window.SAviews.combos());
    } else {
      combos = what.split(',').map((c) => { const [l, g] = c.split(':'); return { layout: l, graphs: g ? g.split('+') : [] }; });
    }
    for (const c of combos) {
      const errors = [];
      const bytes = {};
      const onCon = (m) => { if (m.type() === 'error' || m.type() === 'warning') { errors.push(m.type() + ': ' + m.text()); } };
      const onErr = (e) => errors.push('pageerror: ' + e.message);
      const onResp = async (r) => {
        if (r.status() >= 400) { errors.push(r.status() + ' ' + r.url()); }
        try { bytes[new URL(r.url()).pathname] = (await r.body()).length; } catch (e) { /* redirect */ }
      };
      page.on('console', onCon); page.on('pageerror', onErr); page.on('response', onResp);
      const q = c ? '?layout=' + c.layout + '&graphs=' + c.graphs.join(',') : '';
      if (!c) {
        // the page as a first-time viewer sees it: nothing remembered
        await page.goto(base + '/ui/shaperinsight/sharing', { waitUntil: 'load' });
        await page.evaluate(() => { try { localStorage.clear(); } catch (e) { /* none */ } });
      }
      const t0 = Date.now();
      await page.goto(base + '/ui/shaperinsight/sharing' + q, { waitUntil: 'load' });
      await page.waitForSelector('[data-sa-ready="1"]', { timeout: 30000 }).catch(() => errors.push('not ready in 30 s'));
      const readyMs = Date.now() - t0;
      await page.waitForTimeout(3500);
      await page.evaluate(() => window.scrollTo(0, 0));
      const name = tag + '-' + (c ? c.layout + '-' + c.graphs.join('+') : 'page') + '-' + vp.width + 'x' + vp.height;
      const info = await page.evaluate(() => ({
        view: window.SAviews ? window.SAviews.current() : null,
        note: (document.querySelector('#sa-view-note') || {}).innerText || '',
        overflowX: document.documentElement.scrollWidth > window.innerWidth,
        canvases: Array.from(document.querySelectorAll('.sa-page canvas')).filter((x) => x.offsetWidth > 0).length,
        svgs: Array.from(document.querySelectorAll('.sa-page svg.sa-hz')).length,
        height: document.documentElement.scrollHeight
      }));
      if (process.env.OPEN === '1') {
        // the recording settings card, open, scrolled into the capture
        await page.evaluate(() => { const d = document.getElementById('sa-rec'); if (d) { d.open = true; d.scrollIntoView(); window.scrollBy(0, -80); } });
        await page.waitForTimeout(800);
      }
      if (process.env.EXPLAIN === '1') {
        // "How hosts are counted" (1.0.k), open, scrolled into the capture
        await page.evaluate(() => {
          const d = Array.from(document.querySelectorAll('details.sa-explain')).find((x) => /How hosts are counted/.test(x.innerText));
          if (d) { d.open = true; d.scrollIntoView(); window.scrollBy(0, -80); }
        });
        await page.waitForTimeout(800);
      }
      if (process.env.LIST === '1') {
        // the hosts table's full list (the newest step from the sampler, 1.0.k: sent + how counted per host)
        // the "List every host" button shows only when hosts are grouped; otherwise a sort by Name lists every host too
        // (one pipe is on screen at a time: the other pipe's table is in the DOM but hidden)
        const b = page.locator('.sa-open').filter({ visible: true });
        const h = page.locator('.tabulator-col[tabulator-field="name"] .tabulator-col-title').filter({ visible: true });
        if (await b.count()) {
          await b.first().click();
        } else if (await h.count()) {
          await h.first().click();
        }
        await page.waitForTimeout(2500);
        await page.evaluate(() => { const t = document.querySelector('.sa-find'); if (t) { t.scrollIntoView(); window.scrollBy(0, -120); } });
      }
      if (process.env.SHOTS !== '0') {
        const foot = await page.addStyleTag({ content: '.page-foot { position: static !important; }' });
        await page.screenshot({ path: path.join(out, name + '.png'), fullPage: process.env.FULL === '1' });
        await foot.evaluate((el) => el.remove());
      }
      await page.addScriptTag({ content: axeSource });
      const axe = await page.evaluate(async () => {
        const r = await window.axe.run({ include: [['.sa-page']] }, {
          runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] },
          rules: { 'target-size': { enabled: true } }
        });
        return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length,
          targets: v.nodes.slice(0, 5).map((n) => n.target.join(' ')) }));
      });
      page.off('console', onCon); page.off('pageerror', onErr); page.off('response', onResp);
      const own = Object.entries(bytes).filter(([k]) => /shaperinsight|chartjs|chart\.umd|tabulator/.test(k));
      report.runs.push({ name, combo: c, viewport: vp.width + 'x' + vp.height, readyMs, info, errors, axe,
        scripts: own.filter(([k]) => k.endsWith('.js')), api: Object.entries(bytes).filter(([k]) => k.startsWith('/api/')) });
      console.log(name, `ready ${readyMs} ms, errors ${errors.length}, axe ${axe.length}`, axe.map((v) => v.id + '(' + v.nodes + ')').join(' '),
        errors.slice(0, 3).join(' | '), info.view ? JSON.stringify(info.view) : '');
    }
    await ctx.close();
  }
  await browser.close();
  fs.writeFileSync(path.join(out, 'report-' + tag + '.json'), JSON.stringify(report, null, 1));
})().catch((e) => { console.error(e); process.exit(1); });
