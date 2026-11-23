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
 * G2 Horizon rows: one thin row per series on one time axis (pipe total, limiting state, drops, the
 * classes, then one row per host). Each row folds its values into 3 bands of one size, darker = more,
 * so every host keeps its own baseline; the band size is printed at the row's right end.
 * Plain SVG, no library. Hosts: every stored host when the sampler keeps them all, else the busiest
 * per sample + the rest (the note under the rows says which). 25 host rows a page, a search above 8.
 * opts: {select: function (a, b)}
 */
(function () {
    'use strict';
    const SH = window.SAsh;
    const NS = 'http://www.w3.org/2000/svg';
    const BANDS = [0.01, 0.03, 0.1, 1 / 3];
    const PAGE = 25;
    const RAMP = {light: ['#c6dbef', '#6baed6', '#1f5fa8'], dark: ['#1d3557', '#3f74b8', '#8fbcf2']};
    const DROPS = {light: ['#f8c9b4', '#e0794c', '#a3401a'], dark: ['#5c2610', '#b5552b', '#f2a27e']};

    SH.graphs.g2 = {create: function (el, opts) {
        opts = opts || {};
        const st = {page: 0, q: '', sel: null};
        let M0 = null, P0 = null, geo = null, timer = null;
        el.innerHTML = '<div class="sa-find sa-hz-find" hidden><label for="' + el.id + '-q">Find a host</label> ' +
            '<input type="search" id="' + el.id + '-q" class="form-control sa-inline-select" autocomplete="off" placeholder="name or IP"> ' +
            '<span class="sa-meta sa-hz-count" aria-live="polite"></span></div>' +
            '<p class="sa-meta sa-hz-key"></p><div class="sa-hz-box"></div><p class="sa-meta sa-hz-read" aria-live="off">&nbsp;</p>' +
            '<div class="sa-pager"></div><p class="sa-meta sa-hz-cov"></p>';
        const box = el.querySelector('.sa-hz-box');
        const q = el.querySelector('input');
        q.addEventListener('input', function () {
            clearTimeout(timer);
            timer = setTimeout(function () { st.q = q.value.trim().toLowerCase(); st.page = 0; draw(); }, 150);
        });
        el.querySelector('.sa-pager').addEventListener('click', function (e) {
            const b = e.target.closest('button[data-p]');
            if (b) { st.page += Number(b.getAttribute('data-p')); draw(); }
        });
        function onResize() { clearTimeout(timer); timer = setTimeout(draw, 150); }
        window.addEventListener('resize', onResize);

        function rowsOf(M, P) {
            const out = [{kind: 'pipe', label: P.short + ' total', sub: SA.rate(P.bits[P.iNow] || 0) + ' of ' + SA.rate(P.cap), v: P.bits, band: 1 / 3},
                {kind: 'state', label: 'Limiting', sub: SH.dur(P.periods.reduce(function (s, x) { return s + (x[1] - x[0]) * P.step; }, 0)) + ' in window'}];
            if (P.drops) {
                out.push({kind: 'drops', label: 'Drops + marks', sub: SH.num((P.drops[P.iNow] || 0) + ((P.marks || [])[P.iNow] || 0)) + ' /s now',
                    v: P.drops.map(function (x, i) { return (x || 0) + ((P.marks || [])[i] || 0); })});
            }
            P.tins.slice().map(function (name, k) { return [name, k]; }).reverse().forEach(function (x) {
                out.push({kind: 'tin', label: x[0], k: x[1], sub: SA.rate(P.tinBits[x[1]][P.iNow] || 0), v: P.tinBits[x[1]]});
            });
            out.push({kind: 'sep'});
            const cov = SH.coverage(M, P);
            let hs = [];
            if (cov.mode !== 'none') {
                const src = P.all || P.hosts.concat(P.other ? [{ip: '', series: P.other}] : []);
                hs = src.map(function (h) { return {h: h, tot: SH.sum(h.series, 0, P.N)}; }).filter(function (x) { return x.tot > 0; });
                hs.sort(function (a, b) { return (a.h.ip === '') - (b.h.ip === '') || b.tot - a.tot; });
                if (st.q) {
                    hs = hs.filter(function (x) { return x.h.ip !== '' && (x.h.ip + ' ' + (SH.extra.names[x.h.ip] || '')).toLowerCase().indexOf(st.q) >= 0; });
                }
            }
            const pages = Math.max(1, Math.ceil(hs.length / PAGE));
            st.page = Math.max(0, Math.min(st.page, pages - 1));
            hs.slice(st.page * PAGE, (st.page + 1) * PAGE).forEach(function (x) {
                const h = x.h;
                out.push({kind: 'host', ip: h.ip, colour: h.ip === '' ? SH.theme().other : SH.colour(P.n, h.ip),
                    label: h.ip === '' ? 'other hosts (summed)' : SH.hostName(h.ip), title: h.ip === '' ? 'other hosts, summed' : SH.hostTitle(h.ip),
                    sub: SA.rate(h.series[P.iNow] || 0), v: h.series});
            });
            if (P.na && !st.q && st.page === pages - 1) {
                out.push({kind: 'host', na: true, label: 'not attributed', title: 'not attributed to a host', sub: SA.rate(P.na[P.iNow] || 0), v: P.na});
            }
            return {rows: out, total: hs.length, pages: pages, cov: cov};
        }

        function svg(tag, attrs, parent) {
            const e = document.createElementNS(NS, tag);
            Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
            if (parent) { parent.appendChild(e); }
            return e;
        }

        function draw() {
            const M = M0, P = P0;
            if (!P) { return; }
            const th = SH.theme();
            const R = rowsOf(M, P);
            const W = box.clientWidth || 800;
            const narrow = W < 600;
            const LW = narrow ? 112 : 260, RW = narrow ? 40 : 60, RH = narrow ? 26 : 24, GAP = 3;
            const PW = Math.max(60, W - LW - RW - 8);
            const N = P.N;
            const X = function (i) { return N > 1 ? i / (N - 1) * PW : 0; };
            const ramp = RAMP[th.dark ? 'dark' : 'light'];
            const root = svg('svg', {width: W, class: 'sa-hz', role: 'img'});
            const defs = svg('defs', {}, root);
            const pat = svg('pattern', {id: el.id + '-lim', width: 6, height: 6, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(135)'}, defs);
            svg('rect', {width: 6, height: 6, fill: th.head}, pat);
            svg('line', {x1: 0, x2: 0, y1: 0, y2: 6, stroke: th.limit, 'stroke-width': 3}, pat);
            const pna = svg('pattern', {id: el.id + '-na', width: 4, height: 4, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)'}, defs);
            svg('rect', {width: 4, height: 4, fill: th.na}, pna);
            svg('line', {x1: 0, x2: 0, y1: 0, y2: 4, stroke: th.naInk, 'stroke-width': 1.5}, pna);
            /* time axis: a few labels on round times */
            const span = N * P.step;
            const every = [60, 300, 600, 1800, 3600, 7200, 21600, 43200, 86400, 604800].find(function (s) { return span / s <= (narrow ? 4 : 8); }) || 2592000;
            const ax = svg('g', {transform: 'translate(' + LW + ',14)'}, root);
            const ticks = [];
            for (let i = 0; i < N; i++) {
                if (Math.floor(P.t(i) / every) !== Math.floor((P.t(i) - P.step) / every)) { ticks.push(i); }
            }
            ticks.forEach(function (i) {
                const t = svg('text', {x: X(i), y: 0, 'text-anchor': 'middle', fill: SH.fg(), 'font-size': 11}, ax);
                t.textContent = SH.label(P.t(i), every >= 86400 ? 600 : 60);
            });
            let y = 22;
            R.rows.forEach(function (r) {
                if (r.kind === 'sep') { y += 8; return; }
                r.y = y;
                const g = svg('g', {transform: 'translate(0,' + y + ')'}, root);
                svg('rect', {x: LW, width: PW, height: RH, fill: th.dark ? '#1b1c20' : '#f4f4f1'}, g);
                const sw = r.kind === 'host' ? (r.na ? 'url(#' + el.id + '-na)' : r.colour || 'none') : null;
                if (sw) { svg('rect', {x: 0, y: RH / 2 - 6, width: 12, height: 12, rx: 2, fill: sw === 'none' ? 'transparent' : sw, stroke: sw === 'none' ? th.naInk : 'none'}, g); }
                const lab = svg('text', {x: sw ? 18 : 0, y: narrow ? 11 : RH / 2 + 4, fill: SH.fg(), 'font-size': narrow ? 11 : 13,
                    'font-weight': r.kind === 'host' ? 400 : 600, direction: 'auto', 'unicode-bidi': 'plaintext'}, g);
                lab.textContent = SH.cut(r.label, narrow ? 14 : 20);
                svg('title', {}, lab).textContent = r.title || r.label;
                const sub = svg('text', {x: narrow ? (sw ? 18 : 0) : LW - 8, y: narrow ? RH - 2 : RH / 2 + 4, 'text-anchor': narrow ? 'start' : 'end',
                    fill: SH.fg(), 'font-size': narrow ? 10 : 12}, g);
                sub.textContent = r.sub;
                const pg = svg('g', {transform: 'translate(' + LW + ',0)'}, g);
                if (r.kind === 'state') {
                    P.periods.forEach(function (pr) {
                        svg('rect', {x: X(pr[0]), width: Math.max(2, X(Math.min(pr[1], N) - 1) - X(pr[0]) + PW / N), height: RH,
                            fill: 'url(#' + el.id + '-lim)', stroke: th.limit}, pg);
                    });
                    y += RH + GAP;
                    return;
                }
                const vals = r.v.map(function (v, i) {
                    if (v === null || v === undefined) { return 0; }
                    return r.kind === 'drops' ? v : v / (P.capRows[i] || P.cap || 1);
                });
                const mx = Math.max.apply(null, vals.concat([0]));
                let band, cols = ramp;
                if (r.kind === 'drops') {
                    band = mx > 0 ? Math.pow(10, Math.ceil(Math.log10(mx / 3))) : 1;
                    cols = DROPS[th.dark ? 'dark' : 'light'];
                    r.scale = SH.num(band) + '/s';
                } else {
                    band = r.band || BANDS.find(function (b) { return mx <= b * 3; }) || 1 / 3;
                    r.scale = band === 1 / 3 ? '33 %' : Math.round(band * 100) + ' %';
                }
                for (let b = 0; b < 3; b++) {
                    let d = 'M0,' + RH;
                    vals.forEach(function (v, i) {
                        const h = Math.max(0, Math.min(band, v - b * band)) / band * RH;
                        d += 'L' + X(i).toFixed(1) + ',' + (RH - h).toFixed(1);
                    });
                    d += 'L' + X(N - 1).toFixed(1) + ',' + RH + 'Z';
                    svg('path', {d: d, fill: cols[b]}, pg);
                }
                const sc = svg('text', {x: LW + PW + 6, y: RH / 2 + 4, fill: SH.fg(), 'font-size': narrow ? 10 : 11}, g);
                sc.textContent = '▮ ' + r.scale;
                r.vals = vals;
                y += RH + GAP;
            });
            root.setAttribute('height', y + 4);
            root.setAttribute('aria-label', P.title + ': horizon rows, ' + R.rows.filter(function (r) { return r.kind === 'host'; }).length +
                ' host rows on one time axis. ' + SH.verdict(M, P).history);
            /* the picked span (L3) and the crosshair */
            const selR = svg('rect', {y: 20, height: y - 20, fill: 'none', stroke: SH.fg(), 'stroke-width': 2, visibility: 'hidden', 'pointer-events': 'none'}, root);
            const cross = svg('line', {y1: 20, y2: y, stroke: SH.fg(), opacity: 0, 'pointer-events': 'none'}, root);
            const hit = svg('rect', {x: LW, y: 20, width: PW, height: y - 20, fill: 'transparent'}, root);
            geo = {LW: LW, PW: PW, N: N, selR: selR};
            const idx = function (e) {
                const b = root.getBoundingClientRect();
                return Math.max(0, Math.min(N - 1, Math.round((e.clientX - b.left - LW) / PW * (N - 1))));
            };
            hit.addEventListener('mousemove', function (e) {
                const i = idx(e);
                cross.setAttribute('x1', LW + X(i)); cross.setAttribute('x2', LW + X(i)); cross.setAttribute('opacity', 0.6);
                const b = root.getBoundingClientRect();
                const my = e.clientY - b.top;
                const r = R.rows.find(function (x) { return x.y !== undefined && my >= x.y && my < x.y + RH; });
                let txt = SH.label(P.t(i), P.step) + (P.lim[i] ? ' ▲ limiting' : '') + ' · ' + P.short + ' ' + SA.rate(P.bits[i] || 0);
                if (r && r.v) { txt += ' · ' + (r.title || r.label) + ': ' + (r.kind === 'drops' ? SH.num(r.v[i] || 0) + '/s' : SA.rate(r.v[i] || 0)); }
                el.querySelector('.sa-hz-read').textContent = txt;
            });
            hit.addEventListener('mouseleave', function () { cross.setAttribute('opacity', 0); });
            if (opts.select) {
                let down = null;
                hit.style.cursor = 'crosshair';
                hit.addEventListener('pointerdown', function (e) { down = idx(e); });
                hit.addEventListener('pointerup', function (e) {
                    if (down === null) { return; }
                    const b = idx(e);
                    st.sel = [Math.min(b, down), Math.max(b, down) + 1];
                    down = null;
                    showSel();
                    opts.select(st.sel[0], st.sel[1]);
                });
            }
            box.replaceChildren(root);
            showSel();
            el.querySelector('.sa-hz-key').innerHTML = 'Each row folds its values into 3 bands of one size, darker = more: ' +
                ramp.map(function (c) { return '<span class="sa-swatch" style="background:' + c + '" aria-hidden="true"></span>'; }).join('') +
                ' (share of the cap; drops per second in orange). The band size is printed at the right end, so a small host keeps its shape.';
            el.querySelector('.sa-hz-find').hidden = R.total <= 8 && !st.q;
            el.querySelector('.sa-hz-count').textContent = st.q ? R.total + ' hosts match' : '';
            const pager = el.querySelector('.sa-pager');
            pager.innerHTML = R.pages > 1 ? '<button type="button" class="btn btn-default btn-sm" data-p="-1"' + (st.page === 0 ? ' disabled' : '') + '>Previous</button> ' +
                '<span>Hosts ' + (st.page * PAGE + 1) + '–' + Math.min(R.total, (st.page + 1) * PAGE) + ' of ' + SH.num(R.total) + ', busiest first</span> ' +
                '<button type="button" class="btn btn-default btn-sm" data-p="1"' + (st.page >= R.pages - 1 ? ' disabled' : '') + '>Next</button>' : '';
            el.querySelector('.sa-hz-cov').textContent = R.cov.text + (R.cov.mode === 'top' ? ' The rows show what was stored.' : '');
        }
        function showSel() {
            if (!geo) { return; }
            if (!st.sel) { geo.selR.setAttribute('visibility', 'hidden'); return; }
            const X = function (i) { return geo.N > 1 ? i / (geo.N - 1) * geo.PW : 0; };
            geo.selR.setAttribute('x', geo.LW + X(st.sel[0]));
            geo.selR.setAttribute('width', Math.max(2, X(st.sel[1] - 1) - X(st.sel[0]) + geo.PW / geo.N));
            geo.selR.setAttribute('visibility', 'visible');
        }
        return {
            update: function (M, P) { M0 = M; P0 = P; draw(); },
            setSel: function (s) { st.sel = s; showSel(); },
            destroy: function () { window.removeEventListener('resize', onResize); el.innerHTML = ''; }
        };
    }};
}());
