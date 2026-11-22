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
<script src="{{ cache_safe('/ui/js/chart.umd.min.js') }}"></script>
<script src="{{ cache_safe('/ui/js/shaperinsight/ipcompare.js') }}"></script>
<script src="{{ cache_safe('/ui/js/shaperinsight/common.js') }}"></script>
<script>
'use strict';
$(document).ready(function () {
    const POINTS = 120;          // ring buffer: 10 minutes at the 5 s default
    const PALETTE = ['#E69F00', '#56B4E9', '#009E73', '#CC79A7', '#0072B2', '#D55E00', '#F0E442'];
    let chart = null;
    let last = null;             // previous counters, for rates
    const aboveCap = [];         // readings above a pipe's payload cap (should not happen)
    const ring = {labels: [], series: {}};

    function pipeName(p) {
        return 'Pipe ' + p.number + (p.description ? ' “' + p.description + '”' : '');
    }

    function renderSummary(d) {
        const s = d.summary;
        const level = SA.CLS[s.level] ? s.level : 'info';
        let html = '<div class="sa-summary sa-summary-' + level + '" role="status">' +
            '<i class="fa ' + SA.ICON[level] + ' fa-2x fa-fw text-' + SA.CLS[level] + '" aria-hidden="true"></i>' +
            '<p><strong>' + SA.esc(SA.WORD[level]) + ':</strong> ' + SA.words(s.text);
        if (s.problems) {
            html += ' <span class="sa-count">' + (s.problems === 1 ? '{{ lang._("One thing below needs a look.") }}' :
                SA.esc(s.problems) + ' {{ lang._("things below need a look.") }}') + '</span>';
        }
        html += '</p></div>';
        $('#sa-summary').html(html);
        const t = new Date(d.generated * 1000);
        $('#sa-updated').text(t.toLocaleTimeString() + (d.cached ? ' (shared cache)' : ''));
    }

    function renderModule(d) {
        const m = d.module;
        const rows = [];
        rows.push(['os-cake plugin', m.installed ? SA.badge('ok', 'installed') :
            SA.badge('info', 'not installed') + ' Pipes run their own scheduler ({{ lang._("FQ-CoDel or another") }}).']);
        if (m.installed) {
            rows.push(['CAKE kernel module', (m.loaded ? SA.badge('ok', 'loaded') : SA.badge('info', 'not loaded')) +
                (m.usable ? ' ' + SA.badge('ok', 'usable on this kernel') : ' ' + SA.badge('warn', 'not usable')) +
                (m.reason ? ' ' + SA.esc(m.reason) : '') + SA.why(m.usable ? '' : 'module-match')]);
            rows.push(['dummynet.ko check', SA.esc(m.builtin ? 'CAKE is built into this kernel' :
                (m.dummynet === 'match' ? 'matches the file cake-kmod was built for' : (m.dummynet || 'unknown')))]);
        }
        rows.push(['Loaded modules', SA.esc(m.modules.join(', ') || 'none')]);
        (m.warnings || []).forEach(function (w) {
            rows.push(['os-cake says', SA.badge('warn', 'warning') + ' ' + SA.esc(w)]);
        });
        (d.tunables || []).forEach(function (t) {
            rows.push(['Timing / forwarding', SA.badge(t.level) + ' ' + SA.words(t.text) + SA.why(t.claim)]);
        });
        const hz = d.sysctl['kern.hz'];
        rows.push(['kern.hz (timer)', SA.esc(hz || '?') + (hz === '1000' ? ' ' + SA.badge('ok', 'fine') : '')]);
        SA.table('#sa-module', [{title: '{{ lang._("Item") }}', field: 'item', sortField: 'idx'}, {title: '{{ lang._("State") }}', field: 'state', type: 'html'}],
            rows.map(function (r, k) { return {idx: k, item: r[0], state: r[1]}; }), {sort: ['item', 'asc'], empty: '–'});
    }

    const RANK = {bad: 0, warn: 1, info: 2, ok: 3};
    /* one colour per pipe for the live chart and the pipes table: its place among all pipes, by number */
    let pipeOrder = [];
    function pipeColour(num) {
        const k = pipeOrder.indexOf(String(num));
        return k < 0 ? null : PALETTE[k % PALETTE.length];
    }

    function renderPipes(d) {
        pipeOrder = d.pipes.map(function (p) { return String(p.number); }).sort();
        SA.table('#sa-pipes', [
            {title: '{{ lang._("Pipe") }}', field: 'pipe', type: 'html', swatch: true, sortField: 'number'},
            {title: '{{ lang._("Limit") }}', field: 'limit', type: 'num', fmt: function (v) { return v === null ? '–' : SA.rate(v); }},
            {title: '{{ lang._("Configured") }}', field: 'configured', type: 'html'},
            {title: '{{ lang._("Running in the kernel") }}', field: 'loaded', type: 'html'},
            {title: '{{ lang._("Rules via queue / direct") }}', field: 'via', type: 'num', fmt: function (v, row) { return v + ' / ' + row.direct; }},
            {title: '{{ lang._("Verdict") }}', field: 'verdict', type: 'html', sortField: 'rank'}
        ], d.pipes.map(function (p) {
            const lim = p.bandwidth_kernel !== null ? p.bandwidth_kernel : p.bandwidth_config;
            return {number: p.number, colour: pipeColour(p.number), limit: lim,
                pipe: SA.ref('pipe', pipeName(p), {uuid: p.uuid, content: SA.rate(lim) + ', ' + (p.intended || '')}) +
                    (p.enabled ? '' : ' <em>(disabled)</em>'),
                configured: SA.words(p.intended_words || p.intended) +
                    (p.sched_why ? '<br><span class="sa-meta">' + SA.words(p.sched_why) + '</span>' : ''),
                loaded: '<strong>' + (p.loaded ? SA.words(p.loaded_words || p.loaded) : 'not loaded') + '</strong>',
                via: p.rules_via_queue, direct: p.rules_direct, rank: RANK[p.level],
                verdict: SA.badge(p.level) + ' ' + SA.words(p.text) + SA.why(p.claim)};
        }), {sort: ['pipe', 'asc'], label: '{{ lang._("Pipes: configured and running") }}',
            empty: (d.summary && d.summary.text ? d.summary.text.replace(/\{\{term:[^|}]+\|?([^}]*)\}\}/g, '$1') : '') +
                ' ({{ lang._("Firewall: Shaper") }})'});
    }

    function renderFindings(sel, items, cols, titles) {
        SA.table(sel, cols.map(function (c, k) {
            return c === 'verdict' ? {title: titles[k], field: 'verdict', type: 'html', sortField: 'rank'} :
                {title: titles[k], field: c};
        }), items.map(function (f) {
            const row = {rank: RANK[f.level], verdict: SA.badge(f.level) + ' ' + SA.words(f.text) + SA.why(f.claim)};
            cols.forEach(function (c) { if (c !== 'verdict') { row[c] = f[c] === undefined || f[c] === null ? '' : String(f[c]); } });
            return row;
        }), {sort: ['verdict', 'asc'], empty: '{{ lang._("Nothing found: every object matches.") }}'});
    }

    function renderTins(d) {
        const cake = d.pipes.filter(function (p) { return p.instances && p.instances.length; });
        if (!cake.length) {
            $('#sa-tins').html('<p>' + SA.esc(d.stats_available ?
                '{{ lang._("No CAKE scheduler has counters yet: they appear once traffic passes a CAKE pipe.") }}' :
                '{{ lang._("No per-tin counters: no CAKE scheduler is loaded (or cake-kmod is older than 1.3).") }}') + '</p>');
            return;
        }
        cake.forEach(function (p) {
            p.instances.forEach(function (inst) {
                const id = 'sa-tins-' + p.number + '-' + String(inst.inst).replace(/[^0-9a-z]/gi, '');
                if (!document.getElementById(id)) {
                    if (!$('#sa-tins .sa-tins-box').length) { $('#sa-tins').empty(); }
                    $('#sa-tins').append('<div class="sa-tins-box"><h3>' + SA.esc(pipeName(p)) +
                        (p.instances.length > 1 ? ', instance ' + SA.esc(inst.inst) : '') + '</h3><div id="' + id + '"></div></div>');
                }
                SA.table('#' + id, [
                    {title: '{{ lang._("Tin") }}', field: 'name', sortField: 'idx'},
                    {title: '{{ lang._("Packets") }}', field: 'pkts', type: 'num', wide: true, fmt: function (v) { return SA.num(v); }},
                    {title: '{{ lang._("Data") }}', field: 'bytes', type: 'num', fmt: function (v) { return bytes(v); }},
                    {title: '{{ lang._("Drops") }}', field: 'drops', type: 'num', fmt: function (v) { return SA.num(v); }},
                    {title: '{{ lang._("ECN marks") }}', field: 'marks', type: 'num', fmt: function (v) { return SA.num(v); }},
                    {title: '{{ lang._("Wait: base / avg / peak") }}', field: 'avg', type: 'num',
                        fmt: function (v, t) { return SA.ms(t.base) + ' / ' + SA.ms(v) + ' / ' + SA.ms(t.peak); }},
                    {title: '{{ lang._("Flows: light / busy") }}', field: 'bulk', type: 'num', wide: true, fmt: function (v, t) { return SA.num(t.sparse) + ' / ' + SA.num(v); }},
                    {title: '{{ lang._("Backlog (packets)") }}', field: 'backlog', type: 'num', wide: true, fmt: function (v) { return SA.num(v); }}
                ], inst.tins.map(function (t, k) {
                    return {idx: k, name: t.name, pkts: t.pkts, bytes: t.bytes, drops: t.drops, marks: t.marks, base: t.base_delay,
                        avg: t.avg_delay, peak: t.peak_delay, sparse: t.sparse, bulk: t.bulk, backlog: t.backlog};
                }), {sort: ['name', 'asc'], label: 'Per-tin counters for ' + pipeName(p), empty: '{{ lang._("No tins reported.") }}'});
            });
        });
    }

    function bytes(b) {
        const u = [['GB', 1e9], ['MB', 1e6], ['kB', 1e3]];
        for (const [n, v] of u) {
            if (b >= v) {
                return SA.sig(b / v) + ' ' + n;
            }
        }
        return b + ' B';
    }

    function feedChart(d) {
        const r = d.rates;
        if (!r || !r.pipes) {
            return;
        }
        const names = {};
        d.pipes.forEach(function (p) { names[String(p.number)] = pipeName(p); });
        if (last && r.ts > last.ts) {
            ring.labels.push(new Date(r.ts * 1000).toLocaleTimeString());
            Object.keys(r.pipes).forEach(function (n) {
                const a = r.pipes[n], b = last.pipes[n];
                const key = 'rate:' + n;
                ring.series[key] = ring.series[key] || {label: (names[n] || 'Pipe ' + n) + ' (Mbit/s)', end: names[n] || 'Pipe ' + n, pipe: n, data: [], axis: 'y'};
                /* a rate from two (time, bytes) pairs, each stamped at its counter read; pairs
                   under 2 s apart are skipped (a small error in a short interval is a big one in
                   the rate), and a reading above the pipe's payload cap is counted and shown */
                const dt = b ? (a.ts || r.ts) - (b.ts || last.ts) : 0;
                const v = b && a.bytes >= b.bytes && dt >= 2 ? (a.bytes - b.bytes) * 8 / dt / 1e6 : null;
                if (v !== null && a.payload_cap && v * 1e6 > a.payload_cap * 1.01) {
                    aboveCap.push(names[n] || 'Pipe ' + n);
                }
                ring.series[key].data.push(v === null ? null : Number(v.toFixed(2)));
                if (a.avg_delay_us !== undefined) {
                    const dk = 'delay:' + n;
                    ring.series[dk] = ring.series[dk] || {label: (names[n] || 'Pipe ' + n) + ' wait (ms)', end: (names[n] || 'Pipe ' + n) + ' wait', pipe: n, data: [], axis: 'y1'};
                    ring.series[dk].data.push(Number((a.avg_delay_us / 1000).toFixed(2)));
                }
            });
            // keep every series aligned with the labels, then trim the ring
            Object.values(ring.series).forEach(function (s) {
                while (s.data.length < ring.labels.length) {
                    s.data.unshift(null);
                }
            });
            if (ring.labels.length > POINTS) {
                ring.labels.splice(0, ring.labels.length - POINTS);
                Object.values(ring.series).forEach(function (s) { s.data.splice(0, s.data.length - POINTS); });
            }
            drawChart();
        }
        last = r;
    }

    /* WCAG 1.4.1: colour is never the only cue. Each pipe keeps one colour AND one marker
       shape (for both its lines); rate lines are solid, wait lines dashed, in the legend too;
       and every line is named at its right end, so no legend lookup is needed. */
    const MARKERS = ['circle', 'rect', 'triangle', 'rectRot', 'star', 'crossRot'];
    const endLabels = {
        id: 'saEndLabels',
        afterDatasetsDraw: function (c) {
            const ctx = c.ctx;
            ctx.save();
            ctx.font = '12px ' + getComputedStyle(document.body).fontFamily;
            ctx.textAlign = 'right';
            ctx.textBaseline = 'bottom';
            // collect the line ends, then push labels apart so close lines stay readable
            const ends = [];
            c.data.datasets.forEach(function (ds, i) {
                const meta = c.getDatasetMeta(i);
                let j = ds.data.length - 1;
                while (j >= 0 && (ds.data[j] === null || ds.data[j] === undefined)) { j--; }
                if (j < 0 || !meta.data[j] || meta.hidden) { return; }
                ends.push({text: ds.endLabel, x: meta.data[j].x - 10, y: meta.data[j].y});
            });
            ends.sort(function (a, b) { return a.y - b.y; });
            for (let k = 1; k < ends.length; k++) {
                ends[k].y = Math.max(ends[k].y, ends[k - 1].y + 15);
            }
            ctx.textBaseline = 'middle';
            ctx.fillStyle = SA.cssVar('--chart-js-font-color', getComputedStyle(document.body).color);
            ends.forEach(function (e) { ctx.fillText(e.text, e.x, e.y); });
            ctx.restore();
        }
    };

    function drawChart() {
        const fg = SA.cssVar('--chart-js-font-color', getComputedStyle(document.body).color);
        const grid = SA.cssVar('--chart-js-border-color', 'rgba(128,128,128,0.25)');
        const pipes = [...new Set(Object.values(ring.series).map(function (s) { return s.pipe; }))].sort();
        // rates first, then wait times, each in pipe order
        const order = Object.keys(ring.series).sort(function (a, b) {
            return (a.startsWith('rate') ? 0 : 1) - (b.startsWith('rate') ? 0 : 1) || (a < b ? -1 : a > b ? 1 : 0);
        });
        const sets = order.map(function (k, i) {
            const s = ring.series[k];
            const delay = s.axis === 'y1';
            const n = pipeOrder.indexOf(s.pipe) >= 0 ? pipeOrder.indexOf(s.pipe) : pipes.indexOf(s.pipe);
            const last = s.data.length - 1;
            return {label: s.label, endLabel: s.end, data: s.data, yAxisID: s.axis,
                borderColor: PALETTE[n % PALETTE.length], backgroundColor: PALETTE[n % PALETTE.length],
                borderDash: delay ? [6, 4] : [], pointStyle: MARKERS[n % MARKERS.length],
                pointRadius: function (c) { return c.dataIndex === last ? 5 : 0; },
                borderWidth: delay ? 2 : 2.5, spanGaps: true, tension: 0.2};
        });
        if (chart === null) {
            chart = new Chart(document.getElementById('sa-chart'), {
                type: 'line',
                data: {labels: ring.labels, datasets: sets},
                plugins: [endLabels],
                options: {
                    animation: false, responsive: true, maintainAspectRatio: false,
                    interaction: {mode: 'index', intersect: false},
                    plugins: {legend: {labels: {color: fg, boxWidth: 34, boxHeight: 3,
                        // the swatch is a short line: filled for rates, dashed outline for wait times
                        generateLabels: function (c) {
                            return Chart.defaults.plugins.legend.labels.generateLabels(c).map(function (l) {
                                const ds = c.data.datasets[l.datasetIndex];
                                const dashed = ds.borderDash && ds.borderDash.length;
                                l.fillStyle = dashed ? 'rgba(0,0,0,0)' : ds.borderColor;
                                l.strokeStyle = ds.borderColor;
                                l.lineDash = dashed ? [6, 4] : [];
                                l.lineWidth = 2;
                                return l;
                            });
                        }}}},
                    scales: {
                        x: {ticks: {color: fg, maxTicksLimit: 8}, grid: {color: grid}},
                        y: {beginAtZero: true, title: {display: true, text: 'Mbit/s', color: fg}, ticks: {color: fg}, grid: {color: grid}},
                        y1: {beginAtZero: true, position: 'right', title: {display: true, text: 'ms (CAKE average wait)', color: fg},
                            ticks: {color: fg}, grid: {drawOnChartArea: false}}
                    }
                }
            });
        } else {
            chart.data.labels = ring.labels;
            chart.data.datasets = sets;
            chart.update('none');
        }
        // the same numbers as text, for screen readers and copy/paste
        const latest = sets.map(function (s) { return s.label + ': ' + (s.data.length ? s.data[s.data.length - 1] : '–'); });
        $('#sa-chart-text').text('Latest values: ' + latest.join('; ') + '.');
        $('#sa-chart-empty').toggle(ring.labels.length < 2);
        $('#sa-chart-flag').html(aboveCap.length ? SA.badge('warn', aboveCap.length + ' {{ lang._("readings above the payload cap") }}') +
            ' ' + SA.esc(aboveCap.slice(-3).join(', ')) + '. {{ lang._("A pipe cannot carry more IP bytes than rate × 1500 / (1500 + overhead); such a reading is a timing error. Please report it.") }}' : '');
    }

    function render(d) {
        if (!d || !d.summary) {
            $('#sa-summary').html('<div class="alert alert-danger" role="alert"><i class="fa fa-times-circle" aria-hidden="true"></i> ' +
                '{{ lang._("Could not read the shaper status (configd did not answer). Try again in a moment.") }}</div>');
            return;
        }
        renderSummary(d);
        SA.countdown(d.apply);
        document.body.setAttribute('data-sa-ready', '1');
        renderModule(d);
        renderPipes(d);
        renderFindings('#sa-diff', d.diff, ['object', 'field', 'intended', 'loaded', 'verdict'],
            ['{{ lang._("Object") }}', '{{ lang._("Setting") }}', '{{ lang._("Configured") }}', '{{ lang._("Loaded") }}', '{{ lang._("Verdict") }}']);
        renderFindings('#sa-ignored', d.ignored, ['object', 'setting', 'value', 'verdict'],
            ['{{ lang._("Object") }}', '{{ lang._("Setting") }}', '{{ lang._("Value") }}', '{{ lang._("Verdict") }}']);
        renderTins(d);
        feedChart(d);
        if (d.errors && d.errors.length) {
            $('#sa-errors').html('<div class="alert alert-warning" role="alert">' + SA.esc(d.errors.join(' / ')) + '</div>');
        }
    }

    /* health checks: not polled (they read interfaces and routes too); run on load and on demand.
       Checks with something to fix start open; the rest stay folded with their verdict visible. */
    function renderHealth(h) {
        if (!h || !h.checks) {
            $('#sa-health').html('<p>' + SA.badge('bad', '{{ lang._("Not available") }}') + ' ' +
                '{{ lang._("The checks could not run (configd did not answer).") }}</p>');
            return;
        }
        const html = h.checks.map(function (c) {
            const items = c.items.map(function (i) {
                return '<li>' + SA.badge(i.level) + ' ' + SA.esc(i.text) +
                    (i.why ? '<div class="sa-why-text"><strong>{{ lang._("Why") }}:</strong> ' + SA.esc(i.why) + '</div>' : '') +
                    (i.example ? '<div class="sa-why-text"><strong>{{ lang._("Fix") }}:</strong> ' + SA.esc(i.example) + '</div>' : '') +
                    '</li>';
            }).join('');
            const open = c.level === 'bad' || c.level === 'warn' ? ' open' : '';
            return '<details class="sa-check"' + open + '><summary>' + SA.badge(c.level) + ' <span class="sa-check-id">' +
                SA.esc(c.id) + '</span> ' + SA.esc(c.title) + ' <span class="sa-meta">(' + c.items.length + ')</span></summary>' +
                '<ul class="sa-check-items">' + items + '</ul></details>';
        }).join('');
        /* detection that fell back (no runtime gateway list, a failing tool) is said, not hidden:
           the internet links below, and checks S4/S9, may then be incomplete (review S8) */
        $('#sa-health').html((h.detect_error ? '<p>' + SA.badge('warn', '{{ lang._("Detection incomplete") }}') + ' ' +
            SA.esc(h.detect_error) + ' {{ lang._("The internet links, and the checks that use them, may be incomplete.") }}</p>' : '') +
            (h.no_shaper ? '<p>' + SA.badge('info', '{{ lang._("No shaper configured") }}') + ' ' +
            '{{ lang._("There is no enabled pipe or rule, so these checks have nothing to look at. The setup page can create one.") }}</p>' : '') + html);
        $('#sa-health-meta').text('{{ lang._("Checked") }} ' + new Date(h.generated * 1000).toLocaleTimeString() +
            (h.wans && h.wans.length ? '. {{ lang._("Internet links found") }}: ' + h.wans.join(', ') : '') + '.');
    }

    function loadHealth() {
        $('#sa-health-run').prop('disabled', true);
        return SA.get('/api/shaperinsight/health/get').then(renderHealth, function () { renderHealth(null); })
            .then(function () { $('#sa-health-run').prop('disabled', false); });
    }

    /* not polled: it reads the neighbour tables and the interfaces once */
    let hostInfo = {};
    let devNames = {};
    function loadShares() {
        /* devices/shares needs the full Shaper Insight privilege: it lists MACs with their addresses,
           which the read-only status role must not see (review S6); so do names and manufacturers */
        return Promise.all([
            SA.get('/api/shaperinsight/sampler/hosts').then(function (r) { hostInfo = r.hosts || {}; }).catch(function () {}),
            SA.get('/api/shaperinsight/devices/list').then(function (r) {
                (r.devices || []).forEach(function (x) { if (x.hostname) { devNames[x.address] = x.hostname; } });
            }).catch(function () {})
        ]).then(function () { return SA.get('/api/shaperinsight/devices/shares'); }).then(function (d) {
            if (!d || !Array.isArray(d.several)) { throw new Error((d && d.message) || 'no data'); }
            /* one row per address (Name, IP, MAC, Manufacturer, Interface), grouped under its device */
            if (!$('#sa-shares-table').length) {
                $('#sa-shares').html('<p class="sa-meta" id="sa-shares-note"></p><div id="sa-shares-table"></div>');
            }
            $('#sa-shares-note').html((d.several.length ? SA.badge('info', d.several.length + ' {{ lang._("device(s) with several addresses") }}') :
                SA.badge('ok') + ' {{ lang._("No local device used more than one address recently") }}') + ' (' + d.devices +
                ' {{ lang._("devices seen") }}). {{ lang._("While such a device sends from all of its addresses at once, it gets up to one share per address.") }}' +
                SA.whys(['host-isolation-per-address']));
            const rows = [];
            d.several.forEach(function (x) {
                x.addresses.forEach(function (a) {
                    const ip = String(a).split('/')[0];
                    const info = hostInfo[ip] || {};
                    rows.push({name: devNames[ip] || '', ip: ip, mac: x.mac + ' (' + x.addresses.length + ' {{ lang._("addresses") }})',
                        vendor: info.vendor || '', iface: x.if});
                });
            });
            SA.table('#sa-shares-table', [{title: '{{ lang._("Name") }}', field: 'name'}, {title: '{{ lang._("IP") }}', field: 'ip', type: 'ip'},
                {title: '{{ lang._("MAC") }}', field: 'mac'}, {title: '{{ lang._("Manufacturer") }}', field: 'vendor'},
                {title: '{{ lang._("Interface") }}', field: 'iface'}], rows,
                {sort: ['mac', 'asc'], empty: '{{ lang._("No device with several addresses right now.") }}'});
        }).catch(function (e) {
            $('#sa-shares').html('<p>' + SA.badge('bad', '{{ lang._("Not available") }}') + ' ' + SA.esc(e.message) +
                ' <span class="sa-meta">{{ lang._("This list needs the full Firewall: Shaper: Shaper Insight privilege; the read-only status role does not include it.") }}</span></p>');
        });
    }

    SA.loadGlossary().then(function () {
        loadShares();
        loadHealth();
        $('#sa-health-run').on('click', loadHealth);
        const poller = SA.poll(function () {
            return SA.get('/api/shaperinsight/status/get').then(render, function () { render(null); });
        }, 5);
        $('#sa-refresh').on('click', function () { poller.now(); });
    });
});
</script>

<div class="content-box sa-page">
    <div class="sa-head">
        <div id="sa-summary" aria-live="polite"><div class="alert alert-info"><i class="fa fa-spinner fa-pulse" aria-hidden="true"></i> {{ lang._('Reading the shaper status...') }}</div></div>
        <p class="sa-meta">{{ lang._('Last read') }}: <span id="sa-updated">–</span>.
            {{ lang._('Refreshes every 5 s while this tab is visible.') }}
            <button type="button" id="sa-refresh" class="btn btn-default btn-xs"><i class="fa fa-refresh" aria-hidden="true"></i> {{ lang._('Refresh now') }}</button></p>
        <div id="sa-errors"></div>
    </div>
</div>

<section class="content-box sa-section" aria-labelledby="sa-h-pipes">
    <h2 id="sa-h-pipes">{{ lang._('Is CAKE really running?') }}</h2>
    <p class="sa-lead">{{ lang._('Each pipe as configured on Firewall: Shaper, next to the scheduler the kernel reports for it right now. The configuration keeps saying FQ-CoDel when CAKE is on, because os-cake swaps the scheduler while loading; the kernel column is the proof.') }}</p>
    <div id="sa-pipes"></div>
</section>

<section class="content-box sa-section" aria-labelledby="sa-h-chart">
    <h2 id="sa-h-chart">{{ lang._('Live: traffic through each pipe') }}</h2>
    <p class="sa-lead">{{ lang._('Solid lines: data rate (left axis). Dashed lines: average time packets wait inside CAKE (right axis). The last 10 minutes this page was open. The history, per host, is on the sharing page.') }} <a href="/ui/shaperinsight/sharing">{{ lang._('Who uses each pipe') }}</a></p>
    <div class="sa-chart-box"><canvas id="sa-chart" role="img" aria-label="{{ lang._('Data rate and CAKE wait time per pipe') }}"></canvas></div>
    <p id="sa-chart-flag" class="sa-meta" aria-live="polite"></p>
    <p id="sa-chart-empty" class="sa-meta">{{ lang._('The graph starts after the second reading (about 5 s).') }}</p>
    <p id="sa-chart-text" class="sr-only" aria-live="off"></p>
</section>

<section class="content-box sa-section" aria-labelledby="sa-h-diff">
    <h2 id="sa-h-diff">{{ lang._('Configuration compared with the kernel') }}</h2>
    <p class="sa-lead">{{ lang._('What Firewall: Shaper says, next to what is loaded. Apply adds and changes objects but never removes old ones, so deleted or disabled objects can stay loaded.') }}</p>
    <div id="sa-diff"></div>
</section>

<section class="content-box sa-section" aria-labelledby="sa-h-ignored">
    <h2 id="sa-h-ignored">{{ lang._('Settings that have no effect') }}</h2>
    <p class="sa-lead">{{ lang._('Fields you can fill in on Firewall: Shaper that the chosen scheduler or the shaper template ignores.') }}</p>
    <div id="sa-ignored"></div>
</section>

<section class="content-box sa-section" aria-labelledby="sa-h-health">
    <h2 id="sa-h-health">{{ lang._('Health checks') }}</h2>
    <p class="sa-lead">{{ lang._('Ten checks for mistakes that the shaper pages do not warn about: weights that do nothing, objects that stay loaded, rules that never match or can never match, internet links that are shaped differently, a history sampler that stopped. Each finding says why it matters and how to fix it.') }}</p>
    <p class="sa-meta"><span id="sa-health-meta" aria-live="polite">–</span>
        <button type="button" id="sa-health-run" class="btn btn-default btn-xs"><i class="fa fa-stethoscope" aria-hidden="true"></i> {{ lang._('Check again') }}</button></p>
    <div id="sa-health"><p>–</p></div>
</section>

<section class="content-box sa-section" aria-labelledby="sa-h-tins">
    <h2 id="sa-h-tins">{{ lang._('CAKE counters per tin') }}</h2>
    <p class="sa-lead">{{ lang._('Counted by the CAKE module since the scheduler was loaded. Drops and marks are CAKE telling busy senders to slow down; a high number under load is normal.') }}</p>
    <div id="sa-tins"><p>–</p></div>
    <h3>{{ lang._('Devices with several addresses') }}</h3>
    <p class="sa-lead">{{ lang._("CAKE shares the line per address, not per device. The counters above do not list hosts (the CAKE module does not report them), so this list comes from the firewall's neighbour tables: local devices that used more than one address recently (IPv4 and IPv6, or several IPv6 privacy addresses). While such a device sends from all of them at once, it gets one share per address.") }}</p>
    <div id="sa-shares" aria-live="polite"><p>–</p></div>
</section>

<section class="content-box sa-section" aria-labelledby="sa-h-module">
    <h2 id="sa-h-module">{{ lang._('Module and timing') }}</h2>
    <div id="sa-module"></div>
    <p class="sa-meta"><a href="/ui/shaperinsight/compiled">{{ lang._('Show the compiled configuration (for forum posts)') }}</a></p>
</section>
