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
    const ROLE = {wan: '{{ lang._("Internet") }}', lan: '{{ lang._("Local network") }}', vpn: '{{ lang._("VPN tunnel") }}',
        maybe: '{{ lang._("Possibly internet") }}'};
    const KIND = {pipe: '{{ lang._("Pipe") }}', queue: '{{ lang._("Queue") }}', rule: '{{ lang._("Rule") }}', cake: '{{ lang._("CAKE entry") }}'};

    function signals(list) {
        return '<ul class="sa-signals">' + list.map(function (s) {
            // the mark is a symbol AND a word for screen readers, never colour alone
            const mark = s.true ? '<i class="fa fa-check text-success" aria-hidden="true"></i><span class="sr-only">{{ lang._("yes") }}: </span>'
                : '<i class="fa fa-times text-muted" aria-hidden="true"></i><span class="sr-only">{{ lang._("no") }}: </span>';
            return '<li>' + mark + ' ' + SA.esc(s.text) + (s.strength === 'strong' && s.true ? ' <span class="sa-meta">({{ lang._("strong sign") }})</span>' : '') + '</li>';
        }).join('') + '</ul>';
    }

    /* the interface rows as detected, so a tick is saved only where it differs from detection */
    let detected = [];
    let linkRows = [];

    function renderDetect(det) {
        detected = det.interfaces;
        const rows = det.interfaces.map(function (i, n) {
            /* a native checkbox: keyboard (Tab, Space) and screen readers work without extra code;
               a tunnel is never shaped on its own (its packets are shaped on the WAN they leave by) */
            const pick = i.role === 'vpn' ? '<input type="checkbox" id="sa-pick-' + n + '" disabled aria-describedby="sa-pick-vpn"> ' +
                '<label for="sa-pick-' + n + '" class="sa-meta">{{ lang._("shaped on its WAN") }}</label>' :
                '<input type="checkbox" class="sa-pick" id="sa-pick-' + n + '" data-key="' + SA.esc(i.key) + '"' +
                (i.selected ? ' checked' : '') + '> <label for="sa-pick-' + n + '">{{ lang._("Shape") }} ' + SA.esc(i.descr) + '</label>';
            return '<tr><th scope="row">' + SA.ref('interface', i.descr, {key: i.key, content: i.key + ': ' + i.path.join(' → ')}) +
                ' <span class="sa-meta">(' + SA.esc(i.key) + ')</span></th>' +
                '<td>' + pick + '</td>' +
                '<td>' + SA.badge(i.role === 'maybe' ? 'warn' : (i.role === 'wan' ? 'info' : 'ok'), ROLE[i.role] || i.role) +
                (i.chosen ? ' <span class="sa-meta">{{ lang._("(your choice:") }} ' + (i.selected ? '{{ lang._("shaped)") }}' : '{{ lang._("not shaped)") }}') + '</span>' : '') + '</td>' +
                '<td><code>' + SA.esc(i.path.join(' → ')) + '</code>' + (i.note ? ' <span class="sa-meta">(' + SA.esc(i.note) + ')</span>' : '') + '</td>' +
                '<td>' + signals(i.signals) + '</td></tr>';
        }).join('');
        $('#sa-ifs tbody').html(rows || '<tr><td colspan="5">{{ lang._("No assigned interfaces found.") }}</td></tr>');
        SA.labelCells('#sa-ifs');
        $('#sa-links').html(det.links.length ? '<ul>' + det.links.map(function (l) {
            return '<li><strong>' + SA.esc(l.name) + '</strong>: ' + SA.esc(l.members.join(', ')) + '. ' + SA.esc(l.why) + '.</li>';
        }).join('') + '</ul>' : '<p>' + SA.badge('warn', '{{ lang._("None") }}') + ' {{ lang._("No internet link detected.") }}</p>');
        if (det.error) {
            $('#sa-links').append('<p>' + SA.badge('warn') + ' ' + SA.esc(det.error) + '</p>');
        }
    }

    /* step 2: one row per link, its own speeds and line type (a failover LTE line is not the fibre) */
    function renderRates(plan) {
        const typed = {};
        $('#sa-rates-table tbody tr').each(function () {
            typed[$(this).attr('data-port')] = {down: $(this).find('.sa-down').val(), up: $(this).find('.sa-up').val(),
                line: $(this).find('.sa-line').val()};
        });
        linkRows = plan.links;
        const opts = plan.line_types || {};
        const rows = plan.links.map(function (l, n) {
            const r = l.rates;
            const t = typed[l.root] || {};
            const down = t.down !== undefined ? t.down : (r.own ? r.down : (qs.get('down') || r.down || 0));
            const up = t.up !== undefined ? t.up : (r.own ? r.up : (qs.get('up') || r.up || 0));
            const line = t.line || r.line;
            const select = Object.keys(opts).map(function (k) {
                return '<option value="' + SA.esc(k) + '"' + (k === line ? ' selected' : '') + '>' + SA.esc(opts[k]) +
                    (k === 'auto' ? ' (' + r.overhead_suggested + ' {{ lang._("bytes") }})' : '') + '</option>';
            }).join('');
            return '<tr data-port="' + SA.esc(l.root) + '"><th scope="row">' + SA.esc(l.name) + ' <span class="sa-meta">(' +
                SA.esc(l.members.join(', ')) + ' {{ lang._("on") }} ' + SA.esc(l.root) + ')</span></th>' +
                '<td><label class="sr-only" for="sa-down-' + n + '">' + SA.esc(l.name) + ' {{ lang._("download, Mbit/s") }}</label>' +
                '<input type="number" id="sa-down-' + n + '" class="form-control sa-down" min="0" max="100000" step="1" inputmode="numeric" value="' + SA.esc(down) + '"></td>' +
                '<td><label class="sr-only" for="sa-up-' + n + '">' + SA.esc(l.name) + ' {{ lang._("upload, Mbit/s") }}</label>' +
                '<input type="number" id="sa-up-' + n + '" class="form-control sa-up" min="0" max="100000" step="1" inputmode="numeric" value="' + SA.esc(up) + '"></td>' +
                '<td><label class="sr-only" for="sa-line-' + n + '">' + SA.esc(l.name) + ' {{ lang._("line type") }}</label>' +
                '<select id="sa-line-' + n + '" class="form-control sa-line" aria-describedby="sa-line-why">' + select + '</select>' +
                '<div class="sa-meta">{{ lang._("Overhead in the plan") }}: ' + r.overhead + ' {{ lang._("bytes") }}' +
                (r.line === 'auto' ? ' ({{ lang._("suggested from the interface") }})' : '') + '</div></td></tr>';
        }).join('');
        $('#sa-rates-table tbody').html(rows || '<tr><td colspan="4">{{ lang._("Tick at least one internet link in step 1.") }}</td></tr>');
        SA.labelCells('#sa-rates-table');
    }

    function formState() {
        return JSON.stringify({picks: $('.sa-pick').map(function () { return $(this).attr('data-key') + ':' + this.checked; }).get(),
            rates: $('#sa-rates-table tbody tr').map(function () {
                return [$(this).attr('data-port'), $(this).find('.sa-down').val(), $(this).find('.sa-up').val(), $(this).find('.sa-line').val()].join('|');
            }).get(), mode: $('input[name=sa-conflicts]:checked').val() || 'skip'});
    }

    /* save the ticks (only where they differ from detection) and each link's speeds, then preview */
    function saveChoices() {
        const choices = [];
        $('.sa-pick').each(function () {
            const key = $(this).attr('data-key');
            const row = detected.find(function (i) { return i.key === key; });
            if (row && this.checked !== (row.role === 'wan')) {
                choices.push({interface: key, include: this.checked ? 'yes' : 'no'});
            }
        });
        const links = $('#sa-rates-table tbody tr[data-port]').map(function () {
            return {port: $(this).attr('data-port'), download: $(this).find('.sa-down').val() || '0',
                upload: $(this).find('.sa-up').val() || '0', line: $(this).find('.sa-line').val() || 'auto'};
        }).get();
        return new Promise(function (resolve, reject) {
            const shown = detected.map(function (i) { return i.key; });
            ajaxCall('/api/shaperinsight/links/set', {shown: shown, choices: choices, links: links}, function (r, status) {
                if (status !== 'success' || !r || r.status !== 'ok') {
                    reject(new Error((r && r.message) || '{{ lang._("the choices could not be saved") }}'));
                } else {
                    resolve();
                }
            });
        });
    }

    function fields(f) {
        return Object.keys(f).map(function (k) { return SA.esc(k) + ' = ' + SA.esc(f[k]); }).join(', ');
    }

    function renderList(sel, list, empty) {
        $(sel).html(list.length ? '<ul class="sa-check-items">' + list.map(function (i) {
            return '<li>' + SA.badge(i.level) + ' ' + SA.esc(i.text) +
                (i.why ? '<div class="sa-why-text"><strong>{{ lang._("Why") }}:</strong> ' + SA.words(i.why) + SA.whys(i.claims) + '</div>' : '') +
                (i.example ? '<div class="sa-why-text"><strong>{{ lang._("Fix") }}:</strong> ' + SA.esc(i.example) + '</div>' : '') +
                (i.choices ? '<div class="sa-why-text"><strong>{{ lang._("You will choose") }}:</strong> ' + SA.esc(i.choices.join(' / ')) + '</div>' : '') +
                '</li>';
        }).join('') + '</ul>' : '<p>' + SA.badge('ok') + ' ' + empty + '</p>');
    }

    function renderPlan(plan) {
        renderRates(plan);
        renderList('#sa-preflight', plan.preflight, '{{ lang._("Nothing blocks the setup.") }}');
        renderList('#sa-conflicts', plan.conflicts, '{{ lang._("No hand-made shaper rule on these links.") }}');
        const t = plan.totals;
        $('#sa-plan-sum').text(plan.links.length ?
            '{{ lang._("Would add") }} ' + (t.add || 0) + ', {{ lang._("change") }} ' + (t.change || 0) + ' {{ lang._("and leave") }} ' +
            (t.same || 0) + ' {{ lang._("of Shaper Insight objects from an earlier apply as they are") }} (' + (t.pipe || 0) + ' {{ lang._("pipes") }}, ' +
            (t.queue || 0) + ' {{ lang._("queues") }}, ' + (t.rule || 0) + ' {{ lang._("rules") }}, ' + (t.cake || 0) +
            ' {{ lang._("CAKE entries") }})' + (t.remove ? ', {{ lang._("and remove") }} ' + t.remove +
            ' {{ lang._("it no longer needs") }}' : '') + '; ' + (plan.disable_rules && plan.disable_rules.length ?
            '{{ lang._("and switches off") }} ' + plan.disable_rules.length + ' {{ lang._("of your rules (listed below; the timer or Remove switches them back on).") }}' :
            '{{ lang._("none of yours changes.") }}') :
            '{{ lang._("Nothing to add.") }}');
        if (plan.nothing) {
            /* "Keep mine" on every link, or nothing ticked: say so instead of counting objects */
            $('#sa-plan-sum').text('{{ lang._("Nothing to apply") }}: ' + plan.nothing + '.');
        }
        /* every object of the plan in SA.table (sortable; Change, then object kind, by default) */
        const KIND_ORDER = {pipe: 0, queue: 1, rule: 2, cake: 3};
        const ACT_ORDER = {add: 0, change: 1, same: 2, remove: 3};
        const PLAN_COLS = [
            {title: '{{ lang._("Change") }}', field: 'act', type: 'html', sortField: 'actRank'},
            {title: '{{ lang._("Object") }}', field: 'kind', type: 'html', sortField: 'kindRank'},
            {title: '{{ lang._("Number") }}', field: 'num', type: 'num', fmt: function (v, row) { return row.id; }},
            {title: '{{ lang._("Name") }}', field: 'name', type: 'html'},
            {title: '{{ lang._("Settings") }}', field: 'fields', type: 'html', sort: false},
            {title: '{{ lang._("Why") }}', field: 'why', type: 'html'}];
        const planRow = function (o, action) {
            const id = o.kind === 'rule' ? '#' + o.sequence : (o.kind === 'cake' ? '{{ lang._("pipe") }} ' + o.pipe : o.number);
            const act = action === 'remove' ? '<span class="sa-add" aria-hidden="true">−</span> {{ lang._("Remove") }}' :
                (action === 'change' ? '<span class="sa-add" aria-hidden="true">~</span> {{ lang._("Change") }}' :
                (action === 'same' ? '<span class="sa-add" aria-hidden="true">=</span> {{ lang._("No change") }}' :
                '<span class="sa-add" aria-hidden="true">+</span> {{ lang._("Add") }}'));
            const diff = o.changes ? Object.keys(o.changes).map(function (k) {
                return SA.esc(k) + ': ' + SA.esc(o.changes[k][0]) + ' → ' + SA.esc(o.changes[k][1]);
            }).join(', ') : '';
            /* objects that exist already link to core's shaper page, as core's rules grid links aliases */
            const kindRef = o.kind === 'cake' ? 'cake' : o.kind;  /* os-cake's own page: tooltip only */
            const name = action === 'add' || !o.description ? SA.esc(o.description || '') :
                SA.ref(kindRef, o.description, {uuid: o.uuid, content: KIND[o.kind] + ' ' + id});
            return {act: act + (diff ? '<div class="sa-meta">' + diff + '</div>' : ''), actRank: ACT_ORDER[action] || 0,
                kind: KIND[o.kind], kindRank: KIND_ORDER[o.kind] || 0, id: String(id),
                num: Number(o.kind === 'rule' ? o.sequence : (o.kind === 'cake' ? o.pipe : o.number)) || 0,
                name: name, fields: fields(o.fields), why: SA.words(o.why) + SA.whys(o.claims || [])};
        };
        $('#sa-plan').html(plan.links.map(function (l, li) {
            const c = l.counts;
            return '<details class="sa-check"><summary>' + SA.esc(l.name) + ': {{ lang._("every object, with its settings and reason") }} ' +
                '<span class="sa-meta">(' + (c.pipe || 0) + ' {{ lang._("pipes") }}, ' + (c.queue || 0) + ' {{ lang._("queues") }}, ' +
                (c.rule || 0) + ' {{ lang._("rules") }}, ' + (c.cake || 0) + ' {{ lang._("CAKE entries") }})</span></summary>' +
                '<div id="sa-plan-' + li + '"></div></details>';
        }).join(''));
        plan.links.forEach(function (l, li) {
            SA.table('#sa-plan-' + li, PLAN_COLS, l.objects.map(function (o) { return planRow(o, o.action || 'add'); }),
                {sort: ['kind', 'asc'], label: l.name, empty: '{{ lang._("No objects for this link.") }}'});
        });
        if ((plan.remove || []).length) {
            /* Shaper Insight's own objects the new plan has no place for: removed by the same apply */
            $('#sa-plan').append('<details class="sa-check" open><summary>{{ lang._("Objects from an earlier apply that the new plan no longer needs") }} ' +
                '<span class="sa-meta">(' + plan.remove.length + ')</span></summary><div id="sa-plan-remove"></div></details>');
            SA.table('#sa-plan-remove', PLAN_COLS, plan.remove.map(function (o) { return planRow(o, 'remove'); }),
                {sort: ['kind', 'asc'], label: '{{ lang._("Objects to remove") }}', empty: '–'});
        }
        /* only with "Keep mine" does a link with your rules get skipped (N6) */
        const blocked = plan.mode === 'skip' ? [...new Set(plan.conflicts.map(function (c) { return c.link; }))] : [];
        $('#sa-plan-plain').html(plan.summary.map(function (t) { return '<li>' + SA.words(t.text) + SA.whys(t.claims) + '</li>'; }).join('') +
            ((plan.disable_rules || []).length ? '<li>' + SA.badge('warn') + ' {{ lang._("Apply switches off these rules of yours") }}: ' +
                SA.esc(plan.disable_rules.map(function (r) { return r.text + ' (' + r.link + ')'; }).join('; ')) + '.</li>' : '') +
            (blocked.length ? '<li>' + SA.badge('warn') + ' ' + SA.esc(blocked.join(', ')) +
                ' {{ lang._("already has your own shaper rules: Apply skips it unless you choose \"Disable mine\" above.") }}</li>' : ''));
        $('#sa-steps').html(plan.apply_steps.map(function (st) {
            return '<li><strong>' + SA.esc(st.step) + '.</strong> ' + SA.esc(st.why) + SA.whys(st.claims) + '</li>';
        }).join(''));
        /* Apply writes exactly this plan: the digest goes with it, and the server refuses when
           the configuration or the detected links changed since (then preview again) */
        previewed = {digest: plan.digest, form: formState()};
        $('#sa-apply').prop('disabled', !plan.ready || !!plan.nothing);
        $('#sa-apply-blocked').text(plan.nothing ? '{{ lang._("Nothing to apply") }}: ' + plan.nothing + '.' :
            (plan.ready ? '' : '{{ lang._("Apply needs both speeds of every ticked link (step 2).") }}'));
    }
    let previewed = null;

    /* save: write the choices first (only when the page has rows to save), then build the plan */
    function preview(save) {
        const focused = document.activeElement && document.activeElement.id;
        $('#sa-preview').prop('disabled', true);
        $('#sa-plan-sum').text('{{ lang._("Working out the plan...") }}');
        const q = $.param({conflicts: $('input[name=sa-conflicts]:checked').val() || 'skip'});
        return (save ? saveChoices() : Promise.resolve()).then(function () {
            return SA.get('/api/shaperinsight/health/detect?' + q);
        }).then(function (d) {
            if (!d || !d.detect) { throw new Error((d && d.message) || 'no data'); }
            renderDetect(d.detect);
            renderPlan(d.plan);
            document.body.setAttribute('data-sa-ready', '1');
        }).catch(function (e) {
            previewed = null;
            $('#sa-plan-sum').html(SA.badge('bad', '{{ lang._("Not available") }}') + ' {{ lang._("The plan could not be built") }}: ' +
                SA.esc(e && e.message ? e.message : '{{ lang._("configd did not answer") }}') + '.');
        }).then(function () {
            $('#sa-preview').prop('disabled', false);
            /* the rows are rebuilt: give the keyboard focus back to the control that had it */
            if (focused && document.getElementById(focused)) { document.getElementById(focused).focus(); }
        });
    }

    // ?down=1000&up=50 pre-fills the speeds of links that have none saved (links from a speed test)
    const qs = new URLSearchParams(window.location.search);
    ['down', 'up'].forEach(function (k) {
        if (!/^\d{1,6}$/.test(qs.get(k) || '')) { qs.delete(k); }
    });
    /* ---- Apply with the server-side confirm timer. The countdown is the server's: any tab
       (or a new one after the browser died) shows it, and the firewall reverts on its own. */
    let applyPoll = null;
    function renderApply(st) {
        if (!st || !st.state) {
            return;
        }
        const steps = (st.steps || []).map(function (x) {
            return '<li>' + SA.badge(x.ok ? 'ok' : 'bad', x.ok ? '{{ lang._("Done") }}' : '{{ lang._("Failed") }}') + ' ' +
                SA.esc(x.name) + (x.detail ? ' <span class="sa-meta">' + SA.esc(x.detail) + '</span>' : '') + '</li>';
        }).join('');
        /* after Remove the last apply's steps are history, not what just happened (F13) */
        $('#sa-apply-steps').html(st.state === 'removed' ? '' : steps);
        const words = {
            running: ['info', '{{ lang._("Applying...") }}'],
            waiting: ['warn', '{{ lang._("Keep these settings? Going back in") }} ' + st.seconds_left + ' s.'],
            confirmed: ['ok', '{{ lang._("Confirmed. The new shaper stays.") }}'],
            reverting: ['warn', '{{ lang._("Going back to the restore point...") }}'],
            reverted: ['info', '{{ lang._("Previous shaper settings are back") }}: ' + (st.reason || '')],
            'revert-failed': ['bad', '{{ lang._("Going back failed. The full backup taken before the apply is in System: Configuration: History") }}: ' + (st.safety_net || '')],
            removed: ['info', '{{ lang._("Everything Shaper Insight created was removed.") }}'],
            failed: ['bad', '{{ lang._("Apply failed before anything changed.") }}'],
            'needs-attention': ['warn', '{{ lang._("Needs your decision") }}: ' + (st.reason || '')],
            kept: ['info', '{{ lang._("The settings as they are now were kept.") }}']
        }[st.state] || ['info', st.state];
        $('#sa-apply-state').html(SA.badge(words[0]) + ' ' + SA.esc(words[1]) +
            (st.state_note ? ' <span class="sa-meta">' + SA.esc(st.state_note) + '</span>' : ''));
        $('#sa-confirm').toggle(st.state === 'waiting').data('job', st.job);
        $('#sa-decide').toggle(st.state === 'needs-attention');
        if (st.options) {
            $('#sa-decide-restore-why').text(st.options.restore);
            $('#sa-decide-keep-why').text(st.options.keep);
        }
        SA.countdown(st.state === 'waiting' && st.seconds_left > 0 ? {seconds_left: st.seconds_left, job: st.job} : null);
        $('#sa-apply').prop('disabled', st.alive || !previewed);
        if (!st.alive && applyPoll && st.state !== 'running') {
            clearInterval(applyPoll);
            applyPoll = null;
        }
    }
    /* status is a read: a stopped job is only reported. The page asks for the recovery with a
       POST, once per page view (review S5) */
    let recoverAsked = false;
    function maybeRecover(st) {
        if (st && st.recovery_pending && !recoverAsked) {
            recoverAsked = true;
            ajaxCall('/api/shaperinsight/apply/recover', {}, function () { watchApply(); });
        }
        return st;
    }
    function pollApply() {
        return SA.get('/api/shaperinsight/apply/status').then(maybeRecover).then(renderApply);
    }
    function watchApply() {
        if (!applyPoll) {
            applyPoll = setInterval(pollApply, 1000);
        }
        pollApply();
    }
    $('#sa-apply').on('click', function () {
        stdDialogConfirm('{{ lang._("Apply with a 60 s safety timer") }}',
            '{{ lang._("The firewall saves a restore point, applies the plan and checks the kernel. Then you have 60 s to confirm; without a confirmation it goes back by itself.") }}',
            '{{ lang._("Apply") }}', '{{ lang._("Cancel") }}', function () {
                if (!previewed || previewed.form !== formState()) {
                    $('#sa-apply-state').html(SA.badge('warn') + ' {{ lang._("The choices changed since the preview. Save and preview the plan again, then apply.") }}');
                    return;
                }
                /* each link's speeds are the saved ones; 0/0 = no fallback for a link without them */
                ajaxCall('/api/shaperinsight/apply/start', {down: 0, up: 0, digest: previewed.digest,
                    conflicts: $('input[name=sa-conflicts]:checked').val() || 'skip'}, function (r) {
                    if (r && r.status === 'started') {
                        watchApply();
                    } else {
                        $('#sa-apply-state').html(SA.badge('bad') + ' ' + SA.esc((r && r.message) || 'failed'));
                    }
                });
            });
    });
    $('#sa-decide button').on('click', function () {
        ajaxCall('/api/shaperinsight/apply/resolve', {choice: $(this).data('choice')}, function (r) {
            if (!r || r.status !== 'ok') {
                $('#sa-apply-state').html(SA.badge('bad') + ' ' + SA.esc((r && r.message) || 'failed'));
            }
            pollApply();
        });
    });
    $('#sa-confirm').on('click', function () {
        // a job id, not the confirm token (that never leaves the firewall)
        ajaxCall('/api/shaperinsight/apply/confirm', {job: $(this).data('job')}, function (r) {
            if (!r || r.status !== 'ok') {
                $('#sa-apply-state').html(SA.badge('bad') + ' ' + SA.esc((r && r.message) || 'no timer found'));
            }
            pollApply();
        });
    });

    /* ---- Undo: Remove deletes exactly what confirmed applies recorded (never a name match) */
    $('#sa-remove').on('click', function () {
        stdDialogConfirm('{{ lang._("Remove what Shaper Insight created") }}',
            '{{ lang._("Deletes the pipes, queues, rules and CAKE entries Shaper Insight created, and switches your rules it had disabled back on. Anything of yours that uses a Shaper Insight object keeps it (it is listed). The shaper reloads; traffic is unshaped for about a second.") }}',
            '{{ lang._("Remove") }}', '{{ lang._("Cancel") }}', function () {
                $('#sa-remove').prop('disabled', true);
                ajaxCall('/api/shaperinsight/apply/remove', {}, function (r) {
                    $('#sa-remove').prop('disabled', false);
                    if (!r || !r.status) {
                        $('#sa-remove-state').html(SA.badge('bad') + ' {{ lang._("No answer from the firewall; nothing is known about the result.") }}');
                        return;
                    }
                    const n = r.removed || {};
                    $('#sa-remove-state').html(SA.badge(r.status === 'ok' ? 'ok' : 'bad') + ' ' + SA.esc(r.message || '') + ' ' +
                        SA.esc(['pipe', 'queue', 'rule', 'cake'].filter(function (k) { return n[k]; }).map(function (k) {
                            return n[k] + ' ' + KIND[k].toLowerCase();
                        }).join(', ')) + (n.enabled_again ? ', ' + n.enabled_again + ' {{ lang._("of your rules switched back on") }}' : '') +
                        ((r.kept || []).length ? '<div class="sa-meta">{{ lang._("Kept, because something of yours uses it") }}: ' +
                            SA.esc(r.kept.join('; ')) + '</div>' : ''));
                    preview();
                });
            });
    });
    $('#sa-rates').on('submit', function (e) { e.preventDefault(); preview(true); });
    /* a tick or the conflict choice changes the plan at once; speeds wait for the button */
    $('#sa-ifs').on('change', '.sa-pick', function () { preview(true); });
    $('input[name=sa-conflicts]').on('change', function () { preview(false); });
    SA.loadGlossary().then(function () { return preview(false); }).then(function () {
        // a countdown started in another tab (or before the browser closed) shows here too
        SA.get('/api/shaperinsight/apply/status').then(maybeRecover).then(function (st) {
            if (st && st.alive) { watchApply(); } else { renderApply(st); }
        });
    });
});
</script>

<div class="content-box sa-page">
    <div class="sa-head">
        <p class="sa-lead">{{ lang._('This page shows what the setup would create for your internet links. Previewing writes nothing. Apply saves a restore point first and goes back to it by itself unless you confirm within 60 s. Remove, under Undo, deletes what Shaper Insight created.') }}</p>
    </div>
</div>

<section class="content-box sa-section" aria-labelledby="sa-h-ifs">
    <h2 id="sa-h-ifs">{{ lang._('1. Internet links and local networks') }}</h2>
    <p class="sa-lead">{{ lang._('An interface counts as an internet link when it has at least one strong sign: a gateway OPNsense marks as facing the internet ("Upstream gateway" ticked, or one it gets by DHCP, PPPoE or DHCPv6), the default route, or an address it gets from upstream. A plain gateway is only "possibly internet": it may lead to another router of yours (example: a lab router behind 10.200.0.0/16 must not be slowed to the internet speed). Tick or untick any row to correct the guess; the choice is saved. All internet links on one physical port share one speed limit, so they get one shaper together.') }}</p>
    <p class="sa-meta" id="sa-pick-vpn">{{ lang._('Tunnels (WireGuard, OpenVPN, ...) cannot be ticked: their packets are shaped on the internet link they leave by.') }}</p>
    <div class="table-responsive">
        <table class="table table-condensed table-striped sa-table sa-stack" id="sa-ifs" data-sa-form="a form: sorting would move the ticks while you set them">
            <caption class="sr-only">{{ lang._('Interfaces and why each is an internet link or not') }}</caption>
            <thead><tr><th scope="col">{{ lang._('Interface') }}</th><th scope="col">{{ lang._('Shape it') }}</th><th scope="col">{{ lang._('Detected as') }}</th>
                <th scope="col">{{ lang._('Device down to the physical port') }}</th><th scope="col">{{ lang._('Why we think so') }}</th></tr></thead>
            <tbody><tr><td colspan="5">–</td></tr></tbody>
        </table>
    </div>
    <h3>{{ lang._('Shapers needed') }}</h3>
    <div id="sa-links"><p>–</p></div>
</section>

<section class="content-box sa-section" aria-labelledby="sa-h-rates">
    <h2 id="sa-h-rates">{{ lang._('2. Speed and line type of each link') }}</h2>
    <p class="sa-lead">{{ lang._('Each link gets its own speeds: type what a speed test shows on that line, for example 1000 and 100 for fibre and 50 and 10 for a backup LTE line. The shaper is set to 95 % of it so the queue forms here, where it is managed, and not in the modem.') }}</p>
    <p class="sa-meta" id="sa-line-why">{{ lang._("Line type: what plugs into the wall. Telling it lets CAKE count each packet's real size on the wire, so the shaper can run closer to full speed. Every line adds its own framing to each packet: Ethernet or fibre 38 bytes (+4 for a VLAN tag, +8 for PPPoE), cable (DOCSIS) 18, VDSL2 with PPPoE 30, ADSL 8 to 48 and in 53-byte ATM cells. Not sure? Keep the suggestion, which is worked out from the interface.") }}</p>
    <form id="sa-rates">
        <div class="table-responsive">
            <table class="table table-condensed table-striped sa-table sa-stack" id="sa-rates-table" data-sa-form="a form: sorting would move the fields while you type">
                <caption class="sr-only">{{ lang._('Speeds and line type of each internet link') }}</caption>
                <thead><tr><th scope="col">{{ lang._('Link') }}</th><th scope="col">{{ lang._('Download, Mbit/s') }}</th>
                    <th scope="col">{{ lang._('Upload, Mbit/s') }}</th><th scope="col">{{ lang._('Line type') }}</th></tr></thead>
                <tbody><tr><td colspan="4">–</td></tr></tbody>
            </table>
        </div>
        <button type="submit" id="sa-preview" class="btn btn-primary"><i class="fa fa-eye" aria-hidden="true"></i> {{ lang._('Save and preview plan') }}</button>
        <span class="sa-meta">{{ lang._('Saves the ticks and speeds (nothing is applied), then shows the plan.') }}</span>
    </form>
</section>

<section class="content-box sa-section" aria-labelledby="sa-h-pre">
    <h2 id="sa-h-pre">{{ lang._('3. Before anything changes') }}</h2>
    <h3>{{ lang._('Firewall settings') }}</h3>
    <div id="sa-preflight"><p>–</p></div>
    <h3>{{ lang._('Your own shaper rules on these links') }}</h3>
    <div id="sa-conflicts"><p>–</p></div>
    <fieldset class="sa-choice">
        <legend class="sa-legend">{{ lang._('When one of your rules already shapes a link') }}</legend>
        <label><input type="radio" name="sa-conflicts" value="skip" checked> {{ lang._('Keep mine and skip that link (nothing of yours changes)') }}</label><br>
        <label><input type="radio" name="sa-conflicts" value="disable-bulk"> {{ lang._('Disable only my rules that shape all traffic; keep the narrower ones on (for example ICMPv6 control lanes, rules for some ports or addresses)') }}</label><br>
        <label><input type="radio" name="sa-conflicts" value="disable"> {{ lang._('Disable all my rules on these links and use Shaper Insight (your rules stay, switched off; the timer brings them back if you do not confirm)') }}</label>
        <p class="sa-meta">{{ lang._('The list above says, rule by rule, what Apply does with each one. Every shaper reload (Apply, the timer revert, Remove) leaves traffic unshaped, not cut, for about a second.') }}</p>
    </fieldset>
</section>

<section class="content-box sa-section" aria-labelledby="sa-h-plan">
    <h2 id="sa-h-plan">{{ lang._('4. The plan') }}</h2>
    <p id="sa-plan-sum" class="sa-lead" aria-live="polite">–</p>
    <ul id="sa-plan-plain" class="sa-plain"></ul>
    <div id="sa-plan"></div>
    <h3>{{ lang._('What Apply will do, in order') }}</h3>
    <ol id="sa-steps" class="sa-steps"></ol>
    <p>
        <button type="button" id="sa-apply" class="btn btn-primary" aria-describedby="sa-apply-why"><i class="fa fa-check" aria-hidden="true"></i> {{ lang._('Apply with 60 s safety timer') }}</button>
        <button type="button" id="sa-confirm" class="btn btn-success" style="display:none"><i class="fa fa-thumbs-up" aria-hidden="true"></i> {{ lang._('Keep these settings') }}</button>
        <span id="sa-apply-blocked" class="sa-meta" role="status"></span>
        <span id="sa-apply-why" class="sa-meta">{{ lang._('If you do not confirm within 60 s, for example because the change cut you off, the firewall puts the restore point back by itself, also after a restart.') }}</span>
    </p>
    <div id="sa-apply-state" role="status" aria-live="polite"></div>
    <div id="sa-decide" class="sa-note" style="display:none">
        <p><strong>{{ lang._('The apply stopped before you confirmed it, and the shaper settings were changed after it. Nothing was put back, so no later change is lost. Choose one:') }}</strong></p>
        <p><button type="button" class="btn btn-default" data-choice="restore">{{ lang._('Put back the settings from before the apply') }}</button>
            <span class="sa-meta" id="sa-decide-restore-why"></span></p>
        <p><button type="button" class="btn btn-default" data-choice="keep">{{ lang._('Keep the settings as they are now') }}</button>
            <span class="sa-meta" id="sa-decide-keep-why"></span></p>
    </div>
    <ol id="sa-apply-steps" class="sa-steps"></ol>
</section>

<section class="content-box sa-section" aria-labelledby="sa-h-undo">
    <h2 id="sa-h-undo">{{ lang._('5. Undo') }}</h2>
    <p class="sa-lead">{{ lang._('Remove deletes what Shaper Insight created (recorded when you confirmed each apply) and switches your rules it disabled back on. Your own objects are never deleted.') }}</p>
    <p><button type="button" id="sa-remove" class="btn btn-default"><i class="fa fa-undo" aria-hidden="true"></i> {{ lang._('Remove what Shaper Insight created') }}</button></p>
    <div id="sa-remove-state" role="status" aria-live="polite"></div>
    <p class="sa-meta">{{ lang._("To take Shaper Insight's shaping away, press Remove before you uninstall the plugin. Uninstalling alone leaves your shaper as it is: the pipes, queues and rules keep working, only the priorities turn off (the marks need the plugin). A reinstall picks the setup up again, and Remove still works then. Example: remove the plugin for a test, reinstall it, and nothing has to be set up again.") }}</p>
</section>
