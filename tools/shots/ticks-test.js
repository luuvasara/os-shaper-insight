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

// Setup page, keyboard only: untick the wan row with Space, check focus comes back and the link
// leaves step 2; reload (the tick is saved); tick it again (its speeds are kept).
//   SA_PW=<root password> node ticks-test.js
const { chromium } = require('playwright-core');
(async () => {
  const b = await chromium.launch({ channel: 'chrome', args: ['--ignore-certificate-errors'] });
  const ctx = await b.newContext({ ignoreHTTPSErrors: true });
  const p = await ctx.newPage();
  const base = 'https://127.0.0.1:18444';
  await p.goto(base + '/'); await p.fill('#usernamefld', 'root'); await p.fill('#passwordfld', process.env.SA_PW);
  await Promise.all([p.waitForNavigation(), p.click('button[type=submit]')]);
  await p.goto(base + '/ui/shaperinsight/wizard'); await p.waitForSelector('body[data-sa-ready="1"]');
  const id = await p.$eval('.sa-pick[data-key="wan"]', e => e.id);
  await p.focus('#' + id); await p.keyboard.press('Space');
  await p.waitForFunction(() => document.querySelectorAll('#sa-rates-table tbody tr[data-port]').length === 1, null, { timeout: 20000 });
  await p.waitForTimeout(500);
  console.log('after untick: focus', await p.evaluate(() => document.activeElement.id), 'links', await p.$$eval('#sa-rates-table tbody tr[data-port]', r => r.map(x => x.dataset.port)));
  await p.reload(); await p.waitForSelector('body[data-sa-ready="1"]');
  console.log('after reload: wan checked', await p.$eval('.sa-pick[data-key="wan"]', e => e.checked));
  await p.focus('.sa-pick[data-key="wan"]'); await p.keyboard.press('Space');
  await p.waitForFunction(() => document.querySelectorAll('#sa-rates-table tbody tr[data-port]').length === 2, null, { timeout: 20000 });
  console.log('re-ticked: links', await p.$$eval('#sa-rates-table tbody tr[data-port]', r => r.map(x => x.dataset.port + ':' + x.querySelector('.sa-down').value)));
  await b.close();
})();
