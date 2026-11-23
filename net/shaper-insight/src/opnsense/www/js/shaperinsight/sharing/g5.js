/*
 * Copyright (C) 2026 os-shaper-insight contributors
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 * INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 * AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 * OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */

/*
 * G5 Time grid: rows are the pipe's state, its classes and its hosts; columns are time buckets. Darker =
 * a bigger share of the cap (log scale); hatched state cells = limiting; a red outline = drops in that
 * class and bucket (only class rows: CAKE counts drops per class, not per host). "When was the line full,
 * and who was on it?" Uses chartjs-chart-matrix as core ships it (/ui/js/chartjs-plugin-matrix.min.js),
 * loaded only while this graph is on screen. Hosts: every stored host, or the busiest per sample + the
 * rest (said under the grid); 25 host rows a page.
 * opts: {select: function (a, b)}
 */
(function () {
    'use strict';
    const SH = window.SAsh;
    const PAGE = 25;

    SH.graphs.g5 = {create: function (el, opts) {
        opts = opts || {};
        const st = {page: 0, sel: null};
        let M0 = null, P0 = null, chart = null, B = null;
        el.innerHTML = '<ul class="sa-legend sa-g5-leg"></ul><div class="sa-chart-box sa-g5-box"><canvas id="' + el.id + '-c" role="img"></canvas></div>' +
            '<div class="sa-pager"></div><p class="sa-meta sa-g5-cov"></p>';
        const canvas = el.querySelector('canvas');
        el.querySelector('.sa-pager').addEventListener('click', function (e) {
            const b = e.target.closest('button[data-p]');
            if (b) { st.page += Number(b.getAttribute('data-p')); api.update(M0, P0); }
        });
        function rgba(hex, a) { const n = parseInt(hex.slice(1), 16); return 'rgba(' + (n >> 16) + ',' + (n >> 8 & 255) + ',' + (n & 255) + ',' + a.toFixed(3) + ')'; }
        const selPlugin = {id: 'saSel', afterDatasetsDraw: function (c) {
            if (!st.sel || !B) { return; }
            const a = c.chartArea, x = c.scales.x, cw = (a.right - a.left) / B.cols;
            const c0 = Math.max(0, Math.floor(st.sel[0] / B.bs)), c1 = Math.min(B.cols - 1, Math.ceil(st.sel[1] / B.bs) - 1);
            if (c1 < c0) { return; }
            c.ctx.save(); c.ctx.strokeStyle = SH.fg(); c.ctx.lineWidth = 2;
            c.ctx.strokeRect(x.getPixelForValue(c0) - cw / 2, a.top - 2, (c1 - c0 + 1) * cw, a.bottom - a.top + 4); c.ctx.restore();
        }};
        if (opts.select) {
            let down = null;
            const col = function (e) { return Math.max(0, Math.min(B.cols - 1, Math.round(chart.scales.x.getValueForPixel(e.offsetX)))); };
            canvas.style.cursor = 'crosshair';
            canvas.addEventListener('pointerdown', function (e) { if (chart && B) { down = col(e); } });
            canvas.addEventListener('pointerup', function (e) {
                if (down === null || !chart) { return; }
                const c = col(e), a = Math.min(c, down), b = Math.max(c, down);
                down = null;
                st.sel = [a * B.bs, Math.min(P0.N, (b + 1) * B.bs)];
                chart.draw();
                opts.select(st.sel[0], st.sel[1]);
            });
        }
        const api = {
            update: function (M, P) {
                M0 = M; P0 = P;
                const th = SH.theme();
                const cols = Math.max(1, Math.min(P.N, SH.phone() ? 24 : 60));
                const bs = Math.ceil(P.N / cols);
                B = {bs: bs, cols: Math.ceil(P.N / bs)};
                const rows = [{label: '▲ Pipe state', kind: 'state'}];
                P.tins.slice().map(function (n, k) { return [n, k]; }).reverse().forEach(function (x) {
                    rows.push({label: x[0] + ' (class)', kind: 'tin', v: P.tinBits[x[1]], d: P.tinDrops[x[1]]});
                });
                const cov = SH.coverage(M, P);
                let total = 0, pages = 1;
                if (cov.mode !== 'none') {
                    const src = P.all || P.hosts.concat(P.other ? [{ip: '', series: P.other}] : []);
                    let hs = src.map(function (h) { return {h: h, tot: SH.sum(h.series, 0, P.N)}; }).filter(function (x) { return x.tot > 0; });
                    hs.sort(function (a, b) { return (a.h.ip === '') - (b.h.ip === '') || b.tot - a.tot; });
                    total = hs.length;
                    pages = Math.max(1, Math.ceil(total / PAGE));
                    st.page = Math.max(0, Math.min(st.page, pages - 1));
                    hs.slice(st.page * PAGE, (st.page + 1) * PAGE).forEach(function (x) {
                        rows.push({label: SH.cut(x.h.ip === '' ? 'other hosts (summed)' : SH.hostName(x.h.ip), SH.phone() ? 14 : 24), kind: 'host',
                            title: x.h.ip === '' ? 'other hosts, summed' : SH.hostTitle(x.h.ip), v: x.h.series});
                    });
                    if (P.na && st.page === pages - 1) { rows.push({label: 'not attributed', kind: 'na', v: P.na}); }
                }
                const seen = {};
                rows.forEach(function (r) { let l = r.label; while (seen[l]) { l += ' '; } seen[l] = 1; r.key = l; });
                const cells = [];
                rows.forEach(function (r) {
                    for (let c = 0; c < B.cols; c++) {
                        const a = c * bs, b = Math.min(P.N, a + bs);
                        if (r.kind === 'state') {
                            let lim = 0, mx = 0;
                            for (let i = a; i < b; i++) { lim += P.lim[i] ? 1 : 0; mx = Math.max(mx, (P.bits[i] || 0) / (P.capRows[i] || P.cap || 1)); }
                            cells.push({x: c, y: r.key, v: lim / (b - a), near: mx, r: r, a: a, b: b, lim: lim});
                        } else {
                            cells.push({x: c, y: r.key, v: SH.avg(r.v, a, b) / (P.cap || 1), dr: r.d ? SH.avg(r.d, a, b) : 0, r: r, a: a, b: b});
                        }
                    }
                });
                const base = th.dark ? '#8fbcf2' : '#1f5fa8';
                const alpha = function (v) { return v <= 0.001 ? 0 : Math.min(1, (Math.log10(v) + 3) / 3); };
                const limPat = SH.limitHatch();
                el.querySelector('.sa-g5-leg').innerHTML = '<li><span class="sa-swatch" style="background:' + rgba(base, 0.27) + '" aria-hidden="true"></span>1 % of cap</li>' +
                    '<li><span class="sa-swatch" style="background:' + rgba(base, 0.6) + '" aria-hidden="true"></span>10 %</li><li><span class="sa-swatch" style="background:' + base +
                    '" aria-hidden="true"></span>100 %</li><li><span class="sa-swatch sa-sw-limit" aria-hidden="true"></span>limiting (state row)</li>' +
                    '<li><span class="sa-swatch" style="border:2px solid ' + th.drop + ';background:transparent" aria-hidden="true"></span>drops in that class</li>';
                const RH = SH.phone() ? 22 : 22;
                el.querySelector('.sa-g5-box').style.height = (rows.length * RH + 50) + 'px';
                const every = (function () {
                    const span = P.N * P.step;
                    return [60, 300, 600, 1800, 3600, 7200, 21600, 43200, 86400, 604800].find(function (s) { return span / s <= (SH.phone() ? 4 : 8); }) || 2592000;
                }());
                const cfg = {type: 'matrix', data: {datasets: [{data: cells,
                    backgroundColor: function (c) {
                        const d = c.raw; if (!d) { return 'transparent'; }
                        if (d.r.kind === 'state') { return d.v >= 0.5 ? limPat : d.near > 0.8 ? rgba(base, 0.35) : 'rgba(0,0,0,0)'; }
                        return d.v > 0.001 ? rgba(base, 0.06 + 0.94 * alpha(d.v)) : 'rgba(0,0,0,0)';
                    },
                    borderColor: function (c) { const d = c.raw; return d && d.dr > 0.5 ? th.drop : 'rgba(0,0,0,0)'; },
                    borderWidth: function (c) { const d = c.raw; return d && d.dr > 0.5 ? 1.5 : 0; },
                    width: function (c) { const a = c.chart.chartArea; return a ? Math.max(1, (a.right - a.left) / B.cols - 1) : 0; },
                    height: function (c) { const a = c.chart.chartArea; return a ? Math.max(1, (a.bottom - a.top) / rows.length - 2) : 0; }}]},
                plugins: [selPlugin],
                options: {animation: false, responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}, tooltip: {callbacks: {
                    title: function (it) { const d = it[0].raw; return (d.r.title || d.r.label) + ' · ' + SH.hhmm(P.t(d.a)) + '–' + SH.hhmm(P.t(d.b)); },
                    label: function (c) {
                        const d = c.raw;
                        if (d.r.kind === 'state') { return d.lim ? '▲ limiting in ' + d.lim + ' of ' + (d.b - d.a) + ' steps' : 'not limiting · peak ' + SH.pct(d.near) + ' of cap'; }
                        const out = ['avg ' + SA.rate(d.v * P.cap) + ' · ' + SH.pct(d.v) + ' of cap'];
                        if (d.dr > 0.05) { out.push('drops ' + SH.num(d.dr) + '/s in this class'); }
                        return out;
                    }}}},
                    scales: {x: {type: 'linear', min: -0.5, max: B.cols - 0.5, offset: false, grid: {display: false},
                        afterBuildTicks: function (sc) {
                            const t = [];
                            for (let c = 0; c < B.cols; c++) {
                                /* a label where a column starts a new round time block (every 10 min in an hour, ...) */
                                if (c > 0 && Math.floor(P.t(c * bs) / every) !== Math.floor(P.t((c - 1) * bs) / every)) { t.push({value: c}); }
                            }
                            sc.ticks = t;
                        },
                        ticks: {autoSkip: false, maxRotation: 0, color: SH.fg(), callback: function (v) { return SH.label(P.t(Math.round(v) * bs), every >= 86400 ? 600 : 60); }}},
                        /* the matrix's category axis puts its first label at the bottom: list the rows bottom-up
                           so the pipe state reads first, at the top (lab screenshot 2026-11-23) */
                        y: {type: 'category', labels: rows.map(function (r) { return r.key; }).reverse(), offset: true, grid: {display: false},
                            ticks: {color: SH.fg(), font: function (c) { const r = rows[rows.length - 1 - c.index]; return {size: SH.phone() ? 10 : 12, weight: r && r.kind !== 'host' ? '600' : '400'}; }}}}}};
                chart = SH.chart(canvas, cfg);
                canvas.setAttribute('aria-label', P.title + ': time grid, ' + rows.length + ' rows by ' + B.cols + ' columns of ' + SH.dur(bs * P.step) + '. ' + SH.verdict(M, P).history);
                const pg = el.querySelector('.sa-pager');
                pg.innerHTML = pages > 1 ? '<button type="button" class="btn btn-default btn-sm" data-p="-1"' + (st.page === 0 ? ' disabled' : '') + '>Previous</button> <span>Hosts ' +
                    (st.page * PAGE + 1) + '–' + Math.min(total, (st.page + 1) * PAGE) + ' of ' + SH.num(total) + ', busiest first</span> <button type="button" class="btn btn-default btn-sm" data-p="1"' +
                    (st.page >= pages - 1 ? ' disabled' : '') + '>Next</button>' : '';
                el.querySelector('.sa-g5-cov').textContent = 'Each column is ' + SH.dur(bs * P.step) + '. ' + cov.text + (opts.select ? ' Click or drag across the grid to pick a time.' : '');
            },
            setSel: function (s) { st.sel = s; if (chart) { chart.draw(); } },
            destroy: function () { SH.destroyCharts(el); el.innerHTML = ''; }
        };
        return api;
    }};
}());
