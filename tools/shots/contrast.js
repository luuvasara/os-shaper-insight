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

// print every color-contrast failure on a page with its colours
const { chromium } = require('playwright-core');
const fs = require('fs');
const axe = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');
(async () => {
  const [base, p] = process.argv.slice(2);
  const b = await chromium.launch({ channel: 'chrome' });
  const page = await (await b.newContext({ ignoreHTTPSErrors: true })).newPage();
  await page.goto(base + '/'); await page.fill('#usernamefld', 'root'); await page.fill('#passwordfld', process.env.SA_PW);
  await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);
  await page.goto(base + p); await page.waitForSelector('[data-sa-ready="1"]'); await page.waitForTimeout(1500);
  await page.addScriptTag({ content: axe });
  const r = await page.evaluate(async () => (await axe.run(document, { runOnly: ['color-contrast'] })).violations);
  for (const v of r) for (const n of v.nodes) console.log(n.target.join(' '), '|', n.any[0].data && JSON.stringify(n.any[0].data).slice(0, 160));
  await b.close();
})();
