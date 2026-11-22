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

// Screenshot the apply countdown banner on the status page, the setup page and the dashboard
// while an apply is waiting for its confirmation (start one first).
//   SA_PW=<root password> node banner.js <base-url> <outdir>
const { chromium } = require('playwright-core');
const path = require('path');

(async () => {
  const [base, out] = process.argv.slice(2);
  const browser = await chromium.launch({ channel: 'chrome', args: ['--ignore-certificate-errors'] });
  for (const vp of [{ width: 1920, height: 1080 }, { width: 390, height: 844 }]) {
    const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: vp });
    const page = await ctx.newPage();
    await page.goto(base + '/');
    await page.fill('#usernamefld', 'root');
    await page.fill('#passwordfld', process.env.SA_PW);
    await Promise.all([page.waitForNavigation(), page.click('button[type=submit], input[type=submit]')]);
    for (const [p, name] of [['/ui/shaperinsight/', 'status'], ['/ui/core/dashboard', 'dashboard']]) {
      await page.goto(base + p);
      await page.waitForSelector('#sa-countdown', { timeout: 30000 });
      await page.waitForTimeout(800);
      await page.screenshot({ path: path.join(out, `countdown-banner-${name}-top-${vp.width}x${vp.height}.png`) });
      await page.mouse.wheel(0, 600);       // sticky: it stays at the top while scrolling
      await page.waitForTimeout(1200);
      await page.screenshot({ path: path.join(out, `countdown-banner-${name}-${vp.width}x${vp.height}.png`) });
      console.log(name, vp.width, (await page.textContent('#sa-countdown')).replace(/\s+/g, ' ').trim());
    }
    await ctx.close();
  }
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
