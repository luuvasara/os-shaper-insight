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

// Bufferbloat page: the three stacked charts must share one x-pixel mapping, and hovering one
// must highlight the same second in all three. Exits 1 if either fails; writes a capture.
//   SA_PW=<root password> node crosshair.js <base-url> <out.png>
const { chromium } = require('playwright-core');
(async () => {
  const [base, out] = process.argv.slice(2);
  const b = await chromium.launch({ channel: 'chrome', args: ['--ignore-certificate-errors'] });
  const p = await (await b.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1920, height: 1080 } })).newPage();
  await p.goto(base + '/'); await p.fill('#usernamefld', 'root'); await p.fill('#passwordfld', process.env.SA_PW);
  await Promise.all([p.waitForNavigation(), p.click('button[type=submit]')]);
  await p.goto(base + '/ui/shaperinsight/bufferbloat');
  await p.waitForSelector('[data-sa-ready="1"]', { state: 'attached' });
  await p.waitForTimeout(1500);
  const ids = ['sa-bb-rate', 'sa-bb-lat', 'sa-bb-cake'];
  const areas = await p.evaluate((ids) => ids.map((id) => {
    // the chart area AND where the x scale puts 0 s and the last second (a bar dataset
    // would add an offset to the x scale even with equal chart areas)
    const c = Chart.getChart(id); const a = c.chartArea; const x = c.scales.x;
    return { id, left: Math.round(a.left), right: Math.round(a.right), x0: Math.round(x.getPixelForValue(0)),
      xEnd: Math.round(x.getPixelForValue(x.max)) };
  }), ids);
  const aligned = areas.every((a) => ['left', 'right', 'x0', 'xEnd'].every((k) => a[k] === areas[0][k]));
  console.log('chart areas', JSON.stringify(areas), aligned ? 'ALIGNED' : 'NOT ALIGNED');
  const top = await p.$('#sa-bb-rate');
  await top.scrollIntoViewIfNeeded(); await p.waitForTimeout(300);
  const box = await top.boundingBox();
  await p.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5); await p.waitForTimeout(500);
  const active = await p.evaluate((ids) => ids.map((id) => {
    const a = Chart.getChart(id).getActiveElements(); return a.length ? a[0].index : null;
  }), ids);
  const linked = active.every((i) => i !== null && i === active[0]);
  console.log('active index per chart', JSON.stringify(active), linked ? 'LINKED' : 'NOT LINKED');
  await p.addStyleTag({ content: '.page-foot { position: static !important; }' });
  await (await p.$('.sa-bb-charts')).screenshot({ path: out });
  await b.close();
  process.exit(aligned && linked ? 0 : 1);
})();
