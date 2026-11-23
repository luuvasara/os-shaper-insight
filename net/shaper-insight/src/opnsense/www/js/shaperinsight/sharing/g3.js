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
 * G3 Capacity map: the whole rectangle is the pipe's cap; each tile is a host's rate in the newest step,
 * grouped by its CAKE class (from the priority assignment), with the unused part as a dotted headroom
 * tile. No axis to read: "who has the line right now, and is it full?". Tiles keep their order between
 * updates. Needs chartjs-chart-treemap (vendored, MIT), loaded only while this graph is on screen.
 * opts: {height, table: true}
 */
(function () {
    'use strict';
    const SH = window.SAsh;

    function ink(hex) {
        const n = parseInt(String(hex).slice(1), 16);
        if (isNaN(n)) { return SH.fg(); }
        const c = [n >> 16, n >> 8 & 255, n & 255].map(function (v) { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] > 0.3 ? '#111111' : '#ffffff';
    }

    SH.graphs.g3 = {create: function (el, opts) {
        opts = opts || {};
        el.innerHTML = '<p class="sa-g3-status"></p><ul class="sa-legend sa-g3-leg"></ul>' +
            '<div class="sa-chart-box" style="height:' + (opts.height || (SH.phone() ? 360 : 440)) + 'px"><canvas id="' + el.id + '-c" role="img"></canvas></div>' +
            '<p class="sa-meta sa-g3-tiny"></p>' + (opts.table === false ? '' : '<details class="sa-g3-det"><summary>Show the map as a table</summary><div class="sa-g3-tbl" id="' + el.id + '-t"></div></details>');
        const canvas = el.querySelector('canvas');
        return {
            update: function (M, P) {
                const th = SH.theme();
                const i = P.iNow;
                const hs = SH.hostsAt(P, i);
                const groups = [];
                const order = P.tins.length > 1 ? P.tins.slice() : ['All traffic'];
                const data = [];
                const small = {};
                const floor = (P.cap || 1) * 0.004;
                hs.forEach(function (h) {
                    if (h.ip === 'this firewall') { return; }
                    const g = h.other ? 'Other hosts' : (P.tins.length > 1 ? SH.classOf(h.ip).tin : 'All traffic');
                    const grp = order.indexOf(g) >= 0 || g === 'Other hosts' ? g : order[0];
                    if (!h.other && h.v < floor) { small[grp] = (small[grp] || {v: 0, n: 0}); small[grp].v += h.v; small[grp].n++; return; }
                    data.push({grp: grp, label: h.other ? 'other hosts (summed)' : SH.hostName(h.ip), v: h.v / 1e6, ip: h.ip, colour: h.colour || th.other, other: h.other});
                });
                Object.keys(small).forEach(function (g) {
                    data.push({grp: g, label: small[g].n + ' small host' + (small[g].n === 1 ? '' : 's'), v: small[g].v / 1e6, colour: th.other, other: true});
                });
                const total = i >= 0 ? (P.bits[i] || 0) : 0;
                const named = data.reduce(function (s, d) { return s + d.v * 1e6; }, 0);
                const na = Math.max(0, total - named);
                if (na > floor) { data.push({grp: 'Not attributed', label: 'not attributed', v: na / 1e6, na: true}); }
                const head = Math.max(0, (P.capRows[i] || P.cap) - total);
                if (head > floor) { data.push({grp: 'Headroom', label: 'headroom (unused)', v: head / 1e6, head: true}); }
                /* stable order: classes as the pipe lists them, then other, not attributed, headroom */
                const rank = order.concat(['Other hosts', 'Not attributed', 'Headroom']);
                data.sort(function (a, b) { return rank.indexOf(a.grp) - rank.indexOf(b.grp) || (a.ip || '~').localeCompare(b.ip || '~'); });
                rank.forEach(function (g) { if (data.some(function (d) { return d.grp === g; })) { groups.push(g); } });
                const tinDrops = function (g) { const k = P.tins.indexOf(g); return k >= 0 && P.tinDrops[k] ? (P.tinDrops[k][i] || 0) : 0; };
                const caption = function (g) {
                    if (rank.indexOf(g) >= order.length) { return g; }
                    const r = data.filter(function (d) { return d.grp === g; }).reduce(function (s, d) { return s + d.v; }, 0) * 1e6;
                    const dr = tinDrops(g);
                    return g + ' · ' + SA.rate(r) + (dr >= 0.5 ? ' · ▲ ' + SH.num(dr) + ' drops/s' : '');
                };
                el.querySelector('.sa-g3-status').innerHTML = (P.busy ? SA.badge('warn', 'Limiting: no headroom') : SA.badge('ok', 'Not limiting')) +
                    ' <b>' + SA.esc(SA.rate(total)) + '</b> of ' + SA.esc(SA.rate(P.cap)) + ' (' + SH.pct(total / (P.cap || 1)) + ') in the newest ' + P.step + ' s' +
                    (P.drops ? ' · drops ' + SH.num(P.drops[i] || 0) + '/s' : '');
                el.querySelector('.sa-g3-leg').innerHTML = P.hosts.map(function (h) {
                    return '<li>' + SA.swatch(h.colour) + SH.nameHtml(h.ip) + '</li>';
                }).join('') + '<li>' + SA.swatch(th.other) + 'other hosts</li><li><span class="sa-swatch sa-hatch" aria-hidden="true"></span>not attributed</li>' +
                    '<li><span class="sa-swatch sa-sw-head" aria-hidden="true"></span>headroom</li><li class="sa-meta">Framed groups are the CAKE classes, from the priorities.</li>';
                const naPat = SH.hatch(), headPat = SH.headroom();
                const cfg = {type: 'treemap', data: {datasets: [{tree: data, key: 'v', groups: ['grp', 'label'], spacing: 1, borderWidth: 0, unsorted: true,
                    backgroundColor: function (c) {
                        if (c.type !== 'data') { return 'transparent'; }
                        const r = c.raw, d = r._data;
                        if (r.l === 0) { return d.grp === 'Headroom' ? headPat : d.grp === 'Not attributed' ? 'transparent' : th.na; }
                        const it = d.children && d.children[0];
                        return !it ? th.other : it.head ? headPat : it.na ? naPat : it.colour;
                    },
                    borderColor: function (c) { return c.raw && c.raw.l === 0 && rank.indexOf(c.raw._data.grp) < order.length ? SH.fg() : 'transparent'; },
                    borderWidth: function (c) { return c.raw && c.raw.l === 0 ? 2 : 0; },
                    captions: {display: true, color: SH.fg(), font: {size: 12, weight: '600'}, formatter: function (c) { return caption(c.raw._data.grp); }},
                    labels: {display: true, overflow: 'hidden',
                        color: function (c) { const it = c.raw._data.children && c.raw._data.children[0]; return !it || it.head || it.na ? SH.fg() : ink(it.colour); },
                        font: [{size: SH.phone() ? 11 : 13, weight: '600'}, {size: 11}],
                        formatter: function (c) {
                            const it = c.raw._data.children && c.raw._data.children[0];
                            return it ? [SH.cut(it.label, 22), SA.rate(c.raw.v * 1e6) + ' · ' + SH.pct(c.raw.v * 1e6 / (P.cap || 1))] : '';
                        }}}]},
                options: {animation: false, responsive: true, maintainAspectRatio: false,
                    plugins: {legend: {display: false}, tooltip: {callbacks: {
                        title: function (it) { const r = it[0].raw, d = r._data; return r.l === 0 ? caption(d.grp) : (d.children[0].ip ? SH.hostTitle(d.children[0].ip) : d.children[0].label); },
                        label: function (c) { return SA.rate(c.raw.v * 1e6) + ' · ' + SH.pct(c.raw.v * 1e6 / (P.cap || 1)) + ' of cap'; }}}}}};
                SH.chart(canvas, cfg);
                canvas.setAttribute('aria-label', P.title + ' capacity map, newest step: ' + data.map(function (d) { return d.label + ' ' + SA.rate(d.v * 1e6); }).join(', '));
                const tiny = P.tins.filter(function (g) { return !data.some(function (d) { return d.grp === g; }); })
                    .map(function (g) { const k = P.tins.indexOf(g); return g + ' ' + SA.rate(P.tinBits[k] ? (P.tinBits[k][i] || 0) : 0); });
                el.querySelector('.sa-g3-tiny').textContent = (tiny.length ? 'Too small to draw at this scale: ' + tiny.join(', ') + '. ' : '') +
                    (P.all ? 'Every host of the newest step has its own tile (small ones are merged per class).' :
                        'The coloured hosts have their own tiles; the other hosts are one grey tile.');
                if (el.querySelector('.sa-g3-tbl')) {
                    SA.table('#' + el.id + '-t', [
                        {title: 'Class', field: 'grp'},
                        {title: 'Host', field: 'name', type: 'html', swatch: true, sortField: 'sortName'},
                        {title: 'Rate', field: 'rate', type: 'num', fmt: function (v) { return SA.rate(v); }},
                        {title: 'Share of cap', field: 'share', type: 'num', fmt: function (v) { return SH.pct(v); }}
                    ], data.map(function (d) {
                        return {grp: d.grp, name: d.ip ? SH.nameHtml(d.ip) : SA.esc(d.label), sortName: d.ip ? (SH.extra.names[d.ip] || d.ip) : '~' + d.label,
                            colour: d.head ? '' : d.na ? 'hatch' : d.colour, rate: d.v * 1e6, share: d.v * 1e6 / (P.cap || 1)};
                    }), {sort: ['rate', 'desc'], label: P.title + ': the capacity map as a table'});
                }
            },
            destroy: function () { SH.destroyCharts(el); el.innerHTML = ''; }
        };
    }};
}());
