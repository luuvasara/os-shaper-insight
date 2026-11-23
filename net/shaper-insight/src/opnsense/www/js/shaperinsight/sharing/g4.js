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
 * G4 Flow ribbons: hosts flow into their CAKE class (from the priority assignment), each class flows to
 * Delivered or Dropped, and the cap's unused part is Spare. "Is the shaping doing its job?"
 * CAKE counts drops PER CLASS (tin), not per host: no ribbon runs from a host to Dropped, and the page says
 * so. A dropped flow is drawn as packet-equivalents (drops/s x 1,500 B), so it is visible next to the
 * delivered bits; the magnifier gives the drop rates per class.
 * Needs chartjs-chart-sankey (vendored, MIT), loaded only while this graph is on screen.
 * opts: {height}
 */
(function () {
    'use strict';
    const SH = window.SAsh;

    SH.graphs.g4 = {create: function (el, opts) {
        opts = opts || {};
        let win = 'now', M0 = null, P0 = null;
        el.innerHTML = '<div class="sa-g4-tools"><span id="' + el.id + '-wl">Average over</span> <div class="btn-group btn-group-sm" role="group" aria-labelledby="' + el.id + '-wl">' +
            '<button type="button" class="btn btn-default" data-w="now" aria-pressed="true">the newest step</button>' +
            '<button type="button" class="btn btn-default" data-w="win" aria-pressed="false">the shown window</button></div>' +
            ' <span class="sa-meta">The newest step shows what happens now; the window shows what usually happens.</span></div>' +
            '<p class="sa-meta sa-g4-note"><b>Drops are counted per class</b>: CAKE keeps drop counters per class (tin), not per host, so the ribbons run host → class → outcome and never from a host straight to Dropped.</p>' +
            '<div class="sa-g4-grid"><div><div class="sa-chart-box" style="height:' + (opts.height || (SH.phone() ? 420 : 440)) + 'px"><canvas id="' + el.id + '-c" role="img"></canvas></div></div>' +
            '<div class="sa-g4-mag"></div></div><p class="sa-meta sa-g4-cov"></p>';
        const canvas = el.querySelector('canvas');
        el.querySelector('.btn-group').addEventListener('click', function (e) {
            const b = e.target.closest('button[data-w]');
            if (!b) { return; }
            win = b.getAttribute('data-w');
            el.querySelectorAll('button[data-w]').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); });
            api.update(M0, P0);
        });
        const api = {
            update: function (M, P) {
                M0 = M; P0 = P;
                const th = SH.theme();
                const a = win === 'now' ? P.iNow : 0, b = win === 'now' ? P.iNow + 1 : P.N;
                const tins = P.tins.length > 1 ? P.tins : ['All traffic'];
                const tinRate = tins.map(function (_, k) { return P.tins.length > 1 ? SH.avg(P.tinBits[k], a, b) : SH.avg(P.bits, a, b); });
                const tinDrop = tins.map(function (_, k) { return P.tins.length > 1 ? SH.avg(P.tinDrops[k] || [], a, b) : SH.avg(P.drops || [], a, b); });
                const flows = [], labels = {}, prio = {}, column = {};
                const hs = SH.hostsOver(P, a, b).filter(function (h) { return !h.other && h.ip !== 'this firewall'; });
                const coloured = hs.filter(function (h) { return h.colour; }).slice(0, 8);
                const inTin = tins.map(function () { return 0; });
                coloured.forEach(function (h, k) {
                    const key = 'h' + k;
                    const t = Math.max(0, tins.indexOf(P.tins.length > 1 ? SH.classOf(h.ip).tin : 'All traffic'));
                    labels[key] = SH.cut(SH.hostName(h.ip), SH.phone() ? 16 : 24); prio[key] = k; column[key] = 0;
                    flows.push({from: key, to: 't' + t, flow: h.v / 1e6, colour: h.colour, ip: h.ip});
                    inTin[t] += h.v;
                });
                /* the rest of each class: other hosts and what pf did not attribute */
                labels.rest = 'other hosts and not attributed'; prio.rest = 20; column.rest = 0;
                tins.forEach(function (_, t) {
                    const rest = tinRate[t] - inTin[t];
                    if (rest > (P.cap || 1) * 0.002) { flows.push({from: 'rest', to: 't' + t, flow: rest / 1e6, rest: true}); }
                });
                let delivered = 0;
                labels.ok = 'Delivered'; labels.dm = 'Dropped (≈ drops × 1,500 B)'; labels.spare = 'Spare (unused)'; labels.cap = 'Capacity left';
                prio.ok = 0; prio.dm = 1; prio.spare = 2; prio.cap = 30;
                column.ok = 2; column.dm = 2; column.spare = 2; column.cap = 1;
                tins.forEach(function (name, t) {
                    labels['t' + t] = name; prio['t' + t] = t; column['t' + t] = 1;
                    if (tinRate[t] > 0) { flows.push({from: 't' + t, to: 'ok', flow: tinRate[t] / 1e6, out: true}); delivered += tinRate[t]; }
                    const lost = tinDrop[t] * 1500 * 8;
                    if (lost / 1e6 > 0.0005) { flows.push({from: 't' + t, to: 'dm', flow: lost / 1e6, lost: true, tin: t}); }
                });
                const spare = Math.max(0, (P.cap || 0) - delivered);
                if (spare > (P.cap || 1) * 0.002) { flows.push({from: 'cap', to: 'spare', flow: spare / 1e6, spare: true}); }
                const headPat = SH.headroom();
                const colour = function (f) {
                    if (!f) { return th.other; }
                    if (f.spare) { return headPat; }
                    if (f.lost) { return th.drop; }
                    if (f.rest || f.out) { return th.other + 'aa'; }
                    return f.colour + 'cc';
                };
                const ds = {data: flows, labels: labels, priority: prio, column: column, size: 'max', nodeWidth: 12, borderWidth: 0, nodePadding: 18,
                    color: SH.fg(), font: {size: SH.phone() ? 10 : 12}, colorMode: 'to',
                    /* the plugin colours a node with the last flow drawn through it: colorFrom is the node's own
                       colour (host colour, a neutral class, grey rest), colorTo the ribbon's and its outcome's */
                    colorFrom: function (c) {
                        const f = c.dataset.data[c.dataIndex];
                        return !f ? th.other : f.colour && !f.out && !f.lost ? f.colour : f.rest ? th.other : f.spare ? th.headInk : th.naInk;
                    },
                    colorTo: function (c) { return colour(c.dataset.data[c.dataIndex]); }};
                SH.chart(canvas, {type: 'sankey', data: {datasets: [ds]}, options: {animation: false, responsive: true, maintainAspectRatio: false,
                    layout: {padding: {left: 4, right: 8, top: 8, bottom: 8}},
                    plugins: {legend: {display: false}, tooltip: {callbacks: {
                        title: function (it) { const f = it[0].raw; return (f.ip ? SH.hostTitle(f.ip) : labels[f.from]) + ' → ' + labels[f.to]; },
                        label: function (c) {
                            const f = c.raw;
                            return f.lost ? SH.num(tinDrop[f.tin]) + ' drops/s, drawn as ≈ ' + SA.rate(f.flow * 1e6) : SA.rate(f.flow * 1e6) + ' · ' + SH.pct(f.flow * 1e6 / (P.cap || 1)) + ' of cap';
                        }}}}}});
                canvas.setAttribute('aria-label', P.title + ' flow: ' + flows.filter(function (f) { return !f.spare; }).map(function (f) {
                    return (f.ip ? SH.hostTitle(f.ip) : labels[f.from]) + ' to ' + labels[f.to] + ' ' + SA.rate(f.flow * 1e6);
                }).join('; '));
                /* the magnifier: drops are about 1 % of the bits, so they get their own scale */
                const all = tinDrop.reduce(function (s, x) { return s + x; }, 0);
                const mx = Math.max.apply(null, tinDrop.concat([0.001]));
                const bk = tins.indexOf('Bulk');
                el.querySelector('.sa-g4-mag').innerHTML = '<h4>Drops per class, magnified</h4><p class="sa-meta">' + (all > 0.05 ? SH.num(all) + ' drops/s ' +
                    (win === 'now' ? 'in the newest step' : 'on average') + (bk >= 0 ? '; ' + SH.pct(tinDrop[bk] / all) + ' in Bulk.' : '.') : 'No drops ' + (win === 'now' ? 'in the newest step.' : 'in this window.')) + '</p>' +
                    '<ul class="sa-toplist sa-g4-list" aria-label="Drops per class">' + tins.map(function (name, t) {
                        return '<li>' + (P.tins.length > 1 ? SH.tinSwatch(t) : '<span></span>') + '<span class="sa-tl-name">' + SA.esc(name) + '</span><span class="sa-tl-num">' +
                            SH.num(tinDrop[t]) + '/s</span><span class="sa-tl-num">' + (all > 0.05 ? SH.pct(tinDrop[t] / all) : '') + '</span><span class="sa-tl-bar" aria-hidden="true"><i style="width:' +
                            (tinDrop[t] / mx * 100).toFixed(1) + '%;background:' + th.drop + '"></i></span></li>';
                    }).join('') + '</ul>';
                el.querySelector('.sa-g4-cov').textContent = 'Hosts: the ' + coloured.length + ' coloured hosts by name; the rest of each class is one ribbon. Classes come from the priorities (a host without one is Best Effort).';
            },
            destroy: function () { SH.destroyCharts(el); el.innerHTML = ''; }
        };
        return api;
    }};
}());
