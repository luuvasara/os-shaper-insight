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
<script src="{{ cache_safe('/ui/js/shaperinsight/common.js') }}"></script>
<script src="{{ cache_safe('/ui/js/chart.umd.min.js') }}"></script>
<script>
'use strict';
$(document).ready(function () {
    /* Okabe-Ito: distinguishable with the common colour vision deficiencies; colour is never the
       only cue (each host is named in the table under its chart, in the same order as the stack) */
    const PALETTE = ['#E69F00', '#56B4E9', '#009E73', '#CC79A7', '#0072B2', '#D55E00', '#999999', '#F0E442'];
    const charts = {};
    let names = {};
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
            x: {ticks: {color: fg(), maxTicksLimit: 8}, grid: {color: grid()}},
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
    function capLine(labels, value, text) {
        return {type: 'line', label: text, data: labels.map(function () { return value; }), borderColor: fg(),
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
        const busy = (cap && rate >= 0.9 * cap) || drops > 0;
        return {busy: busy, rate: rate, drops: drops, cap: cap, step: p.step};
    }

    function renderPipe(n, p, hosts, d) {
        const box = 'sa-p-' + n;
        if (!$('#' + box).length) {
            $('#sa-pipes').append('<article class="sa-pipe" id="' + box + '" aria-labelledby="' + box + '-h">' +
                '<h3 id="' + box + '-h"></h3><p class="sa-meta" id="' + box + '-state" aria-live="polite"></p>' +
                '<p class="sa-meta" id="' + box + '-cap"></p>' +
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
        if (hosts && hosts.length) {
            hosts.forEach(function (h, k) {
                sets.push({label: hostName(h[0]), data: h[1].slice(0, labels.length).map(mbit), fill: true, stack: 'hosts',
                    backgroundColor: PALETTE[k % PALETTE.length] + 'CC', borderColor: PALETTE[k % PALETTE.length], borderWidth: 1, tension: 0});
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
                backgroundColor: 'rgba(128,128,128,0.25)', borderColor: 'rgba(128,128,128,0.6)', borderWidth: 1, tension: 0});
            const early = pipeBits.map(function (v, i) { return v === null || covered(i) ? null : v; });
            if (early.some(function (v) { return v !== null; })) {
                sets.push({label: '{{ lang._("pipe total, no per-host record") }}', data: early.map(mbit), fill: true, stack: 'early',
                    backgroundColor: PALETTE[4] + '55', borderColor: PALETTE[4], borderWidth: 1, borderDash: [2, 2], tension: 0});
            }
        } else {
            sets.push({label: '{{ lang._("pipe total") }}', data: pipeBits.map(mbit), fill: true, stack: 'hosts',
                backgroundColor: PALETTE[4] + '99', borderColor: PALETTE[4], borderWidth: 1.5, tension: 0});
        }
        /* never above the cap without saying so: such a point is marked and counted */
        pipeBits.forEach(function (v) { if (v !== null && cap && v > cap * 1.01) { above++; } });
        if (cap) { sets.push(capLine(labels, mbit(cap), '{{ lang._("cap") }} ' + SA.rate(cap))); }
        chart(box + '-c1', {type: 'line', data: {labels: labels, datasets: sets}, options: opts(axes('Mbit/s', true))});
        $('#' + box + '-c1').attr('aria-label', pipeTitle(n, p) + ': {{ lang._("rate per host, stacked, with the cap as a dashed line") }}');

        /* the newest row as a table: the same numbers as the chart, screen readers included */
        const i = pipeBits.length - 1;
        let rows = '';
        if (hosts && hosts.length && i >= 0) {
            hosts.forEach(function (h) {
                const v = h[1][i] || 0;
                rows += '<tr><th scope="row">' + SA.esc(hostName(h[0])) + '</th><td class="sa-num">' + SA.esc(SA.rate(v)) +
                    '</td><td class="sa-num">' + (pipeBits[i] ? Math.round(100 * v / pipeBits[i]) + ' %' : '–') + '</td></tr>';
            });
        }
        let hint = '';
        if (hosts && hosts.length && i >= 0 && pipeBits[i]) {
            const topV = Math.max.apply(null, hosts.filter(function (h) { return h[0] !== ''; }).map(function (h) { return h[1][i] || 0; }).concat([0]));
            if (topV > 0.6 * pipeBits[i] && cap && pipeBits[i] > 0.5 * cap) {
                hint = '<p class="sa-meta">' + SA.badge('info', '{{ lang._("One device has most of the pipe") }}') + ' ' +
                    '{{ lang._("The other hosts left it unused or are not sending enough to need their share. A speed test now shows the spare capacity, not a fair share.") }}</p>';
            }
        }
        $('#' + box + '-latest').html(hint + (rows ? '<table class="table table-condensed sa-latest"><caption>{{ lang._("Newest") }} ' +
            SA.esc(p.step) + ' s</caption><thead><tr><th scope="col">{{ lang._("Host") }}</th><th scope="col" class="sa-num">{{ lang._("Rate") }}</th>' +
            '<th scope="col" class="sa-num">{{ lang._("Share of the pipe") }}</th></tr></thead><tbody>' + rows + '</tbody></table>' : '') +
            (above ? '<p class="sa-meta">' + SA.badge('warn', above + ' {{ lang._("readings above the cap") }}') + ' {{ lang._("The counters are IP bytes after the shaper; a reading above the cap means two reads were timed badly or the pipe changed. Each reading is stamped at the counter read, so this should not happen; please report it.") }}</p>' : ''));

        /* F2: drops and marks per second, CAKE's average wait */
        const s = p.series;
        if (s.drops) {
            chart(box + '-c2', {type: 'bar', data: {labels: labels, datasets: [
                {type: 'bar', label: '{{ lang._("drops/s") }}', data: s.drops.map(function (v) { return v === null ? null : Number(v.toFixed(2)); }),
                    backgroundColor: PALETTE[5], yAxisID: 'y'},
                {type: 'bar', label: '{{ lang._("ECN marks/s") }}', data: s.marks.map(function (v) { return v === null ? null : Number(v.toFixed(2)); }),
                    backgroundColor: PALETTE[1], yAxisID: 'y'},
                {type: 'line', label: '{{ lang._("average wait (ms)") }}', data: s.delay.map(function (v) { return v === null ? null : Number(v.toFixed(2)); }),
                    borderColor: fg(), borderWidth: 1.5, pointRadius: 0, yAxisID: 'y1', borderDash: [2, 2]}
            ]}, options: opts(axes('/s', false, 'ms'))});
            $('#' + box + '-c2').attr('aria-label', pipeTitle(n, p) + ': {{ lang._("drops and ECN marks per second, and the average wait in CAKE") }}');
        } else {
            $('#' + box + '-c2').parent().replaceWith('<p class="sa-meta">{{ lang._("This pipe does not run CAKE, so there are no drop or wait counters to show.") }}</p>');
        }

        /* F5: per tin, with the Bulk threshold (1/16 of the rate in diffserv3 and diffserv4) */
        const tins = p.tins || [];
        if (tins.length > 1 && p.tin_series) {
            if (!$('#' + box + '-c3').length) {
                $('#' + box + '-tins').html('<h4>{{ lang._("Per priority class (CAKE tin)") }}</h4><p class="sa-meta" id="' + box + '-tinlead"></p>' +
                    '<div class="sa-chart-box"><canvas id="' + box + '-c3" role="img"></canvas></div>');
            }
            const tsets = tins.map(function (name, k) {
                return {label: name, data: (p.tin_series['t' + k + 'b'] || []).map(mbit), fill: true, stack: 'tins',
                    backgroundColor: PALETTE[k % PALETTE.length] + 'CC', borderColor: PALETTE[k % PALETTE.length], borderWidth: 1, tension: 0};
            });
            const bulk = tins.indexOf('Bulk');
            if (bulk >= 0 && (p.diffserv === 1 || p.diffserv === 2) && cap) {
                tsets.push(capLine(labels, mbit(cap / 16), '{{ lang._("Bulk threshold") }} ' + SA.rate(cap / 16)));
                $('#' + box + '-tinlead').html('{{ lang._("Bulk keeps at least its threshold (1/16 of the rate) when others are busy, and grows back to the whole pipe when nobody else needs it.") }}' + SA.whys(['tins-capped']));
            }
            chart(box + '-c3', {type: 'line', data: {labels: labels, datasets: tsets}, options: opts(axes('Mbit/s', true))});
            $('#' + box + '-c3').attr('aria-label', pipeTitle(n, p) + ': {{ lang._("rate per CAKE tin, stacked") }}');
        }
    }

    function render(d) {
        const run = d.running ? SA.badge('ok', '{{ lang._("Recording") }}') : SA.badge(d.enabled ? 'warn' : 'info', d.enabled ? '{{ lang._("Not running") }}' : '{{ lang._("Switched off") }}');
        const c = d.cost || {};
        $('#sa-sampler').html(run + ' ' + SA.esc((c.cpu_ms_per_sample !== undefined ?
            '{{ lang._("Every 10 s, about") }} ' + c.cpu_ms_per_sample + ' ms {{ lang._("of CPU per sample") }} (' +
            (c.cpu_ms_per_sample / 100).toFixed(2) + ' % {{ lang._("of one core") }}), ' + SA.num(c.states) + ' {{ lang._("pf states read") }}, ' +
            Math.round((c.rss_kb || 0) / 1024) + ' MB {{ lang._("memory") }}, ' + (Math.round((c.rrd_bytes || 0) / 1e5) / 10) +
            ' MB {{ lang._("of RRD files") }}, ' + (Math.round((c.ring_bytes || 0) / 1e4) / 100) + ' MB {{ lang._("host list") }}.' : '')));
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
        return SA.get('/api/shaperinsight/sampler/history/' + windowS).then(render, function (e) {
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

    SA.loadGlossary().then(function () {
        SA.get('/api/shaperinsight/devices/list').then(function (r) {
            (r.devices || []).forEach(function (x) { if (x.hostname) { names[x.address] = x.hostname; } });
        }).catch(function () {}).finally(function () {
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
        <p class="sa-meta">{{ lang._('Hosts come from the firewall’s connection states (pf), sampled every 10 s: a host’s upload counts what it sent before CAKE dropped anything, and a connection that ends between two samples loses its last seconds. “Not attributed” is what the pipe carried that no state accounts for.') }}</p>
    </section>
    <section class="sa-section" aria-label="{{ lang._('Pipes') }}">
        <div id="sa-pipes"><p>{{ lang._('Reading the history...') }}</p></div>
        <div id="sa-na"></div>
    </section>
    <section class="sa-section">
        <p class="sa-meta">{{ lang._('The pipe history is in RRD files in /var/db/rrd (10 s for 6 hours, 1 minute for 3 days, 10 minutes for 30 days, 1 hour for a year). They survive reboots, and Reporting: Health lists them under “shaper”. Hosts are not in the RRDs: an RRD has a fixed set of series, and the busiest hosts change all the time; they are kept for 6 hours in /var/run, which a reboot clears.') }}</p>
    </section>
</div>
