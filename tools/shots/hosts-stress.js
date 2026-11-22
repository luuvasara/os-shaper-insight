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
 * The sharing page's hosts table with many hosts (host simulation rules, TESTING.md): the newest-step endpoint is
 * answered with N fake hosts (documentation ranges, 192.0.2.0/24 style blocks and 2001:db8::/32), and
 * the history says N-2 other hosts. Checks: the default view stays small (coloured hosts + one expander
 * + not attributed); opening it lists the hosts with a windowed DOM beyond 100 rows; search finds a
 * host by name part and announces the count; a sort by IP is address-aware.
 *   SA_PW=... node hosts-stress.js https://127.0.0.1:18444 [N]
 */
const { chromium } = require('playwright-core');

(async () => {
    const base = process.argv[2];
    const N = parseInt(process.argv[3] || '1000', 10);
    const fake = [];
    for (let i = 0; i < N; i++) {
        const ip = i % 5 === 4 ? '2001:db8::' + (i + 1).toString(16) : '198.51.' + (100 + Math.floor(i / 250)) + '.' + (i % 250 + 1);
        fake.push([ip, Math.round(4e7 / (i + 1))]);
    }
    const browser = await chromium.launch({ channel: 'chrome', args: ['--ignore-certificate-errors'] });
    const page = await (await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1920, height: 1080 } })).newPage();
    await page.goto(base + '/');
    await page.fill('#usernamefld', 'root');
    await page.fill('#passwordfld', process.env.SA_PW);
    await Promise.all([page.waitForNavigation(), page.click('button[type=submit], input[type=submit]')]);
    await page.route('**/api/shaperinsight/sampler/newest/**', (route) => route.fulfill({ contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', total: N, sending: N, offset: 0, limit: 2000, hosts: fake }) }));
    await page.route('**/api/shaperinsight/sampler/history/**', async (route) => {
        const r = await route.fetch();
        const d = await r.json();
        Object.keys(d.hosts || {}).forEach((k) => { d.hosts_other = d.hosts_other || {}; d.hosts_other[k] = { seen: N - 2, sending: N - 2 }; });
        route.fulfill({ response: r, body: JSON.stringify(d) });
    });
    await page.route('**/api/shaperinsight/devices/list', (route) => route.fulfill({ contentType: 'application/json',
        body: JSON.stringify({ devices: fake.slice(0, 50).map((h, i) => ({ address: h[0], hostname: 'device-' + i + '-with-a-rather-long-name-for-the-table', sources: ['test'] })), aliases: [] }) }));
    await page.goto(base + '/ui/shaperinsight/sharing');
    await page.waitForSelector('#sa-p-10000-hosts .tabulator-row', { timeout: 30000 });
    await page.waitForTimeout(1500);
    const out = {};
    const g = '#sa-p-10000-hosts';
    out.defaultRows = await page.locator(g + ' .tabulator-row').count();
    out.expander = await page.locator(g + ' .sa-open-row').innerText().catch(() => '');
    let t = Date.now();
    await page.click(g + ' .sa-open-row');
    await page.waitForFunction((sel) => document.querySelectorAll(sel + ' .tabulator-row').length > 5, g);
    out.openMs = Date.now() - t;
    out.openDomRows = await page.locator(g + ' .tabulator-row').count();
    out.openNodes = await page.evaluate((sel) => document.querySelector(sel).querySelectorAll('*').length, g);
    out.ariaExpanded = await page.getAttribute('#sa-p-10000-open', 'aria-expanded');
    out.count = await page.innerText('#sa-p-10000-qn');
    t = Date.now();
    await page.fill('#sa-p-10000-q', 'device-42-');
    await page.waitForFunction(() => /match/.test(document.querySelector('#sa-p-10000-qn').textContent));
    out.searchMs = Date.now() - t;
    out.searchCount = await page.innerText('#sa-p-10000-qn');
    out.searchRows = await page.locator(g + ' .tabulator-row').count();
    await page.fill('#sa-p-10000-q', '');
    await page.waitForTimeout(400);
    await page.click(g + ' .tabulator-col[tabulator-field="ip"]');
    await page.waitForTimeout(400);
    out.ipOrder = await page.evaluate((sel) => [...document.querySelectorAll(sel + ' .tabulator-row .tabulator-cell[tabulator-field="ip"]')].slice(0, 4).map((c) => c.textContent), g);
    await page.screenshot({ path: process.env.SA_SHOT || 'hosts-stress.png', fullPage: false });
    console.log(JSON.stringify(out, null, 1));
    let bad = 0;
    const want = (ok, what) => { if (!ok) { bad++; console.log('FAIL ' + what); } else { console.log('PASS ' + what); } };
    want(out.defaultRows <= 12, 'default view: coloured hosts + expander + not attributed (' + out.defaultRows + ' rows)');
    want(/other hosts/.test(out.expander), 'expander names the other hosts');
    want(out.openDomRows < 120, 'open list is windowed (' + out.openDomRows + ' rows in the DOM for ' + N + ')');
    want(out.ariaExpanded === 'true', 'aria-expanded true when open');
    want(out.searchRows === 1 && /1 hosts? match/.test(out.searchCount), 'search finds one host and says so');
    want(out.ipOrder[0] && out.ipOrder[0].indexOf(':') < 0, 'IP sort: IPv4 first');
    await browser.close();
    process.exitCode = bad ? 1 : 0;
})().catch((e) => { console.error(e); process.exit(1); });
