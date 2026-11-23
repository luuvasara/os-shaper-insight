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
 * Sharing page, data layer: one model built from one sampler/history answer (plus names, MACs and
 * priorities), shared by every layout and graph. Also the parts every layout reuses: palette and
 * textures, the hosts table, the drops strip, the per-class chart and table, the top list and the
 * verdict sentences. Rules from the host simulation (TESTING.md): at most 8 coloured hosts, colour bound to the
 * host (slots), the rest one grey "N other hosts", "not attributed" hatched, names in <bdi>, IPs never cut.
 */
(function () {
    'use strict';
    const SH = window.SAsh = window.SAsh || {};

    /* ------------------------------------------------------------------ palette and textures */
    /* eight series colours, a light and a dark step each, checked on OPNsense's light and dark surfaces
       (host simulation); no grey among them: grey is "other hosts" (solid) and "not attributed" (hatched) */
    const STEPS = {
        light: {series: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'],
            other: '#b9b8b0', na: '#deddd6', naInk: '#8a8983', head: '#f1f0ea', headInk: '#c9c8c0', limit: '#c62828', drop: '#b3261e'},
        dark: {series: ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#0ca30c', '#9085e9', '#e66767'],
            other: '#6e6d68', na: '#50504d', naInk: '#9a9993', head: '#26272b', headInk: '#4a4b50', limit: '#ef6c6c', drop: '#f28b82'}
    };
    SH.theme = function () {
        const m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(getComputedStyle(document.body).backgroundColor || '');
        const lum = m ? (0.2126 * m[1] + 0.7152 * m[2] + 0.0722 * m[3]) / 255 : 1;
        return Object.assign({dark: lum < 0.5}, STEPS[lum < 0.5 ? 'dark' : 'light']);
    };
    SH.fg = function () { return SA.cssVar('--chart-js-font-color', getComputedStyle(document.body).color); };
    SH.grid = function () { return SA.cssVar('--chart-js-border-color', 'rgba(128,128,128,0.25)'); };
    SH.phone = function () { return window.matchMedia && window.matchMedia('(max-width: 767px)').matches; };
    function pattern(bg, ink, draw) {
        const c = document.createElement('canvas');
        c.width = c.height = 8;
        const x = c.getContext('2d');
        x.fillStyle = bg; x.fillRect(0, 0, 8, 8);
        x.strokeStyle = x.fillStyle = ink; x.lineWidth = 1.5; x.beginPath();
        draw(x);
        x.stroke();
        return x.createPattern(c, 'repeat');
    }
    /* "not attributed": the neutral with a 45 degree hatch, so it differs from "other hosts" without colour */
    SH.hatch = function () {
        const t = SH.theme();
        return pattern(t.na, t.naInk, function (x) { x.moveTo(0, 8); x.lineTo(8, 0); x.moveTo(-2, 2); x.lineTo(2, -2); x.moveTo(6, 10); x.lineTo(10, 6); });
    };
    /* headroom (unused capacity under the cap): light dots, so it reads as empty, not as a series */
    SH.headroom = function () {
        const t = SH.theme();
        return pattern(t.head, t.headInk, function (x) { x.arc(2, 2, 0.9, 0, 2 * Math.PI); x.arc(6, 6, 0.9, 0, 2 * Math.PI); x.fill(); });
    };
    /* the minutes the shaper was limiting: a 135 degree hatch in the limit colour */
    SH.limitHatch = function () {
        const t = SH.theme();
        return pattern('rgba(0,0,0,0)', t.limit + '55', function (x) { x.moveTo(0, 0); x.lineTo(8, 8); x.moveTo(-2, 6); x.lineTo(2, 10); x.moveTo(6, -2); x.lineTo(10, 2); });
    };
    /* the CAKE tins: one fixed texture each in the text colour (no host colour also means a tin) */
    SH.tinPattern = function (k) {
        return pattern(SH.theme().na, SH.fg(), function (x) {
            if (k === 0) { x.moveTo(4, 0); x.lineTo(4, 8); }
            else if (k === 1) { x.moveTo(0, 4); x.lineTo(8, 4); }
            else if (k === 2) { x.arc(4, 4, 1.6, 0, 2 * Math.PI); x.fill(); }
            else { x.moveTo(4, 0); x.lineTo(4, 8); x.moveTo(0, 4); x.lineTo(8, 4); }
        });
    };
    SH.tinSwatch = function (k) { return '<span class="sa-swatch sa-tin-' + (k % 4) + '" aria-hidden="true"></span>'; };
    SH.cut = function (text, n) { text = String(text); return text.length > n ? text.slice(0, n - 1) + '…' : text; };
    /* SA.num keeps three decimals; rates per second read better with one below 10 and none above */
    SH.num = function (x) { x = Number(x) || 0; return x < 10 ? Number(x.toFixed(1)).toLocaleString() : Math.round(x).toLocaleString(); };
    SH.pct = function (x) { return !(x > 0) ? '0 %' : x < 0.01 ? '<1 %' : Math.round(x * 100) + ' %'; };
    SH.mbit = function (v) { return v === null || v === undefined ? null : Number((v / 1e6).toFixed(3)); };
    SH.hhmm = function (t) {
        return new Date(t * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
    };
    /* a span of rows as text: minutes, or seconds when both ends fall in one minute */
    SH.span = function (P, a, b) {
        const t0 = P.t(a), t1 = P.t(Math.min(b, P.N - 1));
        if (b >= P.N) { return 'since ' + SH.hhmm(t0); }
        if (Math.floor(t0 / 60) === Math.floor(t1 / 60)) {
            const f = {hour: '2-digit', minute: '2-digit', second: '2-digit'};
            return new Date(t0 * 1000).toLocaleTimeString([], f) + '–' + new Date(t1 * 1000).toLocaleTimeString([], f);
        }
        return SH.hhmm(t0) + '–' + SH.hhmm(t1);
    };
    SH.label = function (t, step) {
        const d = new Date(t * 1000);
        return step >= 600 ? d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) :
            d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: step < 60 ? '2-digit' : undefined});
    };
    SH.dur = function (s) {
        const m = Math.round(s / 60);
        if (m < 1) { return Math.round(s) + ' s'; }
        const h = Math.floor(m / 60);
        return (h ? h + ' h ' : '') + (m % 60 || !h ? (m % 60) + ' min' : '').trim();
    };
    /* how a host's number was obtained (sampler hosts_method / newest rows, 1.0.k): shares of its bytes per
       method -> a short label and a reason with an example (TESTING.md, per-host counting) */
    const HOW = {
        exact: ['exact', 'Counted where the packet leaves the firewall, after CAKE: what the host really got or sent. Ex.: a download that arrived at 28.9 Mbit/s and was cut to 24.9 by CAKE shows 24.9.'],
        checked: ['counted, checked', 'pf counted it before CAKE (a download with no LAN-side count), but the pipe’s own total shows nothing to correct, within 2 %. Ex.: the firewall’s own TCP download read 11.8 Mbit/s where about 11.6 arrived.'],
        fair: ['fair-share estimate', 'No LAN-side count (the firewall’s own traffic, or a connection whose two states did not match): pf counted it before CAKE, so it is set to its fair share of what the pipe sent, inside its CAKE class. Ex.: the firewall offered 10 Mbit/s, 3 were left: it shows 3.'],
        scaled: ['scaled', 'As above, but the CAKE class of these hosts is unknown (a host marks its own traffic, or a priority by port), so their counts are scaled down by one factor to what the pipe sent; a light host is cut as much as a heavy one.'],
        counted: ['as pf counted', 'This pipe has no CAKE byte counters to check against, so the number is pf’s own count; on download it can include packets the shaper dropped.']
    };
    SH.how = function (shares) {
        if (!shares) { return {text: '–', title: '', key: ''}; }
        const keys = Object.keys(shares).filter(function (k) { return HOW[k] && shares[k] > 0; });
        if (!keys.length) { return {text: '–', title: '', key: ''}; }
        const rest = keys.filter(function (k) { return k !== 'exact'; }).sort(function (a, b) { return shares[b] - shares[a]; });
        const est = rest.reduce(function (a, k) { return a + shares[k]; }, 0);
        if (!rest.length || est < 0.05) { return {text: HOW.exact[0], title: HOW.exact[1], key: 'exact'}; }
        const k = rest[0];
        return {text: HOW[k][0] + (est < 0.95 ? ' (' + SH.pct(est) + ')' : ''), key: k,
            title: (est < 0.95 ? SH.pct(1 - est) + ' exact, the rest: ' : '') + HOW[k][1]};
    };
    SH.howHtml = function (shares) {
        const h = SH.how(shares);
        return h.title ? '<span class="sa-how sa-how-' + h.key + '" title="' + SA.esc(h.title) + '">' + SA.esc(h.text) + '</span>' : '–';
    };
    SH.list = function (xs) { return xs.length < 2 ? xs.join('') : xs.slice(0, -1).join(', ') + ' and ' + xs[xs.length - 1]; };

    /* ------------------------------------------------------------------ colour slots */
    /* a host keeps its slot while it is in a pipe's coloured set; a host that comes back gets its old
       slot if it is free, else the lowest free one; colours never cycle (host simulation) */
    const slotsOf = {};
    const lastSlot = {};
    function assignSlots(pipe, top) {
        const cur = slotsOf[pipe] = slotsOf[pipe] || {};
        Object.keys(cur).forEach(function (h) { if (top.indexOf(h) < 0) { delete cur[h]; } });
        top.forEach(function (h) {
            if (cur[h] !== undefined) { return; }
            const used = Object.values(cur);
            let k = lastSlot[h] !== undefined && used.indexOf(lastSlot[h]) < 0 ? lastSlot[h] : -1;
            if (k < 0) { k = 0; while (used.indexOf(k) >= 0) { k++; } }
            cur[h] = k;
            lastSlot[h] = k;
        });
    }
    SH.colour = function (pipe, h) {
        const k = (slotsOf[pipe] || {})[h];
        return k === undefined ? null : SH.theme().series[k % 8];
    };

    /* ------------------------------------------------------------------ names, MACs, classes */
    SH.extra = {names: {}, hostInfo: {}, prio: null, aliases: {}};
    SH.hostName = function (h) {
        if (h === '') { return 'all other hosts'; }
        if (h === 'this firewall') { return 'this firewall'; }
        return SH.extra.names[h] || h;
    };
    SH.hostTitle = function (h) {
        const n = SH.extra.names[h];
        return n ? n + ' (' + h + ')' : h;
    };
    SH.nameHtml = function (h) {
        const n = SH.extra.names[h];
        return '<span class="sa-name" title="' + SA.esc(SH.hostTitle(h)) + '">' + (n ? '<bdi>' + SA.esc(n) + '</bdi>' :
            (h === 'this firewall' ? 'this firewall' : '<bdi class="' + (h.indexOf(':') >= 0 ? 'sa-v6' : 'sa-nw') + '">' + SA.esc(h) + '</bdi>')) + '</span>';
    };
    function inNet(ip, cidr) {
        const parts = String(cidr).split('/');
        const a = SAip.key(ip);
        const n = SAip.key(parts[0]);
        if (a.family !== n.family || a.family === 2) { return false; }
        const bits = parts.length > 1 ? parseInt(parts[1], 10) : (a.family === 0 ? 32 : 128);
        const width = a.family === 0 ? 8 : 16;
        let left = bits;
        for (let i = 0; i < a.parts.length && left > 0; i++) {
            const take = Math.min(width, left);
            const mask = ((1 << take) - 1) << (width - take);
            if ((a.parts[i] & mask) !== (n.parts[i] & mask)) { return false; }
            left -= take;
        }
        return true;
    }
    const MAC_RE = /^([0-9a-f]{2}:){5}[0-9a-f]{2}$/i;
    /* the priority a host belongs to, from the resolved members (networks, exact addresses, or a MAC
       target matched through the host's MAC): {tin, label (html), cls} */
    SH.classOf = function (ip) {
        const prio = SH.extra.prio;
        const mac = ((SH.extra.hostInfo[ip] || {}).mac || '').toLowerCase();
        if (prio && ip) {
            for (const c of prio.classes || []) {
                for (const m of c.members || []) {
                    const hit = (m.addresses || []).some(function (x) { return inNet(ip, x); }) ||
                        (mac && MAC_RE.test(m.target || '') && m.target.toLowerCase() === mac);
                    if (hit) {
                        const label = c['class'] === 'background' ? 'Background' : 'Interactive';
                        const al = SH.extra.aliases[m.target];
                        return {tin: c.tin, cls: c['class'], label: SA.esc(label + ' (' + c.tin + ') · ') + (al ? SA.ref('alias', m.target, al) : SA.esc(m.target))};
                    }
                }
            }
        }
        return {tin: 'Best Effort', cls: '', label: 'Best Effort'};
    };

    /* ------------------------------------------------------------------ the model */
    /* per row: is the shaper the bottleneck? The rule of SAverdict.limiting, applied to one row */
    function limitingRow(bits, drops, cap) {
        if (bits === null || bits === undefined || !cap) { return false; }
        return bits >= 0.9 * cap || ((drops || 0) >= 1 && bits >= 0.5 * cap);
    }
    function sum(arr, a, b) { let s = 0; for (let i = a; i < b; i++) { s += arr[i] || 0; } return s; }
    SH.sum = sum;
    SH.avg = function (arr, a, b) { return b > a ? sum(arr, a, b) / (b - a) : 0; };

    SH.build = function (d) {
        const th = SH.theme();
        const M = {d: d, now: d.now, window: d.window, step: d.step, shown: d.hosts_shown || 8,
            hostsSince: d.hosts_since || 0, hostsKeep: d.hosts_keep_s || 21600, store: d.hosts_store || {mode: 'top', top: 16, pipes: {}},
            pipes: [], byId: {}, hostsOk: d.window <= (d.hosts_keep_s || 21600), hasAll: !!d.hosts_all};
        Object.keys(d.pipes || {}).sort(function (a, b) { return Number(a) - Number(b); }).forEach(function (n) {
            const p = d.pipes[n];
            const s = p.series || {};
            const N = (s.bytes || []).length;
            const cap = p.payload_cap || p.bandwidth || 0;
            const hosts = (d.hosts || {})[n] || [];
            const named = hosts.filter(function (h) { return h[0] !== ''; });
            assignSlots(n, named.map(function (h) { return h[0]; }));
            const P = {n: n, raw: p, title: 'Pipe ' + n + (p.description ? ' “' + p.description + '”' : ''),
                short: p.description || ('pipe ' + n), where: p.where || [], start: p.start, step: p.step || d.step, N: N,
                t: function (i) { return p.start + i * (p.step || d.step); },
                bits: s.bytes || [], drops: s.drops || null, marks: s.marks || null, delay: s.delay || null, backlog: s.backlog || null,
                cap: cap, bandwidth: p.bandwidth, overhead: p.overhead, cake: !!p.cake, diffserv: p.diffserv,
                capRows: (p.cap_rows || []).map(function (v) { return v || cap; }),
                tins: p.tins || [], tinBits: [], tinDrops: [], latestTins: p.latest_tins || [], bulk: p.bulk, aboveCap: p.above_cap || 0,
                hosts: [], other: null, otherInfo: (d.hosts_other || {})[n] || {seen: 0, sending: 0}, na: null, offered: (d.hosts_offered || {})[n] || [],
                method: (d.hosts_method || {})[n] || {},
                all: null, store: (M.store.pipes || {})[n] || null};
            (P.tins || []).forEach(function (_, k) {
                P.tinBits.push((p.tin_series || {})['t' + k + 'b'] || []);
                P.tinDrops.push((p.tin_series || {})['t' + k + 'd'] || []);
            });
            P.covered = function (i) { return P.t(i) >= M.hostsSince; };
            named.forEach(function (h) {
                P.hosts.push({ip: h[0], series: h[1], colour: SH.colour(n, h[0]), slot: (slotsOf[n] || {})[h[0]]});
            });
            P.hosts.sort(function (a, b) { return a.slot - b.slot; });
            const oth = hosts.find(function (h) { return h[0] === ''; });
            P.other = oth ? oth[1] : null;
            if (hosts.length) {
                P.na = P.bits.map(function (v, i) {
                    if (v === null || v === undefined || !P.covered(i)) { return null; }
                    const tot = hosts.reduce(function (a, h) { return a + (h[1][i] || 0); }, 0);
                    return Math.max(0, v - tot);
                });
            }
            if (d.hosts_all && d.hosts_all[n]) {
                P.all = d.hosts_all[n].map(function (h) { return {ip: h[0], series: h[1], colour: h[0] ? SH.colour(n, h[0]) : th.other}; });
            }
            /* the newest row with a reading, and the state now */
            let i = N - 1;
            while (i > 0 && (P.bits[i] === null || P.bits[i] === undefined)) { i--; }
            P.iNow = i;
            P.lim = P.bits.map(function (v, k) { return limitingRow(v, P.drops ? P.drops[k] : 0, P.capRows[k] || cap); });
            P.busy = SAverdict.limiting(P.bits.slice(0, i + 1), P.drops, cap);
            P.periods = [];
            let a = -1;
            for (let k = 0; k <= N; k++) {
                const on = k < N && P.lim[k];
                if (on && a < 0) { a = k; }
                if (!on && a >= 0) {
                    /* a quiet minute between two limited stretches does not end a period (bursty traffic
                       would otherwise list dozens of one-minute periods) */
                    const gap = Math.max(1, Math.round(60 / P.step));
                    let more = false;
                    for (let j = k + 1; j < N && j <= k + gap; j++) { if (P.lim[j]) { more = true; break; } }
                    if (more) { continue; }
                    P.periods.push([a, k]); a = -1;
                }
            }
            M.pipes.push(P);
            M.byId[n] = P;
        });
        return M;
    };

    /* the hosts at row i, busiest first: every stored host when the page has them, else the coloured
       ones plus "other"; each {ip, v, colour, other} */
    SH.hostsAt = function (P, i) {
        const th = SH.theme();
        const src = P.all || P.hosts.concat(P.other ? [{ip: '', series: P.other}] : []);
        return src.map(function (h) {
            return {ip: h.ip, v: (h.series[i] || 0), colour: h.ip === '' ? th.other : (SH.colour(P.n, h.ip) || null), other: h.ip === ''};
        }).filter(function (h) { return h.v > 0; }).sort(function (a, b) { return b.v - a.v; });
    };
    /* the average over rows [a, b) per host, the same sources */
    SH.hostsOver = function (P, a, b) {
        const th = SH.theme();
        const src = P.all || P.hosts.concat(P.other ? [{ip: '', series: P.other}] : []);
        return src.map(function (h) {
            return {ip: h.ip, v: SH.avg(h.series, a, b), colour: h.ip === '' ? th.other : (SH.colour(P.n, h.ip) || null), other: h.ip === ''};
        }).filter(function (h) { return h.v > 0; }).sort(function (a2, b2) { return b2.v - a2.v; });
    };
    /* what the per-host graphs can show: "every" (all hosts, complete window), or "top" (the busiest
       16 per sample + the rest), and why; the label every limited graph prints */
    SH.coverage = function (M, P) {
        const st = P.store || {};
        if (!M.hostsOk) {
            return {mode: 'none', text: 'Hosts are kept for 6 hours; this window shows the pipe and its classes only.'};
        }
        if (M.store.mode === 'every' && st.complete !== false) {
            return {mode: 'every', text: 'Every host is stored (“Store every host’s history” is on).'};
        }
        if (st.over) {
            return {mode: 'top', text: 'Limited to the busiest ' + M.store.top + ' hosts per sample + the rest: this pipe had ' +
                SH.num(st.over) + ' hosts, over the ceiling of ' + SH.num(M.store.ceiling) + '.'};
        }
        return {mode: 'top', text: M.store.mode === 'every' ? 'Partly limited to the busiest ' + M.store.top + ' hosts per sample + the rest: part of the window was recorded before “Store every host’s history” was switched on.' :
            'Limited to the busiest ' + M.store.top + ' hosts per sample + the rest: “Store every host’s history” is off.'};
    };

    /* ------------------------------------------------------------------ verdict sentences */
    SH.verdict = function (M, P) {
        const out = {limiting: P.busy, lines: []};
        const i = P.iNow;
        const tot = i >= 0 ? (P.bits[i] || 0) : 0;
        out.title = P.short.charAt(0).toUpperCase() + P.short.slice(1) + (P.busy ? ' is limiting now' : ' is not limiting');
        const hs = SH.hostsAt(P, i).filter(function (h) { return !h.other && h.ip !== 'this firewall'; });
        if (i < 0 || !P.cap) {
            out.who = 'Nothing recorded yet for this pipe.';
        } else if (tot < P.cap * 0.01 || !hs.length) {
            out.who = 'The line is idle: ' + SA.rate(tot) + ' of ' + SA.rate(P.cap) + '.';
        } else {
            let acc = 0, k = 0;
            while (k < hs.length && acc < tot * 0.6 && k < 3) { acc += hs[k].v; k++; }
            const names = hs.slice(0, k).map(function (h) { return '<bdi>' + SA.esc(SH.hostName(h.ip)) + '</bdi>'; });
            out.who = (k === 1 ? names[0] + ' takes ' : k + ' hosts (' + SH.list(names) + ') take ') + SH.pct(acc / P.cap) + ' of the ' +
                SA.rate(P.cap) + ' line' + (P.busy ? '' : '; ' + SH.pct(1 - tot / P.cap) + ' is spare') + '.';
        }
        out.lines.push(out.who);
        /* drops per class: now, else over the window (CAKE counts drops per tin, not per host) */
        if (P.tins.length > 1 && P.tinDrops.length) {
            const nowAll = P.tinDrops.reduce(function (s, a) { return s + (a[i] || 0); }, 0);
            const useNow = nowAll >= 0.05;
            const td = P.tinDrops.map(function (a) { return useNow ? (a[i] || 0) : sum(a, 0, P.N); });
            const all = td.reduce(function (s, x) { return s + x; }, 0);
            const bk = P.tins.indexOf('Bulk');
            const hi = ['Video', 'Voice'].map(function (n) { return P.tins.indexOf(n); }).filter(function (k) { return k >= 0; });
            const vv = hi.reduce(function (s, k) { return s + td[k]; }, 0);
            if (all > 0.05) {
                out.drops = (useNow ? 'Drops now: ' : 'Drops in the shown window: ') + (bk >= 0 ? SH.pct(td[bk] / all) + ' in Bulk' : '') +
                    (hi.length ? (vv / all < 0.001 ? '; Voice and Video had none.' : '; Voice and Video ' + SH.pct(vv / all) + '.') : '.') +
                    ' (CAKE counts drops per class, not per host.)';
                out.dropsOk = bk >= 0 && td[bk] / all >= 0.8 && vv / all < 0.02;
            } else {
                out.drops = 'No drops in the shown window.';
                out.dropsOk = true;
            }
            out.lines.push(out.drops);
        }
        const limS = P.periods.reduce(function (s, x) { return s + (x[1] - x[0]) * P.step; }, 0);
        out.history = limS ? 'In the shown window: limited for ' + SH.dur(limS) + ' in ' + (P.periods.length === 1 ? 'one period: ' : P.periods.length + ' periods: ') +
            SH.list(P.periods.slice(-4).map(function (x) { return SH.span(P, x[0], x[1]); })) + (P.periods.length > 4 ? ' (the last 4 listed)' : '') + '.' : 'In the shown window: never limited.';
        out.lines.push(out.history);
        return out;
    };

    /* ------------------------------------------------------------------ charts kept between polls */
    const charts = {};
    SH.chart = function (canvas, cfg) {
        const id = canvas.id;
        /* cfg.sa = {P, r}: what the page's own plugins draw from; set on the chart so a chart kept between
           polls draws the newest data (Chart.js keeps the plugins it was created with) */
        if (charts[id] && charts[id].canvas === canvas) {
            charts[id].$sa = cfg.sa;
            charts[id].data = cfg.data;
            charts[id].options = cfg.options;
            charts[id].update('none');
            return charts[id];
        }
        if (charts[id]) { charts[id].destroy(); }
        charts[id] = new Chart(canvas, cfg);
        charts[id].$sa = cfg.sa;
        charts[id].draw();
        return charts[id];
    };
    SH.destroyCharts = function (root) {
        Object.keys(charts).forEach(function (k) {
            if (!root || root.contains(charts[k].canvas) || !document.body.contains(charts[k].canvas)) { charts[k].destroy(); delete charts[k]; }
        });
    };
    SH.axes = function (P, yTitle, stacked, range) {
        const r = range || [0, P.N];
        return {
            x: {ticks: {color: SH.fg(), maxTicksLimit: SH.phone() ? 4 : 8, maxRotation: 0, autoSkipPadding: 24}, grid: {color: SH.grid()}},
            y: {beginAtZero: true, stacked: !!stacked, title: {display: !SH.phone(), text: yTitle, color: SH.fg()},
                ticks: {color: SH.fg(), maxTicksLimit: 6}, grid: {color: SH.grid()}},
            _r: r
        };
    };
    SH.labels = function (P, range) {
        const r = range || [0, P.N];
        const out = [];
        for (let i = r[0]; i < r[1]; i++) { out.push(SH.label(P.t(i), P.step)); }
        return out;
    };
    SH.opts = function (scales) {
        delete scales._r;
        return {animation: false, responsive: true, maintainAspectRatio: false, interaction: {mode: 'index', intersect: false},
            plugins: {legend: {display: false}}, scales: scales, elements: {point: {radius: 0}}};
    };
    SH.capLine = function (n, value, text) {
        return {type: 'line', label: text, data: Array.from({length: n}, function (_, i) { return Array.isArray(value) ? value[i] : value; }),
            borderColor: SH.fg(), borderDash: [6, 4], borderWidth: 1.5, pointRadius: 0, fill: false, stack: 'cap', order: -1, isCap: true};
    };
    /* the limited rows: a faint hatch under the chart and a band above it (needs layout.padding.top) */
    SH.limitPlugin = function (band) {
        return {id: 'saLimit', beforeDatasetsDraw: function (chart) {
            if (!chart.$sa) { return; }
            const P = chart.$sa.P, r = chart.$sa.r;
            const a = chart.chartArea, g = chart.ctx, x = chart.scales.x;
            const n = r[1] - r[0];
            if (!n) { return; }
            const w = (a.right - a.left) / n;
            g.save();
            P.periods.forEach(function (pr) {
                const s = Math.max(pr[0], r[0]), e = Math.min(pr[1], r[1]);
                if (e <= s) { return; }
                const x0 = x.getPixelForValue(s - r[0]) - w / 2, x1 = x.getPixelForValue(e - 1 - r[0]) + w / 2;
                g.fillStyle = SH.limitHatch();
                g.fillRect(x0, a.top, x1 - x0, a.bottom - a.top);
                if (band) {
                    g.fillStyle = SH.theme().limit;
                    g.fillRect(x0, a.top - 12, Math.max(2, x1 - x0), 7);
                }
            });
            if (band) {
                g.fillStyle = SH.fg(); g.font = '12px ' + Chart.defaults.font.family; g.textBaseline = 'bottom';
                g.fillText(P.periods.length ? '▲ limiting (red band)' : 'never limiting in this window', a.left, a.top - 14);
            }
            g.restore();
        }};
    };

    /* ------------------------------------------------------------------ small parts */
    SH.stateHtml = function (P) {
        const i = P.iNow;
        if (i < 0) { return SA.badge('info', 'No reading yet'); }
        const rate = P.bits[i] || 0, d = P.drops ? (P.drops[i] || 0) : 0;
        return P.busy ? SA.badge('warn', 'Limiting now') + ' ' + SA.esc(SA.rate(rate) + ' of ' + SA.rate(P.cap) + (d > 0 ? ', ' + d.toFixed(1) + ' drops/s' : '') +
            ' in the last ' + P.step + ' s. The shaper is the bottleneck: hosts that want more share what is there.') :
            SA.badge('ok', 'Idle') + ' ' + SA.esc(SA.rate(rate) + ' of ' + SA.rate(P.cap) + ' in the last ' + P.step +
            ' s. The shaper is not limiting anything: every host gets what it asks for.');
    };
    SH.capText = function (P) {
        return P.cake && P.overhead ? 'Configured ' + SA.rate(P.bandwidth) + '; CAKE counts ' + P.overhead +
            ' bytes of framing per packet, so the IP packets that fit are at most ' + SA.rate(P.cap) + ' (' + SA.rate(P.bandwidth) +
            ' × 1500 / ' + (1500 + P.overhead) + '). The dashed line is that cap.' : 'Configured ' + SA.rate(P.bandwidth) + '. The dashed line is that rate.';
    };
    /* the pipe now as a big number and a bar that ends at the configured rate, with the cap marked */
    SH.readout = function (P) {
        const i = P.iNow, tot = i >= 0 ? (P.bits[i] || 0) : 0;
        const range = P.bandwidth || P.cap || 1;
        return '<section class="sa-readout" aria-label="' + SA.esc(P.title) + ' now"><div class="sa-ro-head"><b>' + SA.esc(P.short) + '</b> ' +
            (P.busy ? SA.badge('warn', 'Limiting now') : SA.badge('ok', 'Not limiting')) + '</div>' +
            '<div class="sa-ro-big">' + SA.esc(SA.rate(tot)) + ' <small>of ' + SA.esc(SA.rate(P.cap)) + '</small></div>' +
            '<div class="sa-bullet" role="img" aria-label="' + SA.esc(SA.rate(tot) + ' of ' + SA.rate(P.cap)) + '"><i style="width:' +
            Math.min(100, tot / range * 100).toFixed(1) + '%"></i><b style="left:' + Math.min(100, P.cap / range * 100).toFixed(1) + '%"></b></div>' +
            '<p class="sa-meta">' + SH.pct(tot / (P.cap || 1)) + ' of the cap · ' + (P.drops ? SH.num(P.drops[i] || 0) + ' drops/s · ' : '') +
            'the bar ends at the configured ' + SA.esc(SA.rate(P.bandwidth)) + ', the mark is the cap</p></section>';
    };
    /* the newest row as a list: coloured hosts, the rest, not attributed; bars are shares of the cap */
    SH.topList = function (M, P, n, range) {
        const th = SH.theme();
        const a = range ? range[0] : P.iNow, b = range ? range[1] : P.iNow + 1;
        const every = SH.hostsOver(P, a, b);
        const hs = every.filter(function (h) { return !h.other; });
        const shown = hs.slice(0, n);
        /* the grey row: the summed rest of the history plus the hosts beyond the first n */
        const otherV = every.filter(function (h) { return h.other; }).reduce(function (s, h) { return s + h.v; }, 0) +
            hs.slice(n).reduce(function (s, h) { return s + h.v; }, 0);
        const na = P.na ? SH.avg(P.na, a, b) : 0;
        const li = function (sw, name, v, col) {
            return '<li>' + sw + '<span class="sa-tl-name">' + name + '</span><span class="sa-tl-num">' + SA.esc(SA.rate(v)) + '</span><span class="sa-tl-num">' +
                SH.pct(v / (P.cap || 1)) + '</span><span class="sa-tl-bar" aria-hidden="true"><i style="width:' + Math.min(100, v / (P.cap || 1) * 100).toFixed(1) +
                '%;background:' + col + '"></i></span></li>';
        };
        if (!shown.length && !na) {
            return '<p class="sa-meta">No host traffic ' + (range ? 'in this period.' : 'in the newest step.') + '</p>';
        }
        return '<ul class="sa-toplist" aria-label="' + SA.esc(P.short + ': busiest hosts') + '">' + shown.map(function (h) {
            return li(h.colour ? SA.swatch(h.colour) : '<span class="sa-swatch sa-sw-none" aria-hidden="true"></span>', SH.nameHtml(h.ip), h.v, h.colour || th.other);
        }).join('') + (otherV > 0 ? li(SA.swatch(th.other), (P.all ? SH.num(hs.length - shown.length) + ' ' : P.otherInfo.seen ?
            SH.num(P.otherInfo.seen + Math.max(0, hs.length - n)) + ' ' : '') + 'other hosts', otherV, th.other) : '') +
            (na > 0 ? li('<span class="sa-swatch sa-hatch" aria-hidden="true"></span>', 'not attributed', na, th.naInk) : '') + '</ul>';
    };

    /* the HTML legend above a host chart: Chart.js drops entries that do not fit (host simulation) */
    SH.legend = function (M, P, opts) {
        opts = opts || {};
        const th = SH.theme();
        const items = P.hosts.map(function (h) {
            const inner = SA.swatch(h.colour) + '<span class="sa-name" title="' + SA.esc(SH.hostTitle(h.ip)) + '"><bdi>' + SA.esc(SH.hostName(h.ip)) + '</bdi></span>';
            return '<li>' + (opts.toggle ? '<button type="button" class="sa-leg-btn" data-h="' + SA.esc(h.ip) + '" aria-pressed="' + !(opts.hidden || {})[h.ip] +
                '" title="Show or hide ' + SA.esc(SH.hostTitle(h.ip)) + ' in the chart; the rest rescale">' + inner + '</button>' : inner) + '</li>';
        });
        if (P.other) { items.push('<li>' + SA.swatch(th.other) + (P.otherInfo.seen ? SH.num(P.otherInfo.seen) + ' ' : '') + 'other hosts</li>'); }
        if (P.na) { items.push('<li><span class="sa-swatch sa-hatch" aria-hidden="true"></span>not attributed to a host</li>'); }
        if (opts.extra) { items.push(opts.extra); }
        return '<ul class="sa-legend" aria-label="' + SA.esc(P.short) + ': chart key">' + items.join('') + '</ul>';
    };

    /* the drops strip: drops and ECN marks per second, CAKE's average wait */
    SH.dropsStrip = function (el, P, range) {
        if (!P.drops) {
            el.innerHTML = '<p class="sa-meta">This pipe does not run CAKE, so there are no drop or wait counters to show.</p>';
            return;
        }
        const r = range || [0, P.N];
        if (!el.querySelector('canvas')) {
            el.innerHTML = '<div class="sa-chart-box sa-strip"><canvas id="' + el.id + '-c" role="img"></canvas></div>' +
                '<p class="sa-meta">A drop and an ECN mark are the same signal: slow down. CAKE marks when both ends use ECN and drops otherwise, so drops in a busy class are how it works, not a fault.</p>';
        }
        const th = SH.theme();
        const sl = function (a) { return a.slice(r[0], r[1]).map(function (v) { return v === null ? null : Number(v.toFixed(2)); }); };
        const o = SH.opts(SH.axes(P, '/s', false, r));
        o.scales.y1 = {beginAtZero: true, position: 'right', title: {display: !SH.phone(), text: 'ms', color: SH.fg()}, ticks: {color: SH.fg(), maxTicksLimit: 4}, grid: {drawOnChartArea: false}};
        o.plugins.legend = {display: true, position: 'top', align: 'end', labels: {color: SH.fg(), boxWidth: 12}};
        const c = el.querySelector('canvas');
        SH.chart(c, {type: 'bar', data: {labels: SH.labels(P, r), datasets: [
            {label: 'drops/s', data: sl(P.drops), backgroundColor: th.drop, barPercentage: 1, categoryPercentage: 1, yAxisID: 'y'},
            {label: 'ECN marks/s', data: sl(P.marks || []), backgroundColor: th.other, barPercentage: 1, categoryPercentage: 1, yAxisID: 'y'},
            {type: 'line', label: 'average wait (ms)', data: sl(P.delay || []), borderColor: SH.fg(), borderWidth: 1.5, pointRadius: 0, yAxisID: 'y1', borderDash: [2, 2]}
        ]}, options: o, plugins: [SH.limitPlugin(false)], sa: {P: P, r: r}});
        c.setAttribute('aria-label', P.title + ': drops and ECN marks per second, and the average wait in CAKE');
    };

    /* the per-class part: stacked tin rates with the Bulk threshold, the newest step per class */
    SH.tinsPart = function (el, P, range) {
        const tins = P.tins;
        if (tins.length < 2) {
            el.innerHTML = '<p class="sa-meta">' + (P.cake ? 'This pipe has one class (besteffort), so there is no per-class view.' : 'Per-class counters exist only for CAKE pipes.') + '</p>';
            return;
        }
        const r = range || [0, P.N];
        if (!el.querySelector('canvas')) {
            el.innerHTML = '<p class="sa-meta sa-tinlead"></p><ul class="sa-legend sa-tinleg"></ul><div class="sa-chart-box"><canvas id="' + el.id + '-c" role="img"></canvas></div>' +
                '<div id="' + el.id + '-t"></div>';
        }
        const sets = tins.map(function (name, k) {
            return {label: name, data: P.tinBits[k].slice(r[0], r[1]).map(SH.mbit), fill: true, stack: 'tins', backgroundColor: SH.tinPattern(k),
                borderColor: SH.fg(), borderWidth: 1, tension: 0};
        });
        const bulk = tins.indexOf('Bulk');
        const bi = P.bulk;
        if (bulk >= 0 && (P.diffserv === 1 || P.diffserv === 2) && P.cap) {
            sets.push(SH.capLine(r[1] - r[0], SH.mbit(P.cap / 16), 'Bulk threshold ' + SA.rate(P.cap / 16)));
        }
        el.querySelector('.sa-tinleg').innerHTML = tins.map(function (name, k) { return '<li>' + SH.tinSwatch(k) + SA.esc(name) + '</li>'; }).join('') +
            (bulk >= 0 ? '<li><span class="sa-swatch sa-sw-dash" aria-hidden="true"></span>Bulk threshold (1/16 of the cap)</li>' : '');
        el.querySelector('.sa-tinlead').innerHTML = bi && bi.text ? SA.badge(bi.state === 'yielding' ? 'info' : bi.state === 'besteffort' ? 'warn' : 'ok',
            bi.state === 'yielding' ? 'Yielding' : bi.state === 'free' ? 'Free line' : bi.state === 'idle' ? 'Idle' : bi.state === 'besteffort' ? 'Background has no effect here' : 'Background') + ' ' +
            SA.esc(bi.text) + (bi.floor_text ? ' ' + SA.esc(bi.floor_text) : '') : '';
        const c = el.querySelector('canvas');
        const o = SH.opts(SH.axes(P, 'Mbit/s', true, r));
        o.plugins.tooltip = {filter: function (it) { return it.raw > 0; }, itemSort: function (a, b) { return b.raw - a.raw; }};
        SH.chart(c, {type: 'line', data: {labels: SH.labels(P, r), datasets: sets}, options: o, plugins: [SH.limitPlugin(false)], sa: {P: P, r: r}});
        c.setAttribute('aria-label', P.title + ': rate per CAKE tin, stacked');
        const lt = P.latestTins || [];
        SA.table('#' + el.id + '-t', [
            {title: 'Class', field: 'name', swatch: true},
            {title: 'Rate', field: 'rate', type: 'num', fmt: function (v) { return SA.rate(v); }},
            {title: 'Drops/s', field: 'drops', type: 'num', minWidth: 76, fmt: function (v) { return v.toFixed(1); }},
            {title: 'ECN marks/s', field: 'marks', type: 'num', minWidth: 76, fmt: function (v) { return v.toFixed(1); }},
            {title: 'Waiting (packets)', field: 'backlog', type: 'num', wide: true, fmt: function (v) { return SH.num(v); }}
        ], lt.length === tins.length ? tins.map(function (name, k) {
            const x = lt[k];
            return {colour: 'tin-' + k, name: name, rate: x[0] * 8 / 10, drops: x[1] / 10, marks: x[2] / 10, backlog: x[3]};
        }) : [], {sort: ['rate', 'desc'], label: P.title + ': per class, newest 10 s', empty: 'No per-class reading yet: it appears after two samples.'});
    };

    /* ------------------------------------------------------------------ the hosts table (1.0.i, unchanged rules) */
    const tstate = {};
    function hostRow(n, ip, v, total, how) {
        const info = SH.extra.hostInfo[ip] || {};
        const fw = ip === 'this firewall';
        const nm = fw ? 'this firewall' : (SH.extra.names[ip] || '');
        const shared = (info.shared || []).length;
        /* the phone hides the "How counted" column: an estimate is said in the line under the name instead */
        const hw = SH.how(how);
        const est = hw.key && hw.key !== 'exact' ? '<span class="sa-how sa-how-' + hw.key + '" title="' + SA.esc(hw.title) + '">' + SA.esc(hw.text) + '</span>' : '';
        const maker = info.vendor ? SA.esc(info.vendor) : (info.random ?
            '<span class="sa-muted" title="Locally administered MAC: a phone or laptop with private Wi-Fi addresses, a virtual machine or a container. The IEEE list names no maker for it.">private (random) MAC</span>' : '');
        return {colour: SH.colour(n, ip), ip: fw ? '' : ip, rate: v, share: total ? Math.round(1000 * v / total) / 10 : null,
            sortName: nm, find: [nm, ip, info.mac || '', info.vendor || ''].join(' ').toLowerCase(),
            name: '<div class="sa-hostcell">' + (nm ? '<span class="sa-name" title="' + SA.esc(nm + (fw ? '' : ' (' + ip + ')')) + '"><bdi>' + SA.esc(nm) + '</bdi></span>' :
                '<span class="sa-muted">no name</span>') +
                '<span class="sa-sub">' + (fw ? '' : '<span class="' + (ip.indexOf(':') >= 0 ? 'sa-v6' : 'sa-nw') + '">' + SA.esc(ip) + '</span>') +
                (info.vendor ? ' · ' + SA.esc(info.vendor) : '') + (est ? (fw ? '' : ' · ') + est : '') + '</span></div>',
            mac: info.mac ? SA.esc(info.mac) + (shared ? ' <span class="sa-muted" title="' + SA.esc([ip].concat(info.shared).join(', ')) +
                '">shared ×' + (shared + 1) + '</span>' : '') : '',
            vendor: maker, cls: SH.classOf(ip).label, how: SH.howHtml(how)};
    }
    function drawTable(n, box) {
        const st = tstate[n];
        const P = st.P;
        const flat = st.open || st.flat || !!st.q;
        const i = P.iNow;
        const total = i >= 0 ? P.bits[i] : null;
        const covered = i >= 0 && P.covered(i);
        if (flat && (!st.list || Date.now() - st.at > 9000)) {
            st.at = Date.now();
            SA.get('/api/shaperinsight/sampler/newest/' + n + '?limit=2000').then(function (r) {
                st.list = r;
                if (document.getElementById(box + '-hosts')) { drawTable(n, box); }
            }).catch(function () { st.list = {hosts: [], total: 0}; });
        }
        let rows = [];
        let named = 0;
        if (!flat) {
            P.hosts.forEach(function (h) {
                const v = i >= 0 ? (h.series[i] || 0) : 0;
                named += v;
                rows.push(hostRow(n, h.ip, v, total, P.method[h.ip]));
            });
            if (P.other || P.otherInfo.seen) {
                const v = P.other && i >= 0 ? (P.other[i] || 0) : 0;
                named += v;
                rows.push({colour: SH.theme().other, sortName: '~', find: '', ip: '', mac: '', vendor: '', cls: '', rate: v, how: SH.howHtml(P.method['']),
                    share: total ? Math.round(1000 * v / total) / 10 : null,
                    name: SA.chip('hosts-grouped', 'Other hosts') + ' <button type="button" class="btn btn-link btn-xs sa-open-row" ' +
                        'aria-expanded="false" data-pipe="' + SA.esc(n) + '" data-box="' + box + '">Show the ' + P.otherInfo.seen + ' other hosts · ' +
                        P.otherInfo.sending + ' sending now</button>'});
            }
        } else if (st.list) {
            /* 1.0.k: the sampler already gives what each host sent (exact or estimated, h[3] = shares not
               exact); a newest file from before the upgrade still holds pf's counts: scaled as 1.0.j did */
            const s = st.list.hosts.reduce(function (a, h) { return a + h[1]; }, 0);
            const f = !st.list.corrected && total && s > total ? total / s : 1;
            st.list.hosts.forEach(function (h) {
                const how = st.list.corrected ? Object.assign({exact: 1}, h[3] || {}) : (f < 1 ? {scaled: 1} : {exact: 1});
                if (st.list.corrected && h[3]) { how.exact = Math.max(0, 1 - Object.keys(h[3]).reduce(function (a, k) { return a + h[3][k]; }, 0)); }
                named += h[1] * f; rows.push(hostRow(n, h[0], h[1] * f, total, P.cake ? how : {counted: 1}));
            });
            if (st.q) { rows = rows.filter(function (x) { return x.find.indexOf(st.q) >= 0; }); }
        }
        if (covered && total !== null && !st.q && P.na) {
            const rest = Math.max(0, total - named);
            rows.push({colour: 'hatch', name: SA.chip('not-attributed', 'Not attributed'), sortName: '~~', ip: '', mac: '', vendor: '',
                cls: '', rate: rest, share: total ? Math.round(1000 * rest / total) / 10 : null, find: '', how: ''});
        }
        $('#' + box + '-open').text(flat ? 'Show the busiest only' : 'List every host')
            .attr('aria-expanded', flat ? 'true' : 'false').toggle(flat || P.otherInfo.seen > 0);
        $('#' + box + '-qn').text(st.q ? rows.length + ' hosts match' : (flat && st.list ? st.list.total + ' hosts in the newest step' +
            (st.list.total > st.list.hosts.length ? ' (the busiest ' + st.list.hosts.length + ' listed; search to find the others)' : '') : ''));
        const cols = [{title: 'Name', field: 'name', type: 'html', swatch: true, sortField: 'sortName'},
            {title: 'IP', field: 'ip', type: 'ip', wide: true},
            {title: 'MAC', field: 'mac', type: 'html', wide: true, minWidth: 140},
            {title: 'Manufacturer', field: 'vendor', type: 'html', wide: true}];
        if (P.diffserv === 1 || P.diffserv === 2) {
            cols.push({title: 'Class (tin)', field: 'cls', type: 'html', wide: true});
        }
        cols.push({title: 'Rate through the pipe', field: 'rate', type: 'num', fmt: function (v) { return SA.rate(v); }},
            {title: 'Share', field: 'share', type: 'num', fmt: function (v) { return v === null ? '–' : v + ' %'; }},
            {title: 'How counted', field: 'how', type: 'html', wide: true});
        const t = SA.table('#' + box + '-hosts', cols, rows, {sort: ['rate', 'desc'], label: P.title + ': hosts in the newest step',
            scroll: true, empty: st.q ? 'No host matches the search.' : (flat && !st.list ? 'Loading the hosts of the newest step...' :
                'No host traffic in the newest step (or the per-host view is paused, see the line at the top).')});
        /* a sort by another column than Rate lists every host (the grouped row cannot be ordered with them) */
        if (t && !t._saFlatHook) {
            t._saFlatHook = true;
            t.on('dataSorted', function (sorters) {
                const other = sorters.length && sorters[0].field !== 'rate';
                if (other && !tstate[n].flat && !tstate[n].open) { tstate[n].flat = true; setTimeout(function () { drawTable(n, box); }, 0); }
            });
        }
    }
    $(document).on('click', '.sa-open-row', function () {
        const n = $(this).attr('data-pipe');
        const box = $(this).attr('data-box');
        tstate[n].open = true;
        drawTable(n, box);
        $('#' + box + '-open').trigger('focus');
    });
    /* the newest step as a table, the same numbers and colours as the charts (host simulation) */
    SH.hostsTable = function (el, P) {
        const n = P.n;
        const box = el.id;
        const st = tstate[n] = tstate[n] || {open: false, q: '', flat: false, list: null, at: 0};
        if (!document.getElementById(box + '-hosts')) {
            el.innerHTML = '<div class="sa-find"><label for="' + box + '-q">Find a host</label> ' +
                '<input type="search" id="' + box + '-q" class="form-control sa-inline-select" autocomplete="off" placeholder="name, IP, MAC or maker"> ' +
                '<span class="sa-meta" id="' + box + '-qn" aria-live="polite"></span> ' +
                '<button type="button" class="btn btn-default btn-xs sa-open" id="' + box + '-open" aria-expanded="false" aria-controls="' + box + '-hosts"></button></div>' +
                '<div id="' + box + '-hosts"></div><p class="sa-meta sa-caption"></p>';
            let timer = null;
            const q = document.getElementById(box + '-q');
            q.value = st.q;
            q.addEventListener('input', function () {
                clearTimeout(timer);
                timer = setTimeout(function () { st.q = q.value.trim().toLowerCase(); drawTable(n, box); }, 150);
            });
            document.getElementById(box + '-open').addEventListener('click', function () { st.open = !st.open; st.flat = false; drawTable(n, box); });
        }
        st.P = P;
        drawTable(n, box);
        el.querySelector('.sa-caption').textContent = 'Newest ' + P.step + ' s · the busiest ' + P.hosts.length +
            ' hosts of the shown window in colour, the rest grouped. “How counted”: exact = counted after CAKE, where the packet leaves the firewall; ' +
            'counted, checked / fair-share estimate / scaled = traffic with no LAN-side count (ex.: the firewall’s own downloads), checked against or fitted to what the pipe sent. ' +
            'Point at a label for the reason; “How hosts are counted” above has the details.';
    };

    /* ------------------------------------------------------------------ lazy loading */
    /* each graph's library and module load only when the graph is on screen (SA_LAZY from the view:
       cache-safe URLs); a script loads once */
    const loaded = {};
    SH.load = function (urls) {
        return Promise.all((urls || []).map(function (u) {
            if (!loaded[u]) {
                loaded[u] = new Promise(function (ok, fail) {
                    const s = document.createElement('script');
                    s.src = u;
                    s.async = false;            /* in order: the library before the module that uses it */
                    s.onload = ok;
                    s.onerror = function () { delete loaded[u]; fail(new Error('could not load ' + u)); };
                    document.head.appendChild(s);
                });
            }
            return loaded[u];
        }));
    };
    SH.graphs = {};          /* filled by g1.js..g5.js: {id: {create(el, opts) -> {update(M, P), destroy()}}} */
}());
