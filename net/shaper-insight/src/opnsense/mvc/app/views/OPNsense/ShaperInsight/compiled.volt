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
<script src="{{ cache_safe('/ui/js/shaperinsight/common.js') }}"></script>
<script>
'use strict';
$(document).ready(function () {
    let data = null;

    function redacted() {
        return $('#sa-redact').is(':checked');
    }

    function panelText(p) {
        return redacted() ? p.redacted : p.text;
    }

    function render() {
        if (!data || !data.panels) {
            $('#sa-panels').html('<div class="alert alert-danger" role="alert"><i class="fa fa-times-circle" aria-hidden="true"></i> ' +
                '{{ lang._("Could not read the compiled configuration (configd did not answer). Try again in a moment.") }}</div>');
            return;
        }
        const r = data.redactions;
        const hidden = r.v4 + r.v6 + r.mac + r.duid + r.host + (r.descr || 0);
        $('#sa-redact-info').text(redacted() ?
            ('{{ lang._("Hidden") }}: ' + hidden + ' (' + r.v4 + ' public IPv4, ' + r.v6 + ' public IPv6, ' + (r.mac + r.duid) +
             ' MAC/DUID, ' + r.host + ' host names, ' + (r.descr || 0) + ' {{ lang._("descriptions and comments, which can name people or places") }}). ' + r.private_kept + ' {{ lang._("private addresses kept, because they identify nobody and help others debug.") }}') :
            '{{ lang._("Showing everything, including public addresses. Turn hiding back on before you post.") }}');
        $('#sa-panels').html(data.panels.map(function (p) {
            const text = panelText(p) || '{{ lang._("(empty)") }}';
            return '<section class="content-box sa-section sa-panel" aria-labelledby="sa-h-' + p.id + '">' +
                '<h2 id="sa-h-' + p.id + '">' + SA.esc(p.title) + '</h2>' +
                '<p class="sa-lead">' + SA.esc(p.help) + '</p>' +
                '<div class="sa-panel-tools"><button type="button" class="btn btn-default btn-xs sa-copy" data-id="' + p.id + '">' +
                '<i class="fa fa-clipboard" aria-hidden="true"></i> {{ lang._("Copy") }}</button></div>' +
                '<pre tabindex="0" aria-label="' + SA.esc(p.title) + '">' + SA.esc(text) + '</pre></section>';
        }).join(''));
        document.body.setAttribute('data-sa-ready', '1');
    }

    function forumPost() {
        const v = data.panels.find(function (p) { return p.id === 'versions'; });
        let out = 'Shaper configuration' + (redacted() ? ' (public addresses, MACs, host names and descriptions hidden)' : '') + '\n\n';
        data.panels.forEach(function (p) {
            if (p === v) {
                out += panelText(p).split('\n').map(function (l) { return '- ' + l; }).join('\n') + '\n\n';
                return;
            }
            out += '**' + p.title + '**\n```\n' + (panelText(p) || '(empty)') + '\n```\n\n';
        });
        return out;
    }

    function copy(text, btn) {
        const done = function () {
            const $b = $(btn), old = $b.html();
            $b.html('<i class="fa fa-check" aria-hidden="true"></i> {{ lang._("Copied") }}');
            setTimeout(function () { $b.html(old); }, 2000);
        };
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text).then(done, function () { fallback(text); done(); });
        } else {
            fallback(text);
            done();
        }
    }

    function fallback(text) {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.top = '-1000px';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
    }

    function load() {
        $('#sa-reload i').addClass('fa-spin');
        return SA.get('/api/shaperinsight/compiled/show').then(function (d) { data = d; render(); },
            function () { data = null; render(); }).finally(function () { $('#sa-reload i').removeClass('fa-spin'); });
    }

    $('#sa-redact').on('change', render);
    $('#sa-reload').on('click', load);
    $(document).on('click', '.sa-copy', function () {
        const id = $(this).data('id');
        const p = data.panels.find(function (x) { return x.id === id; });
        copy(panelText(p), this);
    });
    $('#sa-copy-forum').on('click', function () { copy(forumPost(), this); });
    $('#sa-download').on('click', function () {
        const blob = new Blob([forumPost()], {type: 'text/plain'});
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'shaper-config' + (redacted() ? '-redacted' : '') + '.txt';
        a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    });
    SA.loadGlossary().then(load);
});
</script>

<div class="content-box sa-section">
    <h1 class="sr-only">{{ lang._('Compiled shaper configuration') }}</h1>
    <p class="sa-lead">{{ lang._('Everything the shaper generated and what the kernel has loaded, as text you can paste into a forum post or bug report. Read-only: nothing here changes the firewall.') }}</p>
    <div class="sa-toolbar">
        <div class="checkbox" style="margin:0">
            <label for="sa-redact"><input type="checkbox" id="sa-redact" checked> {{ lang._('Hide public addresses, MAC addresses, host names and descriptions') }}</label>
        </div>
        <button type="button" id="sa-copy-forum" class="btn btn-primary btn-sm"><i class="fa fa-clipboard" aria-hidden="true"></i> {{ lang._('Copy all as forum post') }}</button>
        <button type="button" id="sa-download" class="btn btn-default btn-sm"><i class="fa fa-download" aria-hidden="true"></i> {{ lang._('Download .txt') }}</button>
        <button type="button" id="sa-reload" class="btn btn-default btn-sm"><i class="fa fa-refresh" aria-hidden="true"></i> {{ lang._('Read again') }}</button>
    </div>
    <p id="sa-redact-info" class="sa-meta" aria-live="polite"></p>
</div>
<div id="sa-panels"><div class="content-box sa-section"><i class="fa fa-spinner fa-pulse" aria-hidden="true"></i> {{ lang._('Reading the configuration...') }}</div></div>
