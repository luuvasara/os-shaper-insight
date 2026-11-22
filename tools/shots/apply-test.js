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

// Apply through the wizard page in a real browser, then either confirm or kill the browser.
//   SA_PW=<root password> node apply-test.js <base-url> <outdir> confirm|kill
// confirm: click Apply, screenshot the countdown, click "Keep these settings", screenshot.
// kill:    click Apply, wait for the countdown, close the browser without confirming; then a
//          new browser waits 75 s and screenshots the page, which must show "Back to the restore point".
const { chromium } = require('playwright-core');
const path = require('path');

async function login(browser, base) {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1920, height: 1080 } });
  const page = await ctx.newPage();
  await page.goto(base + '/');
  await page.fill('#usernamefld', 'root');
  await page.fill('#passwordfld', process.env.SA_PW);
  await Promise.all([page.waitForNavigation(), page.click('button[type=submit], input[type=submit]')]);
  return page;
}

async function shot(page, out, name) {
  await page.addStyleTag({ content: '.page-foot { position: static !important; }' });
  await page.locator('#sa-h-plan').scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(out, name), fullPage: false });
}

(async () => {
  const [base, out, mode] = process.argv.slice(2);
  const launch = () => chromium.launch({ channel: 'chrome', args: ['--ignore-certificate-errors'] });
  let browser = await launch();
  let page = await login(browser, base);
  await page.goto(base + '/ui/shaperinsight/wizard?down=500&up=50');
  await page.waitForSelector('[data-sa-ready="1"]');
  await page.click('#sa-apply');
  await page.click('.modal-dialog .btn-primary, .modal-dialog .btn-warning');
  await page.waitForSelector('#sa-confirm', { state: 'visible', timeout: 90000 });
  await page.waitForTimeout(3000);
  await shot(page, out, `apply-countdown-${mode}.png`);
  const txt = await page.textContent('#sa-apply-state');
  console.log('countdown:', txt.trim());
  if (mode === 'confirm') {
    await page.click('#sa-confirm');
    await page.waitForFunction(() => /Confirmed/.test(document.querySelector('#sa-apply-state').textContent), null, { timeout: 15000 });
    await shot(page, out, 'apply-confirmed.png');
    console.log('result:', (await page.textContent('#sa-apply-state')).trim());
    await browser.close();
    return;
  }
  await browser.close();          // the browser dies: nobody will confirm
  console.log('browser closed at', new Date().toISOString());
  await new Promise((r) => setTimeout(r, 75000));
  browser = await launch();
  page = await login(browser, base);
  await page.goto(base + '/ui/shaperinsight/wizard?down=500&up=50');
  await page.waitForSelector('[data-sa-ready="1"]');
  await page.waitForFunction(() => document.querySelector('#sa-apply-state').textContent.trim().length > 0, null, { timeout: 15000 });
  await shot(page, out, 'apply-reverted-after-browser-closed.png');
  console.log('result:', (await page.textContent('#sa-apply-state')).trim());
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
