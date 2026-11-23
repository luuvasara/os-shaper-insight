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
 * G1 Ceiling river: the coloured hosts stacked under the cap (dashed), the rest grey, "not attributed"
 * hatched, the unused space under the cap dotted as headroom, the limited minutes as a red band.
 * Clicking a name hides that host and rescales the rest (a hog no longer flattens everyone else).
 * Uses Chart.js only (core's copy, on the page already).
 * opts: {height, range: function () -> [a, b] | null, select: function (a, b), legend: true}
 */
(function () {
    'use strict';
    const SH = window.SAsh;

    SH.graphs.g1 = {create: function (el, opts) {
        opts = opts || {};
        const hidden = {};
        let sel = null, M0 = null, P0 = null, chart = null;
        el.innerHTML = (opts.legend === false ? '' : '<div class="sa-g1-leg"></div>') +
            '<div class="sa-chart-box" style="height:' + (opts.height || 300) + 'px"><canvas id="' + el.id + '-c" role="img"></canvas></div>' +
            '<p class="sa-meta sa-g1-note"></p>';
        const canvas = el.querySelector('canvas');
        function range(P) { return (opts.range && opts.range()) || [0, P.N]; }
        /* L3: drag (or click) on the chart picks rows; the plugin draws the picked span */
        const selPlugin = {id: 'saSel', afterDatasetsDraw: function (c) {
            if (!sel || !P0) { return; }
            const r = range(P0), a = c.chartArea, x = c.scales.x, w = (a.right - a.left) / Math.max(1, r[1] - r[0]);
            const s = Math.max(sel[0], r[0]) - r[0], e = Math.min(sel[1], r[1]) - r[0];
            if (e <= s) { return; }
            c.ctx.save(); c.ctx.strokeStyle = SH.fg(); c.ctx.lineWidth = 2;
            c.ctx.strokeRect(x.getPixelForValue(s) - w / 2, a.top - 2, (e - s) * w, a.bottom - a.top + 4); c.ctx.restore();
        }};
        if (opts.select) {
            let down = null;
            const at = function (e) {
                const r = range(P0);
                return r[0] + Math.max(0, Math.min(r[1] - r[0] - 1, Math.round(chart.scales.x.getValueForPixel(e.offsetX))));
            };
            canvas.style.cursor = 'crosshair';
            canvas.addEventListener('pointerdown', function (e) { if (chart) { down = at(e); } });
            canvas.addEventListener('pointerup', function (e) {
                if (down === null || !chart) { return; }
                const b = at(e);
                sel = [Math.min(b, down), Math.max(b, down) + 1];
                down = null;
                chart.draw();
                opts.select(sel[0], sel[1]);
            });
        }
        if (opts.legend !== false) {
            el.querySelector('.sa-g1-leg').addEventListener('click', function (e) {
                const b = e.target.closest('button[data-h]');
                if (!b) { return; }
                const h = b.getAttribute('data-h');
                if (hidden[h]) { delete hidden[h]; } else { hidden[h] = true; }
                api.update(M0, P0);
                const again = el.querySelector('button[data-h="' + CSS.escape(h) + '"]');
                if (again) { again.focus(); }
            });
        }
        const api = {
            update: function (M, P) {
                M0 = M; P0 = P;
                const th = SH.theme();
                const r = range(P);
                const n = r[1] - r[0];
                const sl = function (a) { return (a || []).slice(r[0], r[1]).map(SH.mbit); };
                const any = Object.keys(hidden).length > 0;
                const sets = [];
                P.hosts.forEach(function (h) {
                    sets.push({label: SH.hostTitle(h.ip), data: sl(h.series), hidden: !!hidden[h.ip], fill: true, stack: 's',
                        backgroundColor: h.colour, borderColor: h.colour, borderWidth: 1, tension: 0});
                });
                if (P.other) {
                    sets.push({label: (P.otherInfo.seen ? P.otherInfo.seen + ' ' : '') + 'other hosts', data: sl(P.other), fill: true, stack: 's',
                        backgroundColor: th.other, borderColor: th.other, borderWidth: 1, tension: 0});
                }
                if (P.na) {
                    sets.push({label: 'not attributed to a host', data: sl(P.na), fill: true, stack: 's', backgroundColor: SH.hatch(),
                        borderColor: th.naInk, borderWidth: 1, tension: 0});
                }
                /* before the first host record (the 6 h ring, a reboot) only the pipe total exists */
                const early = P.bits.slice(r[0], r[1]).map(function (v, k) { return v === null || (P.na && P.covered(r[0] + k)) ? null : SH.mbit(v); });
                if (early.some(function (v) { return v !== null; })) {
                    sets.push({label: P.na ? 'pipe total, no per-host record' : 'pipe total', data: early, fill: true, stack: P.na ? 'early' : 's',
                        backgroundColor: th.other + '88', borderColor: SH.fg(), borderWidth: 1, borderDash: [2, 2], tension: 0});
                }
                if (!any && P.cap) {
                    sets.push({label: 'headroom', isHead: true, data: P.bits.slice(r[0], r[1]).map(function (v, k) {
                        return v === null ? null : SH.mbit(Math.max(0, (P.capRows[r[0] + k] || P.cap) - v));
                    }), fill: true, stack: 's', backgroundColor: SH.headroom(), borderColor: 'rgba(0,0,0,0)', borderWidth: 0, tension: 0});
                }
                if (P.cap) { sets.push(SH.capLine(n, P.capRows.slice(r[0], r[1]).map(SH.mbit), 'cap ' + SA.rate(P.cap))); }
                if (opts.legend !== false) {
                    el.querySelector('.sa-g1-leg').innerHTML = SH.legend(M, P, {toggle: true, hidden: hidden,
                        extra: '<li><span class="sa-swatch sa-sw-head" aria-hidden="true"></span>headroom (unused)</li>' +
                            '<li><span class="sa-swatch sa-sw-dash" aria-hidden="true"></span>cap ' + SA.esc(SA.rate(P.cap)) + '</li>' +
                            '<li><span class="sa-swatch sa-sw-limit" aria-hidden="true"></span>limiting</li>'});
                }
                const o = SH.opts(SH.axes(P, 'Mbit/s', true, r));
                o.layout = {padding: {top: 26}};
                o.plugins.tooltip = {filter: function (it) { return it.raw !== null && it.raw > 0.0005 && !it.dataset.isCap; },
                    itemSort: function (a, b) { return b.raw - a.raw; },
                    callbacks: {title: function (it) { return it[0].label + (P.lim[r[0] + it[0].dataIndex] ? '  ▲ limiting' : ''); },
                        label: function (c) {
                            return SH.cut(c.dataset.label, 32) + ': ' + SA.rate(c.raw * 1e6) + (c.dataset.isHead ? ' unused' : ' (' + SH.pct(c.raw * 1e6 / (P.cap || 1)) + ' of cap)');
                        }}};
                chart = SH.chart(canvas, {type: 'line', data: {labels: SH.labels(P, r), datasets: sets}, options: o,
                    plugins: [SH.limitPlugin(true), selPlugin], sa: {P: P, r: r}});
                canvas.setAttribute('aria-label', P.title + ': rate per host, stacked under the cap of ' + SA.rate(P.cap) + '. ' + SH.verdict(M, P).history);
                el.querySelector('.sa-g1-note').textContent = any ? 'Hidden hosts are left out and the rest rescale; the cap and headroom come back when every host is shown.' :
                    (opts.select ? 'Drag across the chart to pick a time.' : '');
            },
            setSel: function (s) { sel = s; if (chart) { chart.draw(); } },
            destroy: function () { SH.destroyCharts(el); el.innerHTML = ''; }
        };
        return api;
    }};
}());
