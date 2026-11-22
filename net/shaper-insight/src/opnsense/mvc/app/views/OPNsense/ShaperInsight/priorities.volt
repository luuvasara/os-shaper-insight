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
<script>
'use strict';
$(document).ready(function () {
    const esc = SA.esc;
    const grid = $("#{{ formGridPriority['table_id'] }}").UIBootgrid({
        search: '/api/shaperinsight/priorities/search_item/',
        get: '/api/shaperinsight/priorities/get_item/',
        set: '/api/shaperinsight/priorities/set_item/',
        add: '/api/shaperinsight/priorities/add_item/',
        del: '/api/shaperinsight/priorities/del_item/',
        toggle: '/api/shaperinsight/priorities/toggle_item/'
    });
    $("#reconfigureAct").SimpleActionButton({onAction: function () { loadStatus(); } });

    let chosen = {};
    function pickRow(target, label, sub) {
        const has = chosen[target.toLowerCase()];
        return '<tr><td><b>' + esc(label) + '</b><br><span class="sa-note">' + esc(sub) + '</span></td><td>' +
            (has ? '<span class="sa-pick-has">' + esc(has === 'interactive' ? 'Interactive' : 'Background') + '</span>' :
            ['interactive', 'background'].map(function (c) {
                return '<button type="button" class="btn btn-default btn-xs sa-pick" data-target="' + esc(target) +
                    '" data-class="' + c + '" data-descr="' + esc(label) + '">' +
                    (c === 'interactive' ? 'Interactive' : 'Background') + '</button>';
            }).join(' ')) + '</td></tr>';
    }
    function loadDevices() {
        ajaxGet('/api/shaperinsight/priorities/search_item/', {rowCount: -1}, function (rows) {
            if (!rows || !Array.isArray(rows.rows)) {
                $('#sa-devices tbody').html('<tr><td colspan="2">' + SA.badge('bad', 'Not available') +
                    ' The saved priorities could not be read, so the list cannot show which devices have one.</td></tr>');
                return;
            }
            chosen = {};
            (rows.rows || []).forEach(function (r) {
                chosen[String(r.target).toLowerCase()] = r['class'];
            });
            SA.get('/api/shaperinsight/devices/list').then(function (d) {
                if (!d || !d.devices) { throw new Error('no data'); }
                const dev = (d.devices || []).map(function (x) {
                    /* a MAC follows the device to a new address; without one, the address */
                    const target = x.mac || x.address;
                    return pickRow(target, x.hostname || x.address, x.address + (x.mac ? ' · ' + x.mac : '') + ' · ' +
                        x.sources.join(', ') + (x.static ? '' : (x.mac ? ' (address may change; picked by MAC)' :
                            ' (no MAC known: picked by this address, which may change)')));
                });
                $('#sa-devices tbody').html(dev.join('') ||
                    '<tr><td colspan="2">No DHCP leases or reservations found (Kea or Dnsmasq). Use + above to enter a MAC or IP address.</td></tr>');
                const al = (d.aliases || []).map(function (x) {
                    return pickRow(x.name, x.name, (x.description ? x.description + ' · ' : '') + x.type + ': ' + x.content);
                });
                $('#sa-aliases tbody').html(al.join('') || '<tr><td colspan="2">No host or network aliases.</td></tr>');
            }).catch(function (e) {
                // an API error is an error, not "no devices" (core's ajaxGet hands {} to the callback)
                $('#sa-devices tbody').html('<tr><td colspan="2">' + SA.badge('bad', 'Not available') +
                    ' The device list could not be read (' + esc(e.message) + '). Use + above to enter a MAC or IP address.</td></tr>');
            });
        });
    }
    $(document).on('click', '.sa-pick', function () {
        const b = $(this);
        ajaxCall('/api/shaperinsight/priorities/add_item/', {priority: {enabled: '1', target: b.attr('data-target'),
            'class': b.attr('data-class'), description: b.attr('data-descr')} }, function (r) {
            if (r.result === 'saved') {
                grid.bootgrid('reload');
                loadDevices();
                $('#sa-apply-hint').show();
            } else {
                $('#sa-pick-error').text(Object.values(r.validations || {}).join(' ')).show();
            }
        });
    });
    grid.on('loaded.rs.jquery.bootgrid', function () { loadDevices(); });

    function loadStatus() {
        SA.get('/api/shaperinsight/priorities/status').then(function (d) {
            if (!d || !d.classes) { throw new Error((d && d.message) || 'no data'); }
            const rules = {};
            (d.rules || []).forEach(function (r) { rules[r.number] = r; });
            let html = '';
            if (d.last && d.last.status !== 'ok') {
                html += '<div class="alert alert-danger" role="alert"><b>The last ' + esc(d.last.command || 'load') +
                    ' of the marks failed</b>' + (d.last.kept_previous ? ' (the previous marks stay loaded)' : '') + ': ' +
                    (d.last.errors || []).map(esc).join('; ') + '</div>';
            }
            if (!(d.links || []).length) {
                html += '<div class="alert alert-warning" role="status">No internet link has a CAKE pipe that sorts by priority ' +
                    '(diffserv4), so marks would change nothing yet. Run <a href="/ui/shaperinsight/wizard">setup</a> again: ' +
                    'with priorities saved, its plan switches the CAKE pipes to diffserv4.</div>';
            } else {
                html += '<p>Marked on: <b>' + d.links.map(esc).join(', ') + '</b>. The marks only choose a class inside the ' +
                    'same shared pipe: no extra pipe, nothing reserved.</p>';
            }
            html += '<table class="table table-condensed sa-stack" id="sa-prio-classes"><thead><tr><th scope="col">Priority</th><th scope="col">What it does</th>' +
                '<th scope="col">Devices now</th><th scope="col">Marked so far</th></tr></thead><tbody>';
            (d.classes || []).forEach(function (c) {
                const mine = (d.rules || []).filter(function (r) { return r.rule.indexOf('setdscp ' + c.dscp + ' ') === 0; });
                const bytes = mine.reduce(function (a, r) { return a + r.bytes; }, 0);
                html += '<tr><td><b>' + esc(c['class'] === 'interactive' ? 'Interactive' : 'Background') + '</b><br>' +
                    '<span class="sa-note">CAKE ' + esc(c.tin) + ' class, DSCP ' + esc(c.dscp.toUpperCase()) + '</span></td>' +
                    '<td>' + esc(c.what) + '</td><td>' + (c.members.length ? c.members.map(function (m) {
                        if (m.port) {
                            return esc(m.target) + ': every device, this port on either end';
                        }
                        return esc(m.target) + ': ' + (m.addresses.length ? m.addresses.map(esc).join(', ') :
                            '<i class="fa fa-exclamation-triangle" aria-hidden="true"></i> no address found yet (device offline?)') +
                            (m.warning ? '<br>' + SA.badge('warn', 'Shared MAC') + ' <span class="sa-note">' + esc(m.warning) + '</span>' : '');
                    }).join('<br>') : '<span class="sa-note">none</span>') + '</td>' +
                    '<td>' + (mine.length ? (bytes / 1e6).toFixed(1) + ' MB' : '<span class="sa-note">not loaded</span>') + '</td></tr>';
            });
            html += '</tbody></table><p class="sa-note">Addresses are looked up again every minute (DHCP leases, ARP, IPv6 neighbours).</p>';
            $('#sa-prio-status').html(html);
            SA.labelCells('#sa-prio-classes');
            document.body.setAttribute('data-sa-ready', '1');
        }).catch(function (e) {
            $('#sa-prio-status').html('<div class="alert alert-danger" role="alert">' + SA.badge('bad', 'Not available') +
                ' The status could not be read (' + esc(e.message) + '), so nothing is known about the marks right now.</div>');
            document.body.setAttribute('data-sa-ready', '1');
        });
    }
    loadStatus();
    // after Apply: show what loaded, including a failure
    $(document).ajaxComplete(function (ev, xhr, opts) {
        if (opts.url && opts.url.indexOf('/api/shaperinsight/priorities/apply') === 0) { loadStatus(); }
    });
});
</script>

<section class="page-content-main">
<div class="content-box sa-intro">
    <p>Give chosen devices a priority on the internet line. A device is not given its own speed limit: its traffic is
    marked (<span class="sa-chip-slot" data-term="dscp">DSCP</span>), and CAKE sorts it into one of its
    <span class="sa-chip-slot" data-term="tin">tins</span> (classes) inside the same shared pipe. Unused capacity is always shared,
    so nothing is reserved or wasted.</p>
    <p>A priority is <b>per device</b>: picked by its MAC address, it covers every address the device uses
    (IPv4 and all its IPv6 addresses), looked up again every minute. CAKE's fair sharing is different: it is per
    address (<span class="sa-chip-slot" data-term="host-isolation">host isolation</span>), so a device that uses
    IPv4 and IPv6 at the same time gets one share for each.</p>
    <p>A priority can also be <b>per port</b>: <code>udp/51820</code> or <code>tcp+udp/6881-6889</code> marks
    that traffic from every device, in both directions. Background is generic: anything that should give way
    (backups, a VPN tunnel full of downloads, a file-sharing port, update servers by alias).</p>
    <p class="sa-note">Example: <b>Interactive</b> for the console and the work laptop keeps calls and games
    responsive while someone downloads; <b>Background</b> for the backup server makes it give way. Marking a heavy
    download as Interactive does not make it faster: Interactive has priority only up to half the line.</p>
</div>
<div class="content-box">
    <h2 class="sa-h">Priorities</h2>
    {{ partial('layout_partials/base_bootgrid_table', formGridPriority) }}
</div>
<div class="content-box sa-intro">
    <h2 class="sa-h">Pick from your network</h2>
    <p class="sa-note" id="sa-apply-hint" style="display:none" role="status">Added. Press Apply below to load the marks.</p>
    <p class="text-danger" id="sa-pick-error" style="display:none" role="alert"></p>
    <details open><summary>Devices (DHCP leases and reservations)</summary>
    <table class="table table-condensed" id="sa-devices"><thead><tr><th scope="col">Device</th><th scope="col">Add as</th></tr></thead><tbody></tbody></table>
    </details>
    <details><summary>Aliases (host and network)</summary>
    <table class="table table-condensed" id="sa-aliases"><thead><tr><th scope="col">Alias</th><th scope="col">Add as</th></tr></thead><tbody></tbody></table>
    </details>
</div>
<div class="content-box sa-intro">
    <h2 class="sa-h">Now loaded</h2>
    <div id="sa-prio-status" aria-live="polite"></div>
</div>
</section>
{{ partial('layout_partials/base_apply_button', {'data_endpoint': '/api/shaperinsight/priorities/apply'}) }}
{{ partial('layout_partials/base_dialog',['fields':formDialogPriority,'id':formGridPriority['edit_dialog_id'],'label':lang._('Edit priority')]) }}
