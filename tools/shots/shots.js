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

// Screenshots, axe checks and page timings for the plugin pages on a test firewall.
//   SA_PW=<root password> node shots.js <base-url> <outdir> <tag> [/ui/path ...]
// Uses the system Chrome (playwright-core, channel "chrome"). Writes <outdir>/<page>-<w>x<h>-<tag>.png
// and <outdir>/report-<tag>.json (axe violations, load time, long tasks, transferred bytes).
const { chromium } = require('playwright-core');
const fs = require('fs');
const path = require('path');
const axeSource = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');

const VIEWPORTS = [{ width: 1920, height: 1080 }, { width: 390, height: 844 }];

(async () => {
  const [base, out, tag, ...paths] = process.argv.slice(2);
  const pages = paths.length ? paths : ['/ui/core/dashboard', '/ui/shaperinsight/', '/ui/shaperinsight/compiled', '/ui/shaperinsight/wizard'];
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', args: ['--ignore-certificate-errors'] });
  const report = { tag, when: new Date().toISOString(), pages: [] };
  for (const vp of VIEWPORTS) {
    const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: vp });
    await ctx.addInitScript(() => {
      window.__longtasks = [];
      try {
        new PerformanceObserver((l) => l.getEntries().forEach((e) => window.__longtasks.push(
          { start: Math.round(e.startTime), ms: Math.round(e.duration) }))).observe({ type: 'longtask', buffered: true });
      } catch (e) { /* not supported */ }
    });
    const page = await ctx.newPage();
    await page.goto(base + '/');
    await page.fill('#usernamefld', 'root');
    await page.fill('#passwordfld', process.env.SA_PW);
    await Promise.all([page.waitForNavigation(), page.click('button[type=submit], input[type=submit]')]);
    for (const p of pages) {
      const bytes = {};
      const onResp = async (r) => {
        const u = new URL(r.url());
        try { bytes[u.pathname] = (await r.body()).length; } catch (e) { /* redirects */ }
      };
      page.on('response', onResp);
      const t0 = Date.now();
      await page.goto(base + p, { waitUntil: 'load' });
      const loadMs = Date.now() - t0;
      // first data on screen, then one more poll so the graph has two points
      await page.waitForSelector('[data-sa-ready="1"]', { timeout: 30000 }).catch(() => {});
      const readyMs = Date.now() - t0;
      await page.waitForTimeout(p.includes('compiled') ? 1000 : 12000);
      page.off('response', onResp);
      // core's menu scrolls the window so a deep active menu entry is visible (the bufferbloat
      // page lands at ~312 px on 1920x1080); capture from the top like the other pages
      await page.evaluate(() => window.scrollTo(0, 0));
      const name = `${p.replace(/^\/ui\//, '').replace(/\W+/g, '-').replace(/-$/, '') || 'index'}-${vp.width}x${vp.height}-${tag}`;
      // OPNsense's footer is position:fixed at the viewport bottom; a full-page capture keeps
      // it where the first viewport ended, mid-page. For the capture only, let it sit at the
      // end of the page as it does when a user scrolls down (a fixed element is not part of
      // the page flow, so nothing else moves).
      const foot = await page.addStyleTag({ content: '.page-foot { position: static !important; }' });
      await page.screenshot({ path: path.join(out, name + '.png'), fullPage: true });
      await foot.evaluate((el) => el.remove());
      await page.addScriptTag({ content: axeSource });
      const axe = await page.evaluate(async () => {
        const r = await window.axe.run(document, {
          runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] },
          rules: { 'target-size': { enabled: true } }
        });
        return r.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length,
          targets: v.nodes.slice(0, 5).map((n) => n.target.join(' ')) }));
      });
      const perf = await page.evaluate(() => ({
        longtasks: window.__longtasks,
        nav: (() => { const n = performance.getEntriesByType('navigation')[0];
          return n ? { domContentLoaded: Math.round(n.domContentLoadedEventEnd), load: Math.round(n.loadEventEnd) } : null; })(),
        overflowX: document.documentElement.scrollWidth > window.innerWidth
      }));
      const own = Object.entries(bytes).filter(([k]) => k.includes('shaperinsight')).reduce((a, [, v]) => a + v, 0);
      report.pages.push({ page: p, viewport: `${vp.width}x${vp.height}`, file: name + '.png', loadMs, readyMs,
        perf, ownBytes: own, apiBytes: Object.entries(bytes).filter(([k]) => k.startsWith('/api/')),
        axe });
      console.log(name, `load ${loadMs} ms, ready ${readyMs} ms, axe ${axe.length} rule(s):`,
        axe.map((v) => `${v.id}(${v.impact},${v.nodes})`).join(' '));
    }
    await ctx.close();
  }
  await browser.close();
  fs.writeFileSync(path.join(out, `report-${tag}.json`), JSON.stringify(report, null, 1));
})().catch((e) => { console.error(e); process.exit(1); });
