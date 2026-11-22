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
    SA.loadGlossary().then(function (g) {
        const terms = g.terms || {};
        const groups = terms._groups || [];
        const mixups = terms._mixups || [];
        const ids = Object.keys(terms).filter(function (k) { return k.charAt(0) !== '_'; });
        const name = function (id) { return terms[id] ? terms[id].name : id; };
        /* the 15 mix-ups first, always open */
        $('#sa-gl-mixups').html(mixups.map(function (m) {
            return '<li class="sa-gl-mix"><p class="sa-gl-people"><span class="sa-gl-lbl">People say</span> ' + esc(m.people) + '</p>' +
                '<p><span class="sa-gl-lbl">Say instead, and why</span> ' + esc(m.say) + '</p><p>' +
                m.terms.filter(function (t) { return terms[t]; }).map(function (t) {
                    return '<a class="sa-gl-chip" href="#t-' + esc(t) + '">' + esc(name(t)) + '</a>';
                }).join(' ') + '</p></li>';
        }).join(''));
        /* a sticky index of the groups, then every term as a card with its own anchor */
        $('#sa-gl-index').html(groups.map(function (gr) {
            return '<li><a href="#g-' + esc(gr.id) + '">' + esc(gr.title) + '</a></li>';
        }).join(''));
        $('#sa-gl-terms').html(groups.map(function (gr) {
            const mine = ids.filter(function (id) { return terms[id].group === gr.id; })
                .sort(function (a, b) { return name(a).localeCompare(name(b)); });
            return '<section class="sa-gl-group" id="g-' + esc(gr.id) + '"><h2 class="sa-h">' + esc(gr.title) + '</h2>' +
                mine.map(function (id) {
                    const t = terms[id];
                    return '<article class="sa-gl-term" id="t-' + esc(id) + '" data-text="' +
                        esc([t.name, t.plain, t.say, t.people, t.source_text].join(' ').toLowerCase()) + '">' +
                        '<h3>' + esc(t.name) + (t.note ? ' <span class="sa-note">(' + esc(t.note) + ')</span>' : '') +
                        ' <a class="sa-gl-anchor" href="#t-' + esc(id) + '" aria-label="Link to ' + esc(t.name) + '">#</a></h3>' +
                        SA.termBlocks(t) +
                        '<button type="button" class="btn btn-default btn-xs sa-gl-copy" data-id="' + esc(id) + '">Copy as text</button></article>';
                }).join('') + '</section>';
        }).join(''));
        $('#sa-gl-count').text(ids.length + ' terms');
        /* deep link from a chip: open at the term */
        if (location.hash && document.getElementById(location.hash.slice(1))) {
            document.getElementById(location.hash.slice(1)).scrollIntoView();
            $(location.hash).addClass('sa-gl-target');
        }
        document.body.setAttribute('data-sa-ready', '1');
    });
    $('#sa-gl-search').on('input', function () {
        const q = $(this).val().toLowerCase().trim();
        let shown = 0;
        $('.sa-gl-term').each(function () {
            const hit = !q || $(this).data('text').indexOf(q) >= 0;
            $(this).toggle(hit);
            shown += hit ? 1 : 0;
        });
        $('.sa-gl-group').each(function () { $(this).toggle($(this).find('.sa-gl-term:visible').length > 0); });
        $('#sa-gl-count').text(q ? shown + ' matching' : $('.sa-gl-term').length + ' terms');
    });
    $(document).on('click', '.sa-gl-copy', function () {
        const el = document.getElementById('t-' + $(this).data('id'));
        const text = $(el).find('h3').clone().children('a').remove().end().text().trim() + '\n' +
            $(el).find('dt').map(function () { return $(this).text() + ': ' + $(this).next('dd').text(); }).get().join('\n');
        const b = $(this);
        navigator.clipboard.writeText(text).then(function () { b.text('Copied'); setTimeout(function () { b.text('Copy as text'); }, 2000); });
    });
});
</script>

<section class="page-content-main">
<div class="content-box sa-intro">
    <p>The words of traffic management, each with the correct term, where it applies, an example, what people often say
    instead, and the source it comes from. The other pages link here from every underlined term.</p>
    <label for="sa-gl-search" class="sa-gl-lbl">Find a term</label>
    <input type="search" id="sa-gl-search" class="form-control" placeholder="ex. policing, DSCP, jitter" autocomplete="off">
    <span id="sa-gl-count" class="sa-note" role="status"></span>
</div>
<div class="content-box sa-intro">
    <h2 class="sa-h">The 15 most common mix-ups</h2>
    <ul class="sa-gl-mixups" id="sa-gl-mixups"></ul>
</div>
<div class="content-box sa-intro">
    <nav aria-label="Glossary groups"><ul class="sa-gl-index" id="sa-gl-index"></ul></nav>
    <div id="sa-gl-terms"></div>
</div>
</section>
