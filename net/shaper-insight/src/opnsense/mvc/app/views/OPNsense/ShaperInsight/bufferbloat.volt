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
    const esc = SA.esc;
    /* Okabe-Ito, as on the status page; every line also has its own dash or marker (WCAG 1.4.1) */
    const PALETTE = ['#E69F00', '#56B4E9', '#009E73', '#CC79A7', '#0072B2', '#D55E00', '#F0E442'];
    const AXIS_W = 64;
    const DASHES = [[], [6, 4], [2, 3], [10, 3, 2, 3], [1, 2]];
    const PHASE_WORD = {idle: 'Idle', download: 'Download', upload: 'Upload', both: 'Both', cooldown: 'Cool-down'};
    let charts = [];
    let shown = null;           // the result on screen
    let pollTimer = null;

    mapDataToFormUI({'frm_bufferbloat': '/api/shaperinsight/settings/get'}).done(function () {
        formatTokenizersUI();
        $('.selectpicker').selectpicker('refresh');
    });

    /* grade colours come from the theme's label classes; the letter and the words carry the meaning */
    function gradeClass(g) {
        return {'A+': 'text-success', 'A': 'text-success', 'B': 'text-warning', 'C': 'text-danger', 'D': 'text-danger', 'F': 'text-danger'}[g] || '';
    }
    function ms(v) { return v === null || v === undefined ? '–' : (Math.round(v * 10) / 10) + ' ms'; }
    function mbit(v) { return v === null || v === undefined ? '–' : (Math.round(v * 10) / 10) + ' Mbit/s'; }

    /* phase bands behind every chart, so the three charts read as one timeline */
    const phaseBands = {
        id: 'saPhases',
        beforeDraw: function (c) {
            const r = c.$saPhases;
            if (!r) { return; }
            const x = c.scales.x;
            const ctx = c.ctx;
            ctx.save();
            ctx.font = '11px ' + getComputedStyle(document.body).fontFamily;
            ctx.fillStyle = SA.cssVar('--chart-js-font-color', getComputedStyle(document.body).color);
            ctx.textBaseline = 'top';
            let t0 = 0;
            r.forEach(function (p, i) {
                const a = x.getPixelForValue(t0);
                const b = x.getPixelForValue(t0 + p.seconds);
                if (i % 2) {
                    ctx.globalAlpha = 0.07;
                    ctx.fillRect(a, c.chartArea.top, b - a, c.chartArea.bottom - c.chartArea.top);
                }
                ctx.globalAlpha = 0.85;
                if (c.$saLabels) { ctx.fillText(PHASE_WORD[p.name] || p.name, a + 4, c.chartArea.top + 2); }
                t0 += p.seconds;
            });
            ctx.restore();
        }
    };
    /* one crosshair for all three charts: hovering one shows the same second in the others */
    function linkHover(src, evt, active) {
        if (src.$saSyncing) { return; }
        const idx = active.length ? active[0].index : null;
        charts.forEach(function (c) {
            if (c === src) { return; }
            c.$saSyncing = true;
            const els = idx === null ? [] : c.data.datasets.map(function (d, i) { return {datasetIndex: i, index: idx}; })
                .filter(function (e) { return c.data.datasets[e.datasetIndex].data[idx] !== null; });
            c.setActiveElements(els);
            c.tooltip.setActiveElements(els, {x: 0, y: 0});
            c.update('none');
            c.$saSyncing = false;
        });
    }
    function chart(id, title, unit, datasets, labels, phases, top) {
        const fg = SA.cssVar('--chart-js-font-color', getComputedStyle(document.body).color);
        const grid = SA.cssVar('--chart-js-border-color', 'rgba(128,128,128,0.25)');
        const hasY1 = datasets.some(function (d) { return d.yAxisID === 'y1'; });
        const total = (phases || []).reduce(function (a, p) { return a + p.seconds; }, 0) || undefined;
        const c = new Chart(document.getElementById(id), {
            type: 'line',
            data: {labels: labels, datasets: datasets},
            plugins: [phaseBands],
            options: {
                animation: false, responsive: true, maintainAspectRatio: false,
                interaction: {mode: 'index', intersect: false},
                onHover: function (evt, active, ch) { linkHover(ch, evt, active); },
                plugins: {legend: {position: 'bottom', labels: {color: fg, boxWidth: 30, boxHeight: 2} },
                    title: {display: true, text: title, color: fg, align: 'start'},
                    tooltip: {callbacks: {title: function (it) { return it.length ? it[0].parsed.x + ' s' : ''; },
                        label: function (it) {
                            const y = it.raw === null || it.raw.y === null ? null : it.parsed.y;
                            return it.dataset.label + ': ' + (y === null ? 'no value' : y + ' ' + it.dataset.unit);
                        } } } },
                /* the same x-pixel mapping on all three charts: fixed axis widths left and right, and a
                   right axis on every chart (invisible where nothing uses it) */
                scales: {x: {type: 'linear', min: 0, max: total, offset: false, ticks: {color: fg, callback: function (v) { return v + ' s'; } }, grid: {color: grid} },
                    y: {beginAtZero: true, title: {display: true, text: unit, color: fg}, ticks: {color: fg}, grid: {color: grid},
                        afterFit: function (sc) { sc.width = AXIS_W; } },
                    y1: {display: true, position: 'right', beginAtZero: true,
                        title: {display: hasY1, text: 'drops per second', color: fg}, ticks: {color: fg, display: hasY1},
                        grid: {drawOnChartArea: false, drawTicks: hasY1, display: hasY1}, border: {display: hasY1},
                        afterFit: function (sc) { sc.width = AXIS_W; } } }
            }
        });
        c.$saPhases = phases;
        c.$saLabels = top;
        return c;
    }
    function draw(r) {
        charts.forEach(function (c) { c.destroy(); });
        charts = [];
        const s = r.samples || {};
        const lat = s.latency || [];
        const rates = s.rates || [];
        const cake = s.cake || [];
        const pts = function (rows, f) { return rows.map(function (x) { return {x: x.t, y: f(x)}; }); };
        const line = function (label, data, n, unit, extra) {
            return Object.assign({label: label, data: data, unit: unit, borderColor: PALETTE[n % PALETTE.length],
                backgroundColor: PALETTE[n % PALETTE.length], borderDash: DASHES[n % DASHES.length],
                pointRadius: 0, borderWidth: 2, spanGaps: false, tension: 0}, extra || {});
        };
        charts.push(chart('sa-bb-rate', 'Throughput on ' + (r.interface || 'the internet link'), 'Mbit/s', [
            line('Download', pts(rates, function (x) { return x.down; }), 1, 'Mbit/s'),
            line('Upload', pts(rates, function (x) { return x.up; }), 0, 'Mbit/s')], undefined, r.phases, true));
        charts.push(chart('sa-bb-lat', 'Round trip (gaps = no reply)', 'ms', (r.targets || []).map(function (t, i) {
            return line(t === r.gateway ? t + ' (WAN gateway)' : t, pts(lat, function (x) { return x[t] === undefined ? null : x[t]; }), i + 2, 'ms');
        }), undefined, r.phases, false));
        /* CAKE: average wait per pipe and class (lines) and drops (bars, right axis); only those that carried traffic */
        const keys = [];
        cake.forEach(function (x) {
            Object.keys(x.tins).forEach(function (k) { if (x.tins[k].mbit > 0.1 && keys.indexOf(k) < 0) { keys.push(k); } });
        });
        const cakeSets = [];
        keys.sort().forEach(function (k, i) {
            cakeSets.push(line('pipe ' + k.replace('/', ', ') + ' wait', pts(cake, function (x) { return x.tins[k] ? x.tins[k].delay_ms : null; }), i, 'ms'));
            cakeSets.push(line('pipe ' + k.replace('/', ', ') + ' drops', pts(cake, function (x) { return x.tins[k] ? x.tins[k].drops : null; }), i,
                'drops/s', {type: 'bar', yAxisID: 'y1', borderDash: [], barThickness: 3, backgroundColor: PALETTE[i % PALETTE.length]}));
        });
        charts.push(chart('sa-bb-cake', cakeSets.length ? 'Inside CAKE: average wait and drops per class' :
            'Inside CAKE: no CAKE pipe carried this test\'s traffic', 'ms', cakeSets, undefined, r.phases, false));
    }
    function render(r) {
        shown = r;
        const sum = r.summary || {};
        const lab = /^lab\b/i.test((r.settings || {}).label || '');
        let h = '';
        let phaseRows = null;
        if (r.state === 'done') {
            h += '<div class="sa-summary" role="status"><span class="sa-g sa-grade ' + gradeClass(r.grade) + '">' + esc(r.grade) +
                '</span><p><b>Added delay under load: ' + ms(r.added_worst) + '</b> (the worse of download and upload, median over the targets). ' +
                'Grades as the Waveform test: A+ under 5 ms, A under 30, B under 60, C under 200, D under 400, else F. ' +
                (r.shaped ? 'The link <b>' + esc(r.interface) + '</b> has shaper rules, so this measures the shaper.' :
                    'The link <b>' + esc(r.interface) + '</b> has <b>no shaper rules</b>: this measures the line without shaping.') +
                (lab ? ' <span class="sa-g">lab run</span>' : '') + '</p></div>';
            h += '<p class="sa-note">Method: this firewall\'s own test (ICMP echo 5 times a second; load by ' + esc(String((r.settings || {}).streams)) +
                ' curl streams per direction). ' + (r.settings && r.settings.label ? 'Label: ' + esc(r.settings.label) + '. ' : '') +
                'Finished ' + esc(new Date(r.finished * 1000).toLocaleString()) + '.</p>';
            h += '<div id="sa-bb-phases"></div>';
            phaseRows = [];
            ['idle', 'download', 'upload', 'both'].forEach(function (p, k) {
                const v = sum[p];
                if (!v) { return; }
                const refl = Object.keys(v.targets).filter(function (t) { return t !== r.gateway; }).map(function (t) { return v.targets[t]; });
                const med = function (key) {
                    const a = refl.map(function (x) { return x[key]; }).filter(function (x) { return x !== null; }).sort(function (a, b) { return a - b; });
                    return a.length ? a[Math.floor(a.length / 2)] : null;
                };
                phaseRows.push({idx: k, phase: PHASE_WORD[p], added: p === 'idle' ? med('p50') : v.added_p50, idle: p === 'idle',
                    p95: p === 'idle' ? null : med('added_p95'), p99: med('added_p99'), loss: med('loss_pct'),
                    down: v.down_mbit, up: v.up_mbit, rpm: v.rpm || null});
            });
            h += '<p class="sa-note">Round trips per minute = 60 000 / loaded round trip; derived from this probe, not Apple\'s networkQuality test.</p>';
        } else if (r.state === 'failed' || r.state === 'cancelled') {
            h += '<div class="alert alert-warning" role="status">The test ' + (r.state === 'cancelled' ? 'was cancelled' : 'failed: ' + esc(r.error || '')) + '.</div>';
        }
        $('#sa-bb-result').html(h);
        if (phaseRows) {
            const num = function (f) { return function (v) { return v === null || v === undefined ? '–' : f(v); }; };
            SA.table('#sa-bb-phases', [
                {title: 'Phase', field: 'phase', sortField: 'idx'},
                {title: 'Added delay (median)', field: 'added', type: 'num', fmt: function (v, row) { return (row.idle ? 'baseline ' : '') + ms(v); }},
                {title: 'Added, 95th / 99th percentile', field: 'p95', type: 'num', wide: true, fmt: function (v, row) { return row.idle ? '–' : ms(v) + ' / ' + ms(row.p99); }},
                {title: 'Loss', field: 'loss', type: 'num', wide: true, fmt: num(function (v) { return v + ' %'; })},
                {title: 'Download', field: 'down', type: 'num', fmt: function (v) { return mbit(v); }},
                {title: 'Upload', field: 'up', type: 'num', fmt: function (v) { return mbit(v); }},
                {title: 'Round trips per minute', field: 'rpm', type: 'num', wide: true, fmt: num(function (v) { return v.toLocaleString(); })}
            ], phaseRows, {sort: ['phase', 'asc'], label: 'Phases of this test', empty: 'No phase finished.'});
        }
        if (r.samples) { draw(r); }
    }
    function progress(r) {
        const running = r.state === 'running' || r.state === 'starting';
        $('#sa-bb-start').prop('disabled', running);
        $('#sa-bb-cancel').toggle(running);
        if (running) {
            const pct = r.total ? Math.min(100, Math.round(100 * (r.elapsed || 0) / r.total)) : 0;
            $('#sa-bb-progress').show().find('.progress-bar').css('width', pct + '%').attr('aria-valuenow', pct);
            $('#sa-bb-phase').text((PHASE_WORD[r.phase] || 'Starting') + ' · ' + (r.elapsed || 0) + ' of ' + (r.total || '?') + ' s');
            if (r.samples) { draw(r); }
        } else {
            $('#sa-bb-progress').hide();
        }
        return running;
    }
    function poll() {
        ajaxGet('/api/shaperinsight/bufferbloat/status', {}, function (r) {
            r = SA.unescapeApi(r);
            if (progress(r)) {
                pollTimer = setTimeout(poll, 1000);
            } else {
                pollTimer = null;
                loadHistory(true);
            }
        });
    }
    const HIST_COLS = [
        {title: 'Finished', field: 'finished', type: 'num', fmt: function (v) { return new Date(v * 1000).toLocaleString(); }},
        {title: 'Grade', field: 'grade', type: 'num', fmt: function (v, row) { return '<span class="sa-g ' + gradeClass(row.gradeTxt) + '">' + esc(row.gradeTxt) + '</span> ' + ms(v); }, html: true},
        {title: 'Shaped link', field: 'shaped'}, {title: 'Label', field: 'label'},
        {title: 'Show', field: 'show', type: 'html', sort: false}];
    function loadHistory(showFirst) {
        ajaxGet('/api/shaperinsight/bufferbloat/results', {}, function (d) {
            d = SA.unescapeApi(d);
            if (!d || !Array.isArray(d.results)) {
                // core's ajaxGet hands {} to the callback on any error: say so, not "no results"
                SA.table('#sa-bb-history', HIST_COLS, [], {empty: 'Not available: the saved results could not be read.'});
                return;
            }
            const res = d.results;
            SA.table('#sa-bb-history', HIST_COLS, res.map(function (r, i) {
                return {finished: r.finished, grade: r.added_worst, gradeTxt: r.grade, shaped: (r.shaped ? 'yes' : 'no') + ' (' + r.interface + ')',
                    label: (r.settings || {}).label || '',
                    show: '<button type="button" class="btn btn-default btn-xs sa-bb-show" data-i="' + i + '">Show</button>'};
            }), {sort: ['finished', 'desc'], label: 'Saved results', empty: 'No results yet.'});
            $('#sa-bb-history').data('results', res);
            /* data estimate for the confirm dialog, from the last result's rates */
            const last = res[0];
            $('#sa-bb-start').data('estimate', last && last.summary ? (function () {
                const s = last.summary;
                const ps = (last.settings || {}).phase_seconds || 15;
                const mbits = ((s.download || {}).down_mbit || 0) + ((s.upload || {}).up_mbit || 0) +
                    ((s.both || {}).down_mbit || 0) + ((s.both || {}).up_mbit || 0);
                return Math.round(mbits * ps / 8) + ' MB';
            })() : null);
            if (showFirst && res.length) { render(res[0]); }
            document.body.setAttribute('data-sa-ready', '1');
        });
    }
    $(document).on('click', '.sa-bb-show', function () {
        render($('#sa-bb-history').data('results')[$(this).attr('data-i')]);
        document.getElementById('sa-bb-result').scrollIntoView();
    });
    $('#sa-bb-save').click(function () {
        saveFormToEndpoint('/api/shaperinsight/settings/set', 'frm_bufferbloat', function () {
            $('#sa-bb-saved').show().delay(3000).fadeOut();
        }, true);
    });
    $('#sa-bb-start').click(function () {
        const est = $(this).data('estimate');
        stdDialogConfirm('Run the bufferbloat test?',
            'For about ' + (25 + 3 * parseInt($('#shaperinsight\\.bufferbloat\\.phase_seconds').val() || 15)) + ' seconds this firewall fills the ' +
            'internet line in both directions; everyone on it will notice. Data used: ' + (est ? 'about ' + est + ' (from the last result)' :
            'unknown until the first run (set a cap to bound it)') + '. Save the settings first if you changed them.',
            'Run the test', 'Cancel', function () {
                ajaxCall('/api/shaperinsight/bufferbloat/start', {}, function (r) {
                    if (r.status === 'started') {
                        if (pollTimer === null) { poll(); }
                    } else {
                        $('#sa-bb-result').html('<div class="alert alert-warning" role="status">' + esc(r.message || 'The test did not start.') + '</div>');
                    }
                });
            });
    });
    $('#sa-bb-cancel').click(function () { ajaxCall('/api/shaperinsight/bufferbloat/cancel', {}, function () {}); });
    poll();
});
</script>

<section class="page-content-main">
<div class="content-box sa-intro">
    <p>Measures what the shaper is for: how much longer a <span class="sa-chip-slot" data-term="latency-delay">round trip</span>
    gets while the line is full (<span class="sa-chip-slot" data-term="bufferbloat">bufferbloat</span>). The firewall first measures
    the delay with the line idle, then fills it with downloads, with uploads, and with both, and measures again each time.
    The <b>added</b> delay is the grade, not the speed
    (<span class="sa-chip-slot" data-term="capacity-vs-bandwidth">capacity vs bandwidth</span>).</p>
    <p class="sa-note">Run it when nobody needs the line for a minute. It uses the third parties named under Settings
    (by default Cloudflare's speed test for the load, and Cloudflare and Quad9, plus your WAN gateway, for the round trips); change them
    to servers of your own if you prefer.</p>
    <button type="button" class="btn btn-primary" id="sa-bb-start"><i class="fa fa-play fa-fw" aria-hidden="true"></i> Run the test</button>
    <button type="button" class="btn btn-default" id="sa-bb-cancel" style="display:none">Cancel</button>
    <div id="sa-bb-progress" style="display:none; margin-top: 12px">
        <div class="progress"><div class="progress-bar" role="progressbar" aria-label="Test progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"></div></div>
        <span id="sa-bb-phase" role="status"></span>
    </div>
</div>
<div class="content-box sa-intro">
    <h2 class="sa-h">Result</h2>
    <div id="sa-bb-result" aria-live="polite"></div>
    <div class="sa-bb-charts">
        <div class="sa-bb-chart sa-bb-top"><canvas id="sa-bb-rate" role="img" aria-label="Download and upload rate per second, by phase"></canvas></div>
        <div class="sa-bb-chart"><canvas id="sa-bb-lat" role="img" aria-label="Round trip per target per second, by phase"></canvas></div>
        <div class="sa-bb-chart"><canvas id="sa-bb-cake" role="img" aria-label="CAKE average wait and drops per class per second, by phase"></canvas></div>
    </div>
    <p class="sa-note">Read top to bottom at the same second: when a direction fills the line (top), the round trip (middle) grows by
    as much as packets wait in the queue (bottom). With CAKE the wait stays short and drops rise instead: CAKE drops a few packets so
    the senders slow down, rather than letting the queue grow.</p>
</div>
<div class="content-box sa-intro">
    <h2 class="sa-h">History</h2>
    <div id="sa-bb-history"></div>
</div>
<div class="content-box">
    <details><summary class="sa-intro">Settings</summary>
    {{ partial('layout_partials/base_form', ['fields': formBufferbloat, 'id': 'frm_bufferbloat']) }}
    <div class="sa-intro"><button type="button" class="btn btn-primary" id="sa-bb-save">Save</button>
        <span id="sa-bb-saved" style="display:none" role="status">Saved.</span></div>
    </details>
</div>
</section>
