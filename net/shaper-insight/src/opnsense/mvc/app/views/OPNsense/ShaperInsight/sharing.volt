{#
 # Copyright (C) 2026 os-shaper-insight contributors
 # All rights reserved.
 #
 # Redistribution and use in source and binary forms, with or without
 # modification, are permitted provided that the following conditions are met:
 #
 # 1. Redistributions of source code must retain the above copyright notice,
 #    this list of conditions and the following disclaimer.
 #
 # 2. Redistributions in binary form must reproduce the above copyright
 #    notice, this list of conditions and the following disclaimer in the
 #    documentation and/or other materials provided with the distribution.
 #
 # THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 # INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 # AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 # AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 # OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 # SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 # INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 # CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 # ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 # POSSIBILITY OF SUCH DAMAGE.
 #}
<link rel="stylesheet" href="{{ cache_safe('/ui/css/shaperinsight/shaperinsight.css') }}">
<script src="{{ cache_safe('/ui/js/shaperinsight/ipcompare.js') }}"></script>
<script src="{{ cache_safe('/ui/js/shaperinsight/verdicts.js') }}"></script>
<script src="{{ cache_safe('/ui/js/shaperinsight/common.js') }}"></script>
<script src="{{ cache_safe('/ui/js/chart.umd.min.js') }}"></script>
<script>
'use strict';
$(document).ready(function () {
    /* eight series colours, a light and a dark step each, checked on OPNsense's light and dark surfaces
       (host simulation, TESTING.md: Okabe-Ito's #999999 and #F0E442 fail there); no grey among them, grey is for
       "other hosts" (solid) and "not attributed" (hatched). Colour is never the only cue: the legend
       and the table name every coloured host. */
    const STEPS = {
        light: {series: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'],
            other: '#b9b8b0', na: '#deddd6', naInk: '#8a8983'},
        dark: {series: ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#0ca30c', '#9085e9', '#e66767'],
            other: '#6e6d68', na: '#50504d', naInk: '#9a9993'}
    };
    function theme() {
        const m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(getComputedStyle(document.body).backgroundColor || '');
        const lum = m ? (0.2126 * m[1] + 0.7152 * m[2] + 0.0722 * m[3]) / 255 : 1;
        return STEPS[lum < 0.5 ? 'dark' : 'light'];
    }
    let PALETTE = theme().series;
    const charts = {};
    let names = {};
    let hostInfo = {};      /* address -> {mac, vendor, shared} (sampler/hosts) */
    let prioInfo = null;    /* priorities/status: classes with their members' addresses */
    let extraLoaded = 0;
    /* colour slots (host simulation): a host keeps its slot while it is in a pipe's coloured set; a host that
       comes back gets its old slot if it is free, else the lowest free one; colours never cycle */
    const slotsOf = {};       /* pipe -> {host: slot} */
    const lastSlot = {};      /* host -> slot it had last */
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
    function hostColour(pipe, h) {
        const k = (slotsOf[pipe] || {})[h];
        return k === undefined ? null : PALETTE[k % PALETTE.length];
    }
    /* "not attributed": the neutral with a 45 degree hatch, so it differs from "other hosts" without colour */
    function hatch() {
        const t = theme();
        const c = document.createElement('canvas');
        c.width = c.height = 8;
        const x = c.getContext('2d');
        x.fillStyle = t.na; x.fillRect(0, 0, 8, 8);
        x.strokeStyle = t.naInk; x.lineWidth = 1.5; x.beginPath();
        x.moveTo(0, 8); x.lineTo(8, 0); x.moveTo(-2, 2); x.lineTo(2, -2); x.moveTo(6, 10); x.lineTo(10, 6); x.stroke();
        return x.createPattern(c, 'repeat');
    }
    /* the CAKE tins: one fixed texture each in the text colour, so no host colour also means a tin on this
       page (review 2026-11-22); "not attributed" keeps the 45 degree hatch, which no tin uses */
    function tinPattern(k) {
        const c = document.createElement('canvas');
        c.width = c.height = 8;
        const x = c.getContext('2d');
        x.fillStyle = theme().na; x.fillRect(0, 0, 8, 8);
        x.strokeStyle = x.fillStyle = fg(); x.lineWidth = 1.5; x.beginPath();
        if (k === 0) { x.moveTo(4, 0); x.lineTo(4, 8); }                                    /* Best Effort: vertical */
        else if (k === 1) { x.moveTo(0, 4); x.lineTo(8, 4); }                               /* Bulk: horizontal */
        else if (k === 2) { x.arc(4, 4, 1.6, 0, 2 * Math.PI); x.fill(); }                   /* Video: dots */
        else { x.moveTo(4, 0); x.lineTo(4, 8); x.moveTo(0, 4); x.lineTo(8, 4); }           /* Voice: grid */
        x.stroke();
        return x.createPattern(c, 'repeat');
    }
    function cut(text, n) { text = String(text); return text.length > n ? text.slice(0, n - 1) + '…' : text; }
    const tstate = {};        /* pipe -> {open, q, flat, list, at} the hosts table */
    /* the priority a host belongs to, from the resolved members (IPv4 networks and exact addresses) */
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
    function classOf(ip, aliases) {
        if (!prioInfo || !ip) { return ''; }
        for (const c of prioInfo.classes || []) {
            for (const m of c.members || []) {
                if ((m.addresses || []).some(function (x) { return inNet(ip, x); })) {
                    const label = c['class'] === 'background' ? '{{ lang._("Background") }}' : '{{ lang._("Interactive") }}';
                    const al = aliases[m.target];
                    return SA.esc(label + ' (' + c.tin + ') · ') + (al ? SA.ref('alias', m.target, al) : SA.esc(m.target));
                }
            }
        }
        return '{{ lang._("Best Effort") }}';
    }
    let aliasInfo = {};
    let windowS = 3600;
    let poller = null;

    function fg() { return SA.cssVar('--chart-js-font-color', getComputedStyle(document.body).color); }
    function grid() { return SA.cssVar('--chart-js-border-color', 'rgba(128,128,128,0.25)'); }
    function hostName(h) {
        if (h === '') { return '{{ lang._("all other hosts") }}'; }
        return names[h] ? names[h] + ' (' + h + ')' : h;
    }
    function mbit(v) { return v === null || v === undefined ? null : Number((v / 1e6).toFixed(3)); }
    function label(t, step) {
        const d = new Date(t * 1000);
        return step >= 600 ? d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) :
            d.toLocaleTimeString();
    }
    function pipeTitle(n, p) {
        return 'Pipe ' + n + (p.description ? ' “' + p.description + '”' : '');
    }

    /* a chart that is created once and then only gets new data */
    function chart(id, cfg) {
        if (charts[id]) {
            charts[id].data = cfg.data;
            charts[id].options = cfg.options;
            charts[id].update('none');
            return charts[id];
        }
        charts[id] = new Chart(document.getElementById(id), cfg);
        return charts[id];
    }
    function axes(yTitle, stacked, y1Title) {
        const a = {
            x: {ticks: {color: fg(), maxTicksLimit: window.innerWidth <= 767 ? 4 : 8, maxRotation: 0}, grid: {color: grid()}},
            y: {beginAtZero: true, stacked: !!stacked, title: {display: true, text: yTitle, color: fg()},
                ticks: {color: fg()}, grid: {color: grid()}}
        };
        if (y1Title) {
            a.y1 = {beginAtZero: true, position: 'right', title: {display: true, text: y1Title, color: fg()},
                ticks: {color: fg()}, grid: {drawOnChartArea: false}};
        }
        return a;
    }
    function opts(scales) {
        return {animation: false, responsive: true, maintainAspectRatio: false, interaction: {mode: 'index', intersect: false},
            plugins: {legend: {labels: {color: fg(), boxWidth: 18}}}, scales: scales, elements: {point: {radius: 0}}};
    }
    /* value: one number, or one per row (the cap in force at that time, review N13) */
    function capLine(labels, value, text) {
        return {type: 'line', label: text, data: labels.map(function (_, i) { return Array.isArray(value) ? value[i] : value; }), borderColor: fg(),
            borderDash: [6, 4], borderWidth: 1.5, pointRadius: 0, fill: false, stack: 'cap', order: -1};
    }

    /* F3: is the shaper the bottleneck right now? From the newest row */
    function activity(p, rows) {
        const s = p.series;
        const i = s.bytes.length - 1;
        if (i < 0) { return null; }
        const rate = s.bytes[i] || 0;
        const drops = s.drops ? (s.drops[i] || 0) : 0;
        const cap = p.payload_cap || p.bandwidth || 0;
        const busy = SAverdict.limiting(s.bytes, s.drops, cap);
        return {busy: busy, rate: rate, drops: drops, cap: cap, step: p.step};
    }

    /* one host row: Name (with "IP · maker" as a second line on a phone), IP, MAC, Manufacturer */
    function hostRow(n, ip, v, total) {
        const info = hostInfo[ip] || {};
        const fw = ip === 'this firewall';
        const nm = fw ? '{{ lang._("this firewall") }}' : (names[ip] || '');
        const shared = (info.shared || []).length;
        const maker = info.vendor ? SA.esc(info.vendor) : (info.random ?
            '<span class="sa-muted" title="{{ lang._("Locally administered MAC: a phone or laptop with private Wi-Fi addresses, a virtual machine or a container. The IEEE list names no maker for it.") }}">{{ lang._("private (random) MAC") }}</span>' : '');
        return {colour: hostColour(n, ip), ip: fw ? '' : ip, rate: v, share: total ? Math.round(1000 * v / total) / 10 : null,
            sortName: nm, find: [nm, ip, info.mac || '', info.vendor || ''].join(' ').toLowerCase(),
            name: (nm ? '<span class="sa-name" title="' + SA.esc(nm + (fw ? '' : ' (' + ip + ')')) + '"><bdi>' + SA.esc(nm) + '</bdi></span>' :
                '<span class="sa-muted">{{ lang._("no name") }}</span>') +
                '<span class="sa-sub">' + SA.esc(fw ? '' : ip) + (info.vendor ? ' · ' + SA.esc(info.vendor) : '') + '</span>',
            mac: info.mac ? SA.esc(info.mac) + (shared ? ' <span class="sa-muted" title="' + SA.esc([ip].concat(info.shared).join(', ')) +
                '">{{ lang._("shared") }} ×' + (shared + 1) + '</span>' : '') : '',
            vendor: maker, cls: classOf(ip, aliasInfo)};
    }

    function hostsTable(n, box) {
        const st = tstate[n];
        const P = st.pipe;
        const flat = st.open || st.flat || !!st.q;
        const naRow = function (named) {
            const rest = Math.max(0, P.total - named);
            return {colour: 'hatch', name: SA.chip('not-attributed', '{{ lang._("Not attributed") }}'), sortName: '~~', ip: '', mac: '', vendor: '',
                cls: '', rate: rest, share: P.total ? Math.round(1000 * rest / P.total) / 10 : null, find: ''};
        };
        /* the newest-step list: fetched only while the list is open, searched or sorted another way */
        if (flat && (!st.list || Date.now() - st.at > 9000)) {
            st.at = Date.now();
            SA.get('/api/shaperinsight/sampler/newest/' + n + '?limit=2000').then(function (r) {
                st.list = r;
                hostsTable(n, box);
            }).catch(function () { st.list = {hosts: [], total: 0}; });
        }
        let rows = [];
        let named = 0;
        if (!flat) {
            P.hosts.forEach(function (h) {
                const v = P.i >= 0 ? (h[1][P.i] || 0) : 0;
                named += v;
                if (h[0] !== '') { rows.push(hostRow(n, h[0], v, P.total)); }
            });
            const other = P.hosts.find(function (h) { return h[0] === ''; });
            if (other || P.other.seen) {
                const v = other && P.i >= 0 ? (other[1][P.i] || 0) : 0;
                rows.push({colour: theme().other, sortName: '~', find: '', ip: '', mac: '', vendor: '', cls: '', rate: v,
                    share: P.total ? Math.round(1000 * v / P.total) / 10 : null,
                    name: SA.chip('hosts-grouped', '{{ lang._("Other hosts") }}') + ' <button type="button" class="btn btn-link btn-xs sa-open-row" ' +
                        'aria-expanded="false" data-pipe="' + SA.esc(n) + '">{{ lang._("Show the") }} ' + P.other.seen + ' {{ lang._("other hosts") }} · ' +
                        P.other.sending + ' {{ lang._("sending now") }}</button>'});
            }
        } else if (st.list) {
            /* pf counts before the shaper drops: scaled to what the pipe sent, as the chart is (N9) */
            const sum = st.list.hosts.reduce(function (a, h) { return a + h[1]; }, 0);
            const f = P.total && sum > P.total ? P.total / sum : 1;
            st.list.hosts.forEach(function (h) { named += h[1] * f; rows.push(hostRow(n, h[0], h[1] * f, P.total)); });
            if (st.q) {
                rows = rows.filter(function (x) { return x.find.indexOf(st.q) >= 0; });
            }
        }
        if (P.covered && P.total !== null && !st.q) {
            rows.push(naRow(named));
        }
        $('#' + box + '-open').text(flat ? '{{ lang._("Show the busiest only") }}' : '{{ lang._("List every host") }}')
            .attr('aria-expanded', flat ? 'true' : 'false').toggle(flat || P.other.seen > 0);
        $('#' + box + '-qn').text(st.q ? rows.length + ' {{ lang._("hosts match") }}' : (flat && st.list ? st.list.total + ' {{ lang._("hosts in the newest step") }}' +
            (st.list.total > st.list.hosts.length ? ' ({{ lang._("the busiest") }} ' + st.list.hosts.length + ' {{ lang._("listed; search to find the others") }})' : '') : ''));
        const cols = [{title: '{{ lang._("Name") }}', field: 'name', type: 'html', swatch: true, sortField: 'sortName'},
            {title: '{{ lang._("IP") }}', field: 'ip', type: 'ip', wide: true},
            {title: '{{ lang._("MAC") }}', field: 'mac', type: 'html', wide: true},
            {title: '{{ lang._("Manufacturer") }}', field: 'vendor', type: 'html', wide: true}];
        if (P.diffserv === 1 || P.diffserv === 2) {
            cols.push({title: '{{ lang._("Class (tin)") }}', field: 'cls', type: 'html', wide: true});
        }
        cols.push({title: '{{ lang._("Rate through the pipe") }}', field: 'rate', type: 'num', fmt: function (v) { return SA.rate(v); }},
            {title: '{{ lang._("Share") }}', field: 'share', type: 'num', fmt: function (v) { return v === null ? '–' : v + ' %'; }});
        const t = SA.table('#' + box + '-hosts', cols, rows, {sort: ['rate', 'desc'], label: P.title + ': {{ lang._("hosts in the newest step") }}',
            scroll: true, empty: st.q ? '{{ lang._("No host matches the search.") }}' :
                (flat && !st.list ? '{{ lang._("Loading the hosts of the newest step...") }}' :
                '{{ lang._("No host traffic in the newest step (or the per-host view is paused, see the line at the top).") }}')});
        /* a sort by another column than Rate lists every host (the grouped row cannot be ordered with them) */
        if (t && !t._saFlatHook) {
            t._saFlatHook = true;
            t.on('dataSorted', function (sorters) {
                const other = sorters.length && sorters[0].field !== 'rate';
                if (other && !tstate[n].flat && !tstate[n].open) { tstate[n].flat = true; setTimeout(function () { hostsTable(n, box); }, 0); }
            });
        }
    }
    $(document).on('click', '.sa-open-row', function () {
        const n = $(this).attr('data-pipe');
        tstate[n].open = true;
        hostsTable(n, 'sa-p-' + n);
        $('#sa-p-' + n + '-open').trigger('focus');
    });

    function renderPipe(n, p, hosts, d) {
        const box = 'sa-p-' + n;
        if (!$('#' + box).length) {
            $('#sa-pipes').append('<article class="sa-pipe" id="' + box + '" aria-labelledby="' + box + '-h">' +
                '<h3 id="' + box + '-h"></h3><p class="sa-meta" id="' + box + '-state" aria-live="polite"></p>' +
                '<p class="sa-meta" id="' + box + '-cap"></p>' +
                '<ul class="sa-legend" id="' + box + '-legend" aria-label="{{ lang._("Chart legend") }}"></ul>' +
                '<div class="sa-chart-box"><canvas id="' + box + '-c1" role="img"></canvas></div>' +
                '<div id="' + box + '-latest"></div>' +
                '<h4>{{ lang._("Drops, marks and wait time") }}</h4>' +
                '<div class="sa-chart-box sa-strip"><canvas id="' + box + '-c2" role="img"></canvas></div>' +
                '<div id="' + box + '-tins"></div></article>');
        }
        $('#' + box + '-h').text(pipeTitle(n, p) + (p.where && p.where.length ? ' · ' + p.where.join(', ') : ''));
        const labels = p.series.bytes.map(function (_, i) { return label(p.start + i * p.step, p.step); });
        const cap = p.payload_cap || p.bandwidth || 0;
        $('#' + box + '-cap').text(p.cake && p.overhead ?
            '{{ lang._("Configured") }} ' + SA.rate(p.bandwidth) + '; {{ lang._("CAKE counts") }} ' + p.overhead +
            ' {{ lang._("bytes of framing per packet, so the IP packets that fit are at most") }} ' + SA.rate(cap) +
            ' (' + SA.rate(p.bandwidth) + ' × 1500 / ' + (1500 + p.overhead) + '). {{ lang._("The dashed line is that cap.") }}' :
            '{{ lang._("Configured") }} ' + SA.rate(p.bandwidth) + '. {{ lang._("The dashed line is that rate.") }}');

        /* F3 badge */
        const a = activity(p);
        if (a) {
            $('#' + box + '-state').html(a.busy ?
                SA.badge('warn', '{{ lang._("Limiting now") }}') + ' ' + SA.esc(SA.rate(a.rate) + ' {{ lang._("of") }} ' + SA.rate(a.cap) +
                    (a.drops > 0 ? ', ' + a.drops.toFixed(1) + ' {{ lang._("drops/s") }}' : '') +
                    ' {{ lang._("in the last") }} ' + a.step + ' s. {{ lang._("The shaper is the bottleneck: hosts that want more share what is there.") }}') :
                SA.badge('ok', '{{ lang._("Idle") }}') + ' ' + SA.esc(SA.rate(a.rate) + ' {{ lang._("of") }} ' + SA.rate(a.cap) +
                    ' {{ lang._("in the last") }} ' + a.step + ' s. {{ lang._("The shaper is not limiting anything: every host gets what it asks for.") }}'));
        }

        /* F1: hosts stacked under the cap; what pf did not attribute is the rest of the pipe's rate */
        const sets = [];
        const pipeBits = p.series.bytes;
        let above = 0;
        const th = theme();
        PALETTE = th.series;
        const otherN = ((d.hosts_other || {})[n] || {}).seen || 0;
        const legend = [];
        if (hosts && hosts.length) {
            assignSlots(n, hosts.filter(function (h) { return h[0] !== ''; }).map(function (h) { return h[0]; }));
            hosts.forEach(function (h) {
                const c = h[0] === '' ? th.other : hostColour(n, h[0]);
                const name = h[0] === '' ? otherN + ' {{ lang._("other hosts") }}' : hostName(h[0]);
                legend.push([c, name, h[0] === '' ? '' : h[0]]);
                sets.push({label: name, data: h[1].slice(0, labels.length).map(mbit), fill: true, stack: 'hosts',
                    backgroundColor: c, borderColor: c, borderWidth: 1, tension: 0});
            });
            /* rows from before the first host record (the 6 h ring, or a ring cleared by a reboot) have
               no per-host data: they show as the pipe total, not as "not attributed" */
            const since = d.hosts_since || 0;
            const covered = function (i) { return p.start + i * p.step >= since; };
            const rest = pipeBits.map(function (v, i) {
                if (v === null || !covered(i)) { return null; }
                const sum = hosts.reduce(function (s, h) { return s + (h[1][i] || 0); }, 0);
                return Math.max(0, v - sum);
            });
            sets.push({label: '{{ lang._("not attributed to a host") }}', data: rest.map(mbit), fill: true, stack: 'hosts',
                backgroundColor: hatch(), borderColor: th.naInk, borderWidth: 1, tension: 0});
            legend.push(['hatch', '{{ lang._("not attributed to a host") }}', '']);
            const early = pipeBits.map(function (v, i) { return v === null || covered(i) ? null : v; });
            if (early.some(function (v) { return v !== null; })) {
                sets.push({label: '{{ lang._("pipe total, no per-host record") }}', data: early.map(mbit), fill: true, stack: 'early',
                    backgroundColor: theme().other + '88', borderColor: fg(), borderWidth: 1, borderDash: [2, 2], tension: 0});
            }
        } else {
            sets.push({label: '{{ lang._("pipe total") }}', data: pipeBits.map(mbit), fill: true, stack: 'hosts',
                backgroundColor: theme().other, borderColor: fg(), borderWidth: 1.5, tension: 0});
        }
        /* pf counts a host's bytes before the shaper drops them: the stack is scaled to what the pipe
           delivered, and what the hosts offered is a dashed line of its own (review N9) */
        const offered = (d.hosts_offered || {})[n] || [];
        let overOffer = 0;
        offered.forEach(function (v, k) { if (pipeBits[k] && v > pipeBits[k] * 1.01) { overOffer = Math.max(overOffer, v - pipeBits[k]); } });
        if (overOffer > 0) {
            sets.push({type: 'line', label: '{{ lang._("offered by these hosts (before drops)") }}', data: offered.slice(0, labels.length).map(mbit),
                borderColor: fg(), borderDash: [2, 3], borderWidth: 1.5, pointRadius: 0, fill: false, stack: 'offered'});
        }
        /* never above the cap without saying so: readings above the cap of their own time are counted */
        above = p.above_cap || 0;
        const capRows = (p.cap_rows || []).map(function (v) { return v ? mbit(v) : null; });
        if (cap) { sets.push(capLine(labels, capRows.some(function (v) { return v !== null; }) ? capRows : mbit(cap), '{{ lang._("cap") }} ' + SA.rate(cap))); }
        /* the legend is HTML above the chart: Chart.js drops entries that do not fit (host simulation) */
        $('#' + box + '-legend').html(legend.map(function (x) {
            return '<li>' + (x[0] === 'hatch' ? '<span class="sa-swatch sa-hatch" aria-hidden="true"></span>' : SA.swatch(x[0])) +
                '<span class="sa-name" title="' + SA.esc(x[1]) + '"><bdi>' + SA.esc(x[1]) + '</bdi></span></li>';
        }).join(''));
        const o1 = opts(axes('Mbit/s', true));
        o1.plugins.legend = {display: false};
        /* tooltip: no zero rows, busiest first, long names cut at 32 characters */
        o1.plugins.tooltip = {filter: function (it) { return it.raw !== null && it.raw > 0; },
            itemSort: function (a, b) { return b.raw - a.raw; },
            callbacks: {label: function (it) { return cut(it.dataset.label, 32) + ': ' + it.formattedValue + ' Mbit/s'; }}};
        chart(box + '-c1', {type: 'line', data: {labels: labels, datasets: sets}, options: o1});
        $('#' + box + '-c1').attr('aria-label', pipeTitle(n, p) + ': {{ lang._("rate per host, stacked, with the cap as a dashed line") }}');

        /* the newest step as a table (SA.table), the same numbers and colours as the chart (host simulation):
           by default the coloured hosts, one expander for the other hosts and "not attributed"; opening
           it, searching or sorting by another column than Rate lists every host flat, from the newest-step
           endpoint, fetched only then */
        const i = pipeBits.length - 1;
        const total = i >= 0 ? pipeBits[i] : null;
        const st = tstate[n] = tstate[n] || {open: false, q: '', flat: false, list: null, at: 0};
        if (!$('#' + box + '-hosts').length) {
            $('#' + box + '-latest').html('<div class="sa-hint"></div><p class="sa-meta sa-caption"></p>' +
                '<div class="sa-find"><label for="' + box + '-q">{{ lang._("Find a host") }}</label> ' +
                '<input type="search" id="' + box + '-q" class="form-control sa-inline-select" autocomplete="off" ' +
                'placeholder="{{ lang._("name, IP, MAC or maker") }}"> <span class="sa-meta" id="' + box + '-qn" aria-live="polite"></span> ' +
                '<button type="button" class="btn btn-default btn-xs sa-open" id="' + box + '-open" aria-expanded="false" aria-controls="' + box + '-hosts"></button></div>' +
                '<div id="' + box + '-hosts"></div><div class="sa-notes"></div>');
            let timer = null;
            $('#' + box + '-q').on('input', function () {
                const v = $(this).val();
                clearTimeout(timer);
                timer = setTimeout(function () { st.q = v.trim().toLowerCase(); hostsTable(n, box); }, 150);
            });
            $('#' + box + '-open').on('click', function () { st.open = !st.open; st.flat = false; hostsTable(n, box); });
        }
        st.pipe = {total: total, hosts: hosts || [], other: (d.hosts_other || {})[n] || {seen: 0, sending: 0},
            covered: i >= 0 && p.start + i * p.step >= (d.hosts_since || 0), i: i, diffserv: p.diffserv, title: pipeTitle(n, p)};
        hostsTable(n, box);
        let hint = '';
        if (hosts && hosts.length && i >= 0 && pipeBits[i]) {
            const topV = Math.max.apply(null, hosts.filter(function (h) { return h[0] !== ''; }).map(function (h) { return h[1][i] || 0; }).concat([0]));
            if (topV > 0.6 * pipeBits[i] && cap && pipeBits[i] > 0.5 * cap) {
                hint = '<p class="sa-meta">' + SA.badge('info', '{{ lang._("One device has most of the pipe") }}') + ' ' +
                    '{{ lang._("The other hosts left it unused or are not sending enough to need their share. A speed test now shows the spare capacity, not a fair share.") }}</p>';
            }
        }
        $('#' + box + '-latest .sa-hint').html(hint);
        $('#' + box + '-latest .sa-caption').text('{{ lang._("Newest") }} ' + p.step + ' s · ' + d.hosts_shown +
            ' {{ lang._("busiest hosts of the shown window in colour, the rest grouped") }}');
        $('#' + box + '-latest .sa-notes').html(
            (overOffer > 0 ? '<p class="sa-meta">' + SA.badge('info', '{{ lang._("Rates scaled to what the pipe sent") }}') + ' ' +
                SA.esc('{{ lang._("pf counts the packets of each host before the shaper drops them, on download and on upload, so the hosts offered up to") }} ' +
                SA.rate(overOffer) + ' {{ lang._("more than the pipe sent (the dashed line). The stack shows the part of what was sent that each host had; a host that causes the drops loses a little more than its scaled figure says.") }}') + '</p>' : '') +
            (above ? '<p class="sa-meta">' + SA.badge('warn', above + ' {{ lang._("readings above the cap") }}') + ' {{ lang._("Each reading is compared with the cap in force when it was taken. The counters are IP bytes after the shaper and are stamped at the read, so this should not happen; please report it.") }}</p>' : ''));

        /* F2: drops and marks per second, CAKE's average wait */
        const s = p.series;
        if (s.drops) {
            chart(box + '-c2', {type: 'bar', data: {labels: labels, datasets: [
                {type: 'bar', label: '{{ lang._("drops/s") }}', data: s.drops.map(function (v) { return v === null ? null : Number(v.toFixed(2)); }),
                    backgroundColor: fg(), yAxisID: 'y'},
                {type: 'bar', label: '{{ lang._("ECN marks/s") }}', data: s.marks.map(function (v) { return v === null ? null : Number(v.toFixed(2)); }),
                    backgroundColor: theme().other, yAxisID: 'y'},
                {type: 'line', label: '{{ lang._("average wait (ms)") }}', data: s.delay.map(function (v) { return v === null ? null : Number(v.toFixed(2)); }),
                    borderColor: fg(), borderWidth: 1.5, pointRadius: 0, yAxisID: 'y1', borderDash: [2, 2]}
            ]}, options: opts(axes('/s', false, 'ms'))});
            $('#' + box + '-c2').attr('aria-label', pipeTitle(n, p) + ': {{ lang._("drops and ECN marks per second, and the average wait in CAKE") }}');
        } else {
            $('#' + box + '-c2').parent().replaceWith('<p class="sa-meta">{{ lang._("This pipe does not run CAKE, so there are no drop or wait counters to show.") }}</p>');
        }

        /* F5: per tin, with the Bulk threshold (1/16 of the rate in diffserv3 and diffserv4) */
        const tins = p.tins || [];
        const bulkInfo = p.bulk;
        if (bulkInfo && bulkInfo.state === 'besteffort') {
            $('#' + box + '-tins').html('<p class="sa-meta">' + SA.badge('warn', '{{ lang._("Background has no effect here") }}') + ' ' + SA.esc(bulkInfo.text) + '</p>');
        }
        if (tins.length > 1 && p.tin_series) {
            if (!$('#' + box + '-c3').length) {
                $('#' + box + '-tins').html('<h4>{{ lang._("Per priority class (CAKE tin)") }}</h4><p class="sa-meta" id="' + box + '-tinlead"></p>' +
                    '<div class="sa-chart-box"><canvas id="' + box + '-c3" role="img"></canvas></div><div id="' + box + '-tintable"></div>');
            }
            const tsets = tins.map(function (name, k) {
                return {label: name, data: (p.tin_series['t' + k + 'b'] || []).map(mbit), fill: true, stack: 'tins',
                    backgroundColor: tinPattern(k), borderColor: fg(), borderWidth: 1, tension: 0};
            });
            const bulk = tins.indexOf('Bulk');
            if (bulk >= 0 && (p.diffserv === 1 || p.diffserv === 2) && cap) {
                tsets.push(capLine(labels, mbit(cap / 16), '{{ lang._("Bulk threshold") }} ' + SA.rate(cap / 16)));
                const st = bulkInfo && bulkInfo.text ? SA.badge(bulkInfo.state === 'yielding' ? 'info' : 'ok',
                    bulkInfo.state === 'yielding' ? '{{ lang._("Yielding") }}' : bulkInfo.state === 'free' ? '{{ lang._("Free line") }}' :
                    bulkInfo.state === 'idle' ? '{{ lang._("Idle") }}' : '{{ lang._("Background") }}') + ' ' + SA.esc(bulkInfo.text) + '<br>' : '';
                $('#' + box + '-tinlead').html(st + SA.esc(bulkInfo && bulkInfo.floor_text ? bulkInfo.floor_text : '') + ' ' +
                    '{{ lang._("Bulk (Background) keeps at least this threshold when others are busy, and grows back to the whole pipe when nobody else needs it.") }}' + SA.whys(['tins-capped']));
            }
            /* the newest step per class: drops and ECN marks apart, so a drop is not read as a fault */
            const lt = p.latest_tins || [];
            const stepS = 10;
            SA.table('#' + box + '-tintable', [
                {title: '{{ lang._("Class") }}', field: 'name', swatch: true},
                {title: '{{ lang._("Rate") }}', field: 'rate', type: 'num', fmt: function (v) { return SA.rate(v); }},
                {title: '{{ lang._("Drops/s") }}', field: 'drops', type: 'num', fmt: function (v) { return v.toFixed(1); }},
                {title: '{{ lang._("ECN marks/s") }}', field: 'marks', type: 'num', fmt: function (v) { return v.toFixed(1); }},
                {title: '{{ lang._("Waiting (packets)") }}', field: 'backlog', type: 'num', fmt: function (v) { return SA.num(v); }}
            ], lt.length === tins.length ? tins.map(function (name, k) {
                const x = lt[k];
                return {colour: 'tin-' + k, name: name, rate: x[0] * 8 / stepS, drops: x[1] / stepS,
                    marks: x[2] / stepS, backlog: x[3]};
            }) : [], {sort: ['rate', 'desc'], label: pipeTitle(n, p) + ': {{ lang._("per class, newest 10 s") }}',
                empty: '{{ lang._("No per-class reading yet: it appears after two samples.") }}'});
            if (!$('#' + box + '-tinnote').length) {
                $('#' + box + '-tintable').after('<p class="sa-meta" id="' + box + '-tinnote">{{ lang._("A drop and an ECN mark are the same signal: slow down. CAKE marks when both ends use ECN and drops otherwise, so drops in a busy class are how it works, not a fault.") }}</p>');
            }
            chart(box + '-c3', {type: 'line', data: {labels: labels, datasets: tsets}, options: opts(axes('Mbit/s', true))});
            $('#' + box + '-c3').attr('aria-label', pipeTitle(n, p) + ': {{ lang._("rate per CAKE tin, stacked") }}');
        }
    }

    function render(d) {
        const run = d.state === 'running' ? SA.badge('ok', '{{ lang._("Recording") }}') :
            d.state === 'restarting' ? SA.badge('warn', '{{ lang._("Restarting") }}') :
            d.enabled === null ? SA.badge('warn', '{{ lang._("Settings unreadable just now") }}') :
            SA.badge(d.enabled ? 'warn' : 'info', d.enabled ? '{{ lang._("Not running") }}' : '{{ lang._("Switched off") }}');
        const c = d.cost || {};
        const wd = d.watchdog || {};
        $('#sa-sampler').html(run + ' ' + SA.esc((c.cpu_ms_per_sample !== undefined ?
            '{{ lang._("Every 10 s, about") }} ' + c.cpu_ms_per_sample + ' ms {{ lang._("of CPU per sample") }} (' +
            (c.cpu_ms_per_sample / 100).toFixed(2) + ' % {{ lang._("of one core") }}), ' + SA.num(c.states) + ' {{ lang._("pf states") }}' +
            (c.hosts_every_s && c.hosts_every_s > 10 ? ' ({{ lang._("hosts read every") }} ' + c.hosts_every_s + ' s {{ lang._("to keep the cost down") }})' : '') + ', ' +
            Math.round((c.rss_kb || 0) / 1024) + ' MB {{ lang._("memory now") }} (' + Math.round((c.rss_peak_kb || 0) / 1024) + ' MB {{ lang._("peak") }}), ' +
            (Math.round((c.rrd_bytes || 0) / 1e5) / 10) + ' MB {{ lang._("of RRD files") }}, ' + (Math.round((c.ring_bytes || 0) / 1e4) / 100) +
            ' MB {{ lang._("host list") }}.' : '') +
            (c.hosts_paused ? ' {{ lang._("Per-host view paused:") }} ' + c.hosts_paused + ' ({{ lang._("the pipe history goes on") }}).' : '') +
            (wd.count ? ' {{ lang._("The watchdog found the sampler stopped and started it") }} ' + wd.count + ' × ({{ lang._("last") }} ' +
                new Date(wd.last * 1000).toLocaleString() + ').' : '')));
        $('#sa-toggle').text(d.enabled ? '{{ lang._("Switch the sampler off") }}' : '{{ lang._("Switch the sampler on") }}').data('on', d.enabled);
        $('#sa-hosts-note').text(d.window > d.hosts_keep_s ?
            '{{ lang._("Hosts are kept for 6 hours; this window shows the pipes and tins only.") }}' :
            (d.hosts_since ? '{{ lang._("Hosts recorded since") }} ' + new Date(d.hosts_since * 1000).toLocaleString() + '.' : ''));
        const ns = Object.keys(d.pipes || {});
        if (!ns.length) {
            $('#sa-pipes').html('<p>' + SA.badge('info', '{{ lang._("Nothing recorded yet") }}') + ' {{ lang._("The first points appear 20 s after the sampler starts, for pipes that carry traffic.") }}</p>');
        } else {
            $('#sa-pipes > p').remove();
            ns.forEach(function (n) { renderPipe(n, d.pipes[n], (d.hosts || {})[n], d); });
        }
        const na = ['na-up', 'na-down'].filter(function (k) { return d.hosts && d.hosts[k] && d.hosts[k].length; });
        $('#sa-na').html(na.length ? '<p class="sa-meta">{{ lang._("Traffic not attributed to a pipe (the firewall itself on an interface without shaping, or IPv6 without NAT when several links are shaped):") }} ' +
            na.map(function (k) {
                return (k === 'na-up' ? '{{ lang._("upload") }}' : '{{ lang._("download") }}') + ': ' + d.hosts[k].map(function (h) {
                    return SA.esc(hostName(h[0]));
                }).join(', ');
            }).join('; ') + '</p>' : '');
        document.body.setAttribute('data-sa-ready', '1');
    }

    function load() {
        loadExtra();
        return SA.get('/api/shaperinsight/sampler/history/' + windowS).then(function (d) {
            $('#sa-shown').val(String(d.hosts_shown || 8));
            render(d);
        }, function (e) {
            $('#sa-sampler').html(SA.badge('bad', '{{ lang._("Not available") }}') + ' ' + SA.esc(e.message) +
                ' <span class="sa-meta">{{ lang._("This page needs the full Firewall: Shaper: Shaper Insight privilege: it lists local addresses with their traffic.") }}</span>');
            document.body.setAttribute('data-sa-ready', '1');
        });
    }

    $('#sa-window').on('change', function () {
        windowS = parseInt($(this).val(), 10);
        Object.keys(charts).forEach(function (k) { charts[k].destroy(); delete charts[k]; });
        $('#sa-pipes').empty();
        if (poller) { poller.now(); }
    });
    $('#sa-toggle').on('click', function () {
        const on = !$(this).data('on');
        ajaxCall('/api/shaperinsight/settings/set', {shaperinsight: {general: {sampler: on ? '1' : '0'}}}, function () {
            ajaxCall('/api/shaperinsight/sampler/' + (on ? 'start' : 'stop'), {}, function () { if (poller) { poller.now(); } });
        });
    });

    /* names, MACs, manufacturers, priority members and aliases: every 60 s, not every 10 s */
    function loadExtra() {
        if (Date.now() - extraLoaded < 60000) { return Promise.resolve(); }
        extraLoaded = Date.now();
        return Promise.all([
            SA.get('/api/shaperinsight/devices/list').then(function (r) {
                (r.devices || []).forEach(function (x) { if (x.hostname) { names[x.address] = x.hostname; } });
                aliasInfo = {};
                (r.aliases || []).forEach(function (a) { aliasInfo[a.name] = a; });
            }).catch(function () {}),
            SA.get('/api/shaperinsight/sampler/hosts').then(function (r) { hostInfo = r.hosts || {}; }).catch(function () {}),
            SA.get('/api/shaperinsight/priorities/status').then(function (r) { prioInfo = r; }).catch(function () {})
        ]);
    }

    $('#sa-shown').on('change', function () {
        ajaxCall('/api/shaperinsight/settings/set', {shaperinsight: {general: {hosts_shown: $(this).val()}}}, function () {
            if (poller) { poller.now(); }
        });
    });

    SA.loadGlossary().then(function () {
        loadExtra().finally(function () {
            poller = SA.poll(load, 10);
        });
    });
});
</script>

<div class="content-box sa-page">
    <section class="sa-section" aria-labelledby="sa-h-sharing">
        <h2 id="sa-h-sharing">{{ lang._('Who uses each pipe') }}</h2>
        <p class="sa-lead">{{ lang._('Each pipe as the shaper sees it: the rate of every local host stacked under the pipe’s cap, drops and wait time, and the CAKE priority classes. A sampler on the firewall records them every 10 s, so the history is there as soon as the page opens.') }}</p>
        <p class="sa-meta" id="sa-sampler" aria-live="polite">–</p>
        <p class="sa-meta">
            <label for="sa-window">{{ lang._('Show the last') }}</label>
            <select id="sa-window" class="form-control sa-inline-select">
                <option value="600">{{ lang._('10 minutes') }}</option>
                <option value="3600" selected>{{ lang._('hour') }}</option>
                <option value="21600">{{ lang._('6 hours') }}</option>
                <option value="259200">{{ lang._('3 days') }}</option>
                <option value="2592000">{{ lang._('30 days') }}</option>
                <option value="31536000">{{ lang._('year') }}</option>
            </select>
            <label for="sa-shown">{{ lang._('Hosts drawn') }}</label>
            <select id="sa-shown" class="form-control sa-inline-select"
                    title="{{ lang._('The busiest hosts over the shown window get a colour; the rest are one grey band. At most 8: more colours cannot be told apart.') }}">
                <option>3</option><option>4</option><option>5</option><option>6</option><option>7</option>
                <option selected>8</option>
            </select>
            <button type="button" id="sa-toggle" class="btn btn-default btn-xs">–</button>
            <span id="sa-hosts-note"></span>
        </p>
        <details class="sa-explain">
            <summary>{{ lang._('Why a speed test cannot show how the line is shared') }}</summary>
            <p>{{ lang._('A speed test sees only its own device. When the line is shared fairly, it gets whatever the other hosts leave unused at that moment, so "800 while the backups ran" happens with fair sharing, with unfair sharing and with no shaping at all. Sharing only shows when two hosts that both keep sending are measured at the same time, which is what the stacked chart below does.') }}</p>
            <p>{{ lang._('The number itself leans high: Ookla sorts its samples, drops the two fastest and the slowest quarter, and averages the rest, measured on the client.') }}
                <a href="https://www.speedtest.net/help/guides/how-does-speedtest-measure-my-network-speeds" rel="noopener noreferrer" target="_blank">{{ lang._('Ookla: how Speedtest measures') }}</a>.
                {{ lang._('Its ceiling is below the configured rate: TCP payload is about rate × 1448 / (1500 + overhead).') }}</p>
        </details>
        <p class="sa-meta">{{ lang._('Hosts come from the firewall’s connection states (pf), sampled every 10 s (less often on a firewall with many states; paused above 50,000). pf counts packets before CAKE drops them, in both directions, so each sample is scaled down to what the pipe really sent; a connection that ends between two samples loses its last seconds. “Not attributed” is what the pipe carried that no state accounts for.') }}</p>
    </section>
    <section class="sa-section" aria-label="{{ lang._('Pipes') }}">
        <div id="sa-pipes"><p>{{ lang._('Reading the history...') }}</p></div>
        <div id="sa-na"></div>
    </section>
    <section class="sa-section">
        <p class="sa-meta">{{ lang._('The pipe history is in RRD files in /var/db/rrd (10 s for 6 hours, 1 minute for 3 days, 10 minutes for 30 days, 1 hour for a year). They survive reboots, and Reporting: Health lists them under “shaper”. Hosts are not in the RRDs: an RRD has a fixed set of series, and the busiest hosts change all the time; they are kept for 6 hours in /var/run, which a reboot clears.') }}</p>
    </section>
</div>
