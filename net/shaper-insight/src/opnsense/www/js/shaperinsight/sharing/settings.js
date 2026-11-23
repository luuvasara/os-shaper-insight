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
 * Sharing page, top: the sampler's status line, the window and "hosts drawn" controls, and the
 * "Recording settings" card: the insight level (Full / Light, or Custom when the settings were changed
 * one by one), the settings it sets, and a suggestion from cheap facts about this firewall with the
 * thresholds it used. The thresholds and the presets come from the API (sampler.py ASSESS_LIMITS and
 * LEVELS), never from this file, so the page and docs/INSIGHT-LEVELS.md cannot disagree. Nothing is
 * changed without a click.
 */
(function () {
    'use strict';
    const SH = window.SAsh;
    let settings = null, assess = null;

    /* ------------------------------------------------------------------ the status line */
    window.SAsharingTop = {
        status: function (d) {
            const run = d.state === 'running' ? SA.badge('ok', 'Recording') : d.state === 'restarting' ? SA.badge('warn', 'Restarting') :
                d.enabled === null ? SA.badge('warn', 'Settings unreadable just now') :
                SA.badge(d.enabled ? 'warn' : 'info', d.enabled ? 'Not running' : 'Switched off');
            const c = d.cost || {};
            const wd = d.watchdog || {};
            const st = d.hosts_store || {};
            const over = Object.keys(c.hosts_over || {});
            $('#sa-sampler').html(run + ' ' + SA.esc((c.cpu_ms_per_sample !== undefined ?
                'Every 10 s, about ' + c.cpu_ms_per_sample + ' ms of CPU per sample (' + (c.cpu_ms_per_sample / 100).toFixed(2) + ' % of one core), ' +
                SH.num(c.states) + ' pf states' + (c.hosts_every_s && c.hosts_every_s > 10 ? ' (hosts read every ' + c.hosts_every_s + ' s)' : '') + ', ' +
                Math.round((c.rss_kb || 0) / 1024) + ' MB memory now (' + Math.round((c.rss_peak_kb || 0) / 1024) + ' MB peak), ' +
                (Math.round((c.rrd_bytes || 0) / 1e5) / 10) + ' MB of RRD files, ' + (Math.round((c.ring_bytes || 0) / 1e4) / 100) + ' MB host list (' +
                (st.mode === 'every' ? 'every host' : 'the busiest ' + (st.top || 16) + ' per sample') + ').' : '') +
                (over.length ? ' Pipe ' + over.join(', ') + ' has more than ' + SH.num(st.ceiling || 2000) + ' hosts: it keeps the busiest ' + (st.top || 16) + ' and sums the rest.' : '') +
                (c.hosts_paused ? ' Per-host view paused: ' + c.hosts_paused + ' (the pipe history goes on).' : '') +
                (wd.count ? ' The watchdog found the sampler stopped and started it ' + wd.count + ' × (last ' + new Date(wd.last * 1000).toLocaleString() + ').' : '')));
            $('#sa-toggle').text(d.enabled ? 'Switch the sampler off' : 'Switch the sampler on').data('on', d.enabled);
            $('#sa-shown').val(String(d.hosts_shown || 8));
            $('#sa-hosts-note').text(d.window > d.hosts_keep_s ? 'Hosts are kept for 6 hours; this window shows the pipes and classes only.' :
                (d.hosts_since ? 'Hosts recorded since ' + new Date(d.hosts_since * 1000).toLocaleString() + '.' : ''));
            const na = ['na-up', 'na-down'].filter(function (k) { return d.hosts && d.hosts[k] && d.hosts[k].length; });
            $('#sa-na').html(na.length ? '<p class="sa-meta">Traffic not attributed to a pipe (the firewall itself on an interface without shaping, or IPv6 without NAT when several links are shaped): ' +
                na.map(function (k) {
                    return (k === 'na-up' ? 'upload' : 'download') + ': ' + d.hosts[k].map(function (h) { return SA.esc(SH.hostName(h[0])); }).join(', ');
                }).join('; ') + '</p>' : '');
        },
        failed: function (e) {
            $('#sa-sampler').html(SA.badge('bad', 'Not available') + ' ' + SA.esc(e.message) +
                ' <span class="sa-meta">This page needs the full Firewall: Shaper: Shaper Insight privilege: it lists local addresses with their traffic.</span>');
        }
    };

    /* ------------------------------------------------------------------ recording settings */
    function opt(v) {
        if (v && typeof v === 'object') {
            const k = Object.keys(v).find(function (x) { return v[x].selected; });
            return k === undefined ? '' : k;
        }
        return String(v);
    }
    function current() {
        const g = (settings || {}).general || {};
        return {store_all_hosts: String(g.store_all_hosts), hosts_interval: opt(g.hosts_interval), poll_seconds: String(g.poll_seconds)};
    }
    /* the page never refreshes faster than every 10 s, so 5 and 10 are the same choice */
    function pollOf(v) { return String(Math.max(10, parseInt(v, 10) || 10)); }
    function levelOf(cur) {
        const L = (assess || {}).levels;
        if (!L) { return null; }
        return Object.keys(L).find(function (k) {
            return L[k].store_all_hosts === cur.store_all_hosts && L[k].hosts_interval === cur.hosts_interval && pollOf(L[k].poll_seconds) === pollOf(cur.poll_seconds);
        }) || 'custom';
    }
    const NAMES = {full: 'Full', light: 'Light', custom: 'Custom'};
    const ROWS = {
        cores: ['CPU cores', function (v) { return SH.num(v); }],
        free_ram_mb: ['Available RAM', function (v) { return SH.num(v) + ' MB'; }],
        hosts: ['Hosts on the busiest pipe now', function (v) { return SH.num(v); }],
        pf_states: ['pf states', function (v) { return SH.num(v); }],
        cpu_ms_per_sample: ['Sampler CPU per 10 s step (measured)', function (v) { return v + ' ms'; }],
        ring_mb: ['Host list (6 h) with every host, estimated', function (v) { return v + ' MB'; }],
        ring_share_of_free: ['That list as a share of available RAM', function (v) { return SH.pct(v); }]
    };
    function save(patch, msg) {
        $('#sa-rec-msg').text('Saving...');
        return new Promise(function (ok) {
            ajaxCall('/api/shaperinsight/settings/set', {shaperinsight: {general: patch}}, function (r) {
                const bad = r && r.result !== 'saved';
                $('#sa-rec-msg').text(bad ? 'Not saved: ' + JSON.stringify(r.validations || r) : msg || 'Saved. The sampler uses it from its next sample (10 s).');
                ok();
            });
        }).then(function () { return loadSettings(); }).then(function () {
            window.SAviews.restart(parseInt(current().poll_seconds, 10));
            window.SAviews.refresh();
        });
    }
    function draw() {
        const box = document.getElementById('sa-rec-body');
        if (!box || !settings) { return; }
        const cur = current();
        const lvl = levelOf(cur);
        const A = assess || {};
        const sum = document.getElementById('sa-rec-level');
        sum.textContent = lvl ? NAMES[lvl] + ' insight' : '';
        const sug = A.suggest;
        box.innerHTML =
            '<div class="sa-rec-row"><label for="sa-level">Insight level</label> <select id="sa-level" class="form-control sa-inline-select"' + (lvl ? '' : ' disabled') + '>' +
            ['full', 'light'].map(function (k) { return '<option value="' + k + '"' + (lvl === k ? ' selected' : '') + '>' + NAMES[k] + '</option>'; }).join('') +
            (lvl === 'custom' ? '<option value="custom" selected disabled>Custom (changed one by one)</option>' : '') + '</select>' +
            '<span class="sa-meta">Full records every host every 10 s and refreshes this page every 10 s; Light keeps the busiest ' + (A.levels ? '16' : '16') +
            ' hosts, reads them every 30 s and refreshes every 30 s. Ex.: run Full while you learn how the line is shared, switch to Light once you are content.</span></div>' +
            '<div class="sa-rec-row"><input type="checkbox" id="sa-storeall"' + (cur.store_all_hosts === '1' ? ' checked' : '') + '> <label for="sa-storeall">Store every host’s history</label>' +
            '<span class="sa-meta">Every host for 6 h (up to ' + SH.num(A.ceiling || 2000) + ' a pipe), so the horizon rows and the time grid list them all; off keeps the busiest 16 per sample. ' +
            'The list lives in RAM (/var/run): ' + (A.ring_bytes_now !== undefined && A.ring_bytes_now !== null ? (A.ring_bytes_now / 1e6).toFixed(2) + ' MB now' : 'size unknown') +
            (A.rows ? ', about ' + (A.rows.find(function (r) { return r.key === 'ring_mb'; }) || {}).value + ' MB with every host at today’s host count' : '') + '.</span></div>' +
            '<div class="sa-rec-row"><label for="sa-hint">Read the hosts every</label> <select id="sa-hint" class="form-control sa-inline-select">' +
            ['10', '30', '60'].map(function (s) { return '<option value="' + s + '"' + (cur.hosts_interval === s ? ' selected' : '') + '>' + s + ' s</option>'; }).join('') + '</select>' +
            '<span class="sa-meta">Each read lists the pf state table (' + (A.rows ? SH.num((A.rows.find(function (r) { return r.key === 'pf_states'; }) || {}).value || 0) + ' states now' : '') +
            '). Ex.: 30 s cuts that work to a third; a short burst then shows as a 30 s average. The sampler also slows down on its own when a read is costly.</span></div>' +
            '<div class="sa-rec-row"><label for="sa-poll">Refresh this page every</label> <select id="sa-poll" class="form-control sa-inline-select">' +
            ['10', '30', '60'].map(function (s) { return '<option value="' + s + '"' + (pollOf(cur.poll_seconds) === s ? ' selected' : '') + '>' + s + ' s</option>'; }).join('') + '</select>' +
            '<span class="sa-meta">Each refresh makes the firewall read the history once, for every open page. Ex.: 30 s on a slow box with the page left open.</span></div>' +
            '<p class="sa-meta" id="sa-rec-msg" aria-live="polite"></p>' +
            '<div class="sa-assess"><h3>Suggested for this firewall: ' + (sug ? NAMES[sug] : 'unknown') + '</h3>' +
            (A.rows ? '<p class="sa-meta">From cheap readings only (sysctl, the sampler’s own measurements, the newest host list); ' + SA.esc(A.model || 'CPU model unknown') + ', ' +
                SH.num(A.ram_mb || 0) + ' MB RAM. ' + (sug === 'light' ? 'Light is suggested because of the rows marked “decided”.' : 'Every row is within the Full limits.') +
                ' The limits are measured on a test firewall (docs/INSIGHT-LEVELS.md). Nothing changes until you click.</p>' +
                '<div id="sa-assess-tbl"></div>' +
                (sug && lvl !== sug ? '<button type="button" class="btn btn-primary btn-sm" id="sa-apply-level" data-level="' + sug + '">Use ' + NAMES[sug] + ' (suggested)</button>' :
                    '<p class="sa-meta">' + (lvl === sug ? 'This firewall already uses the suggested level.' : '') + '</p>') :
                '<p class="sa-meta">The assessment could not be read just now.</p>') + '</div>';
        if (A.rows) {
            SA.table('#sa-assess-tbl', [
                {title: 'Resource', field: 'name', sort: false},
                {title: 'This firewall', field: 'value', sort: false},
                {title: 'Full suggested', field: 'full', sort: false},
                {title: 'Light suggested', field: 'light', sort: false},
                {title: 'Decided the suggestion', field: 'decided', type: 'html', sort: false}
            ], A.rows.map(function (r) {
                const def = ROWS[r.key] || [r.key, function (v) { return String(v); }];
                const fmt = def[1];
                const min = r.full_min !== null && r.full_min !== undefined;
                return {name: def[0], value: r.value === null || r.value === undefined ? 'unknown' : fmt(r.value),
                    full: min ? 'from ' + fmt(r.full_min) : 'up to ' + fmt(r.full_max), light: min ? 'below ' + fmt(r.full_min) : 'above ' + fmt(r.full_max),
                    decided: r.light ? '<b>▲ yes</b>' : 'no'};
            }), {label: 'This firewall against the Full and Light limits'});
        }
    }
    function loadSettings() {
        return SA.get('/api/shaperinsight/settings/get').then(function (r) { settings = r.shaperinsight || {}; draw(); }).catch(function () {});
    }
    function loadAssess() {
        return SA.get('/api/shaperinsight/sampler/assess').then(function (r) { assess = r; draw(); }).catch(function () { assess = null; draw(); });
    }
    function applyLevel(k) {
        const p = ((assess || {}).levels || {})[k];
        if (p) { save(Object.assign({}, p), NAMES[k] + ' insight saved. The sampler uses it from its next sample (10 s).'); }
    }
    document.addEventListener('change', function (e) {
        if (e.target.id === 'sa-level') { applyLevel(e.target.value); }
        if (e.target.id === 'sa-storeall') { save({store_all_hosts: e.target.checked ? '1' : '0'}); }
        if (e.target.id === 'sa-hint') { save({hosts_interval: e.target.value}); }
        if (e.target.id === 'sa-poll') { save({poll_seconds: e.target.value}); }
    });
    document.addEventListener('click', function (e) {
        if (e.target.id === 'sa-apply-level') { applyLevel(e.target.getAttribute('data-level')); }
    });

    $(document).ready(function () {
        $('#sa-window').on('change', function () { window.SAviews.setWindow(parseInt($(this).val(), 10)); });
        $('#sa-toggle').on('click', function () {
            const on = !$(this).data('on');
            ajaxCall('/api/shaperinsight/settings/set', {shaperinsight: {general: {sampler: on ? '1' : '0'}}}, function () {
                ajaxCall('/api/shaperinsight/sampler/' + (on ? 'start' : 'stop'), {}, function () { window.SAviews.refresh(); });
            });
        });
        $('#sa-shown').on('change', function () {
            ajaxCall('/api/shaperinsight/settings/set', {shaperinsight: {general: {hosts_shown: $(this).val()}}}, function () { window.SAviews.refresh(); });
        });
        if (window.Chart) {
            Chart.defaults.color = SH.fg();
            Chart.defaults.animation = false;
        }
        SA.loadGlossary().then(function () {
            return Promise.all([loadSettings(), loadAssess()]);
        }).finally(function () {
            window.SAviews.start(parseInt(current().poll_seconds, 10) || 10);
        });
        /* the suggestion's numbers move slowly: once a minute is enough */
        setInterval(function () {
            const box = document.getElementById('sa-rec-body');
            if (document.visibilityState === 'visible' && !(box && box.contains(document.activeElement))) { loadAssess(); }
        }, 60000);
    });
}());
