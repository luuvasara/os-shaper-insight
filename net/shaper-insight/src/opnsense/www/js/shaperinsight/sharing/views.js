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
 * Sharing page, views: five layouts (slot templates) and five graphs, chosen with two kinds of selects.
 * - The first visit shows a random valid combination and says so; picking anything (or "Keep this
 *   view") remembers the choice in this browser (localStorage, wrapped: the page works without it).
 * - ?layout=l2&graphs=g4,g1 opens that view without remembering it (links, screenshots).
 * - Each slot offers only the graphs that work there; the combinations that did not work in the
 *   mock-ups (G3 or G4 as L3's time picker, G2/G5 in L4, G4 in L5) are never
 *   offered, so neither the random pick nor the selects can produce them.
 * - One data layer: the history is fetched once and then only the new rows (?since=); every view draws
 *   from the same model. Per-host series of every host (?all=1) are asked for only while G2 or G5 is on
 *   screen.
 */
(function () {
    'use strict';
    const SH = window.SAsh;
    const KEY = 'shaperinsight.sharing.view.v1';
    const PIPE_KEY = 'shaperinsight.sharing.pipe.v1';

    const GRAPHS = {
        g1: {name: 'G1 Ceiling river', time: true, why: 'who used the line, and how close to the cap, over time. Ex.: one console fills the cap from 20:10 to 20:40.'},
        g2: {name: 'G2 Horizon rows', time: true, perHost: true, why: 'every host on its own baseline, so small hosts stay readable. Ex.: which of 40 hosts were busy while the line was limiting.'},
        g3: {name: 'G3 Capacity map', why: 'who has the line right now and whether it is full, with no axis to read. Ex.: the Bulk frame is mostly the backup box.'},
        g4: {name: 'G4 Flow ribbons', why: 'whether the shaping does its job: hosts to classes to delivered or dropped. Ex.: the drops land in Bulk and Voice has none.'},
        g5: {name: 'G5 Time grid', time: true, perHost: true, why: 'when the line was full and who was on it, as a heat grid. Ex.: the limited half hour lights up in the state row.'}
    };
    const LAYOUTS = {
        l1: {name: 'L1 Dashboard grid', why: 'the pipe’s numbers at a glance, every card the same size. Ex.: an operator who checks the same spots each day.',
            slots: [{id: 'main', title: 'Main card', accepts: ['g1', 'g2', 'g3', 'g4', 'g5']}]},
        l2: {name: 'L2 Verdict first', why: 'it answers “the internet is slow, why?” in words first, then the proof. Ex.: “Download is limiting now: nas takes 82 % of the line”.',
            slots: [{id: 'shaping', title: 'Section 2, is the shaping doing its job?', accepts: ['g4', 'g3'], not: 'G1, G2 and G5 are history: section 3 holds them.'},
                {id: 'when', title: 'Section 3, when were you limited?', accepts: ['g1', 'g2', 'g5'], not: 'G3 and G4 have no time axis.'}]},
        l3: {name: 'L3 Master–detail', why: 'you pick a time on the left and the right side explains it. Ex.: what happened at 20:15, and who caused it.',
            slots: [{id: 'master', title: 'Time picker', accepts: ['g5', 'g2', 'g1'], not: 'G3 and G4 have no time axis to pick from (G3 here was a known bad mix in the mock-ups).'}]},
        l4: {name: 'L4 Now', why: 'what is happening right now: the fastest view, and the best on a phone. Ex.: watching a game download start.',
            slots: [{id: 'now', title: 'Live view', accepts: ['g3', 'g4', 'g1'], not: 'G2 and G5 are history grids; in this layout they crowded out the live view (mock-ups).'}]},
        l5: {name: 'L5 Table first', why: 'who exactly, with exact numbers; close to Reporting: Top talkers. Ex.: copy the address of the busiest host.',
            slots: [{id: 'top', title: 'Graph above the table', accepts: ['g1', 'g2', 'g3', 'g5'], not: 'G4 needs the full width and pushed the table below the fold (mock-ups).'}]}
    };

    function valid(v) {
        if (!v || !LAYOUTS[v.layout] || typeof v.graphs !== 'object') { return false; }
        return LAYOUTS[v.layout].slots.every(function (s) { return s.accepts.indexOf(v.graphs[s.id]) >= 0; });
    }
    function combos() {
        const out = [];
        Object.keys(LAYOUTS).forEach(function (l) {
            let acc = [[]];
            LAYOUTS[l].slots.forEach(function (s) {
                const next = [];
                acc.forEach(function (a) { s.accepts.forEach(function (g) { next.push(a.concat([g])); }); });
                acc = next;
            });
            acc.forEach(function (gs) { out.push({layout: l, graphs: gs}); });
        });
        return out;
    }
    function toView(c) {
        const v = {layout: c.layout, graphs: {}};
        LAYOUTS[c.layout].slots.forEach(function (s, k) { v.graphs[s.id] = c.graphs[k]; });
        return v;
    }
    function short(v) {
        return v.layout.toUpperCase() + ' + ' + LAYOUTS[v.layout].slots.map(function (s) { return v.graphs[s.id].toUpperCase(); }).join(', ');
    }
    function store(key, val) { try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) { /* private window: not remembered */ } }
    function stored(key) { try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch (e) { return null; } }
    function forget(key) { try { localStorage.removeItem(key); } catch (e) { /* nothing to forget */ } }

    /* the view: from the link, else remembered, else random (and said so) */
    let view, source;
    (function () {
        const q = new URLSearchParams(location.search);
        const fromLink = q.get('layout') ? toView({layout: q.get('layout'), graphs: (q.get('graphs') || '').split(',')}) : null;
        if (fromLink && LAYOUTS[fromLink.layout] && valid(fromLink)) { view = fromLink; source = 'link'; return; }
        const saved = stored(KEY);
        if (valid(saved)) { view = saved; source = 'saved'; return; }
        const all = combos();
        view = toView(all[Math.floor(Math.random() * all.length)]);
        source = 'random';
    }());

    let M = null, pipeN = stored(PIPE_KEY), insts = {}, fixed = {}, token = 0, built = '';
    const root = function () { return document.getElementById('sa-view'); };

    /* ------------------------------------------------------------------ the switcher */
    function graphOption(g) {
        const G = GRAPHS[g];
        let txt = G.name;
        if (G.perHost && M) {
            txt += M.store.mode === 'every' ? ' (every host)' : ' (busiest ' + M.store.top + ' + rest)';
        }
        return txt;
    }
    function drawBar() {
        const L = LAYOUTS[view.layout];
        const bar = document.getElementById('sa-viewbar');
        const note = source === 'random' ? '<b>Random view (' + short(view) + ').</b> Pick your own below, or <button type="button" class="btn btn-default btn-xs" id="sa-keep">Keep this view</button>. ' +
            'Why random: five layouts and five graphs answer different questions, and seeing a new one now and then shows what each is for.' :
            source === 'link' ? '<b>View from the link (' + short(view) + ').</b> It is not remembered; pick one below to keep it.' :
            '<b>Your view (' + short(view) + ')</b>, remembered in this browser. <button type="button" class="btn btn-default btn-xs" id="sa-forget">Random again</button>';
        bar.innerHTML = '<p class="sa-meta" id="sa-view-note" aria-live="polite">' + note + '</p>' +
            '<div class="sa-viewsel"><div class="sa-vs-row"><label for="sa-layout">Layout</label> <select id="sa-layout" class="form-control sa-inline-select">' +
            Object.keys(LAYOUTS).map(function (l) { return '<option value="' + l + '"' + (l === view.layout ? ' selected' : '') + '>' + LAYOUTS[l].name + '</option>'; }).join('') +
            '</select> <span class="sa-meta sa-vs-why">Why this layout: ' + SA.esc(L.why) + '</span></div>' +
            L.slots.map(function (s) {
                const g = view.graphs[s.id];
                return '<div class="sa-vs-row"><label for="sa-slot-sel-' + s.id + '">' + (L.slots.length > 1 ? SA.esc(s.title) : 'Graph') + '</label> <select id="sa-slot-sel-' + s.id +
                    '" class="form-control sa-inline-select" data-slot="' + s.id + '">' + s.accepts.map(function (x) {
                        return '<option value="' + x + '"' + (x === g ? ' selected' : '') + '>' + SA.esc(graphOption(x)) + '</option>';
                    }).join('') + '</select> <span class="sa-meta sa-vs-why">What it shows: ' + SA.esc(GRAPHS[g].why) + (s.not ? ' Not offered here: ' + SA.esc(s.not) : '') + '</span></div>';
            }).join('') + '</div>' +
            '<details class="sa-explain"><summary>What each layout and graph is for</summary><ul class="sa-plain">' +
            Object.keys(LAYOUTS).map(function (l) { return '<li><b>' + LAYOUTS[l].name + '</b>: ' + SA.esc(LAYOUTS[l].why) + '</li>'; }).join('') +
            Object.keys(GRAPHS).map(function (g) {
                return '<li><b>' + GRAPHS[g].name + '</b>: ' + SA.esc(GRAPHS[g].why) + (GRAPHS[g].perHost ? ' Per-host rows need the per-host history: every host while “Store every host’s history” is on, else the busiest per sample + the rest.' : '') + '</li>';
            }).join('') + '</ul></details>';
        const pipes = M ? M.pipes : [];
        const pb = document.getElementById('sa-pipebar');
        pb.innerHTML = pipes.length > 1 ? '<span id="sa-pipe-l">Pipe</span> <div class="btn-group btn-group-sm" role="group" aria-labelledby="sa-pipe-l">' + pipes.map(function (P) {
            return '<button type="button" class="btn btn-default" data-pipe="' + SA.esc(P.n) + '" aria-pressed="' + (P.n === pipeN) + '">' + SA.esc(P.short) + (P.busy ? ' ▲' : '') + '</button>';
        }).join('') + '</div> <span class="sa-meta">One pipe at a time keeps the page short; the first visit opens the pipe that is limiting (▲) or the busiest.</span>' : '';
    }
    function pick(v, src) {
        view = v; source = src;
        if (src === 'saved') { store(KEY, view); }
        drawBar();
        rebuild();
    }
    document.addEventListener('change', function (e) {
        if (e.target.id === 'sa-layout') {
            const L = LAYOUTS[e.target.value];
            const v = {layout: e.target.value, graphs: {}};
            /* keep the graphs that fit the new layout's slots, else its first choice */
            const have = Object.values(view.graphs);
            L.slots.forEach(function (s) { v.graphs[s.id] = s.accepts.find(function (g) { return have.indexOf(g) >= 0; }) || s.accepts[0]; });
            pick(v, 'saved');
            document.getElementById('sa-layout').focus();
        } else if (e.target.getAttribute && e.target.getAttribute('data-slot')) {
            const v = {layout: view.layout, graphs: Object.assign({}, view.graphs)};
            v.graphs[e.target.getAttribute('data-slot')] = e.target.value;
            const id = e.target.id;
            pick(v, 'saved');
            document.getElementById(id).focus();
        }
    });
    document.addEventListener('click', function (e) {
        if (e.target.id === 'sa-keep') { pick(view, 'saved'); document.getElementById('sa-layout').focus(); }
        if (e.target.id === 'sa-forget') {
            forget(KEY);
            const all = combos();
            pick(toView(all[Math.floor(Math.random() * all.length)]), 'random');
            document.getElementById('sa-layout').focus();
        }
        const pb = e.target.closest && e.target.closest('#sa-pipebar button[data-pipe]');
        if (pb) {
            pipeN = pb.getAttribute('data-pipe');
            store(PIPE_KEY, pipeN);
            drawBar();
            rebuild();
            const again = document.querySelector('#sa-pipebar button[data-pipe="' + CSS.escape(pipeN) + '"]');
            if (again) { again.focus(); }
        }
    });

    /* ------------------------------------------------------------------ layouts */
    function card(id, title, inner) {
        return '<section class="sa-card" aria-labelledby="' + id + '-h"><h3 id="' + id + '-h">' + title + '</h3>' + (inner || '<div id="' + id + '"></div>') + '</section>';
    }
    function slotTitle(sid) { return SA.esc(GRAPHS[view.graphs[sid]].name.replace(/^G\d /, '')); }
    let l3sel = null;
    function l3default(P) {
        const past = P.periods.filter(function (x) { return x[1] < P.N; });
        const pr = past.length ? past[past.length - 1] : P.periods[P.periods.length - 1];
        return pr ? [pr[0], Math.min(P.N, pr[1])] : [Math.max(0, P.N - Math.ceil(P.N / 10)), P.N];
    }
    const L = {
        l1: {
            html: function () {
                return '<div class="sa-kpis" id="sa-x-kpis"></div><div class="sa-grid2">' + card('sa-slot-main', slotTitle('main')) + card('sa-x-tins', 'Classes (CAKE tins)') + '</div>' +
                    '<div class="sa-grid2">' + card('sa-x-drops', 'Drops and ECN marks') + card('sa-x-top', 'Busiest hosts now') + '</div>' + card('sa-x-table', 'All hosts');
            },
            fill: function (P) {
                const i = P.iNow, tot = i >= 0 ? (P.bits[i] || 0) : 0;
                const limS = P.periods.reduce(function (s, x) { return s + (x[1] - x[0]) * P.step; }, 0);
                const sending = SH.hostsAt(P, i).filter(function (h) { return !h.other; }).length;
                const kpi = function (k, v, d) { return '<div class="sa-kpi"><p class="sa-kpi-k">' + k + '</p><p class="sa-kpi-v">' + v + '</p><p class="sa-meta">' + d + '</p></div>'; };
                document.getElementById('sa-x-kpis').innerHTML =
                    kpi(SA.esc(P.short) + ' now', SH.pct(tot / (P.cap || 1)) + ' of cap', SA.esc(SA.rate(tot) + ' of ' + SA.rate(P.cap))) +
                    kpi('State', P.busy ? SA.badge('warn', 'Limiting') : SA.badge('ok', 'Not limiting'), SA.esc(SH.dur(limS)) + ' limited in the window') +
                    kpi('Drops + ECN marks now', P.drops ? SH.num((P.drops[i] || 0) + ((P.marks || [])[i] || 0)) + ' /s' : '–', P.drops ? 'counted per class, not per host' : 'not a CAKE pipe') +
                    kpi('Hosts', SH.num(sending) + ' sending', SH.num(P.hosts.length + P.otherInfo.seen) + ' seen in the window');
                SH.tinsPart(document.getElementById('sa-x-tins'), P);
                SH.dropsStrip(document.getElementById('sa-x-drops'), P);
                document.getElementById('sa-x-top').innerHTML = SH.topList(M, P, 8);
                SH.hostsTable(document.getElementById('sa-x-table'), P);
            }
        },
        l2: {
            html: function () {
                return '<div class="sa-verdicts" id="sa-x-verdicts"></div>' +
                    '<section class="sa-story" aria-labelledby="sa-s1"><h3 id="sa-s1"><span class="sa-n">1.</span> Who is on the line now?</h3><p class="sa-lead" id="sa-x-who"></p><div id="sa-x-top"></div></section>' +
                    '<section class="sa-story" aria-labelledby="sa-s2"><h3 id="sa-s2"><span class="sa-n">2.</span> Is the shaping doing its job?</h3><p class="sa-lead" id="sa-x-drops"></p><div id="sa-slot-shaping"></div></section>' +
                    '<section class="sa-story" aria-labelledby="sa-s3"><h3 id="sa-s3"><span class="sa-n">3.</span> When were you limited?</h3><p class="sa-lead" id="sa-x-hist"></p><div id="sa-slot-when"></div></section>' +
                    '<section class="sa-story" aria-labelledby="sa-s4"><h3 id="sa-s4"><span class="sa-n">4.</span> All hosts</h3><div id="sa-x-table"></div></section>';
            },
            fill: function (P) {
                document.getElementById('sa-x-verdicts').innerHTML = M.pipes.map(function (Q) {
                    const v = SH.verdict(M, Q);
                    return '<section class="sa-verdict ' + (v.limiting ? 'sa-v-lim' : 'sa-v-ok') + '" aria-label="' + SA.esc(Q.title) + ': verdict"><h3>' +
                        (v.limiting ? SA.badge('warn', 'Limiting') : SA.badge('ok', 'Not limiting')) + ' ' + SA.esc(v.title) + '</h3>' +
                        v.lines.map(function (l) { return '<p>' + l + '</p>'; }).join('') + '</section>';
                }).join('');
                const v = SH.verdict(M, P);
                document.getElementById('sa-x-who').innerHTML = v.who;
                document.getElementById('sa-x-drops').innerHTML = (v.drops || 'This pipe has no per-class drop counters.') + (v.drops ? ' ' +
                    (v.dropsOk ? 'Bulk takes the pressure, as intended: interactive traffic stays smooth.' : 'If drops reach the interactive classes, the priorities may need a look.') : '');
                document.getElementById('sa-x-hist').textContent = v.history;
                document.getElementById('sa-x-top').innerHTML = SH.topList(M, P, 5);
                SH.hostsTable(document.getElementById('sa-x-table'), P);
            }
        },
        l3: {
            needs: ['g1'],
            html: function () {
                return '<p class="sa-meta">Pick a time on the left (drag across the graph, or use a shortcut); the right side explains it.</p><div class="sa-grid-md">' +
                    card('sa-slot-master', 'When: the shown window', '<div class="sa-chips" id="sa-x-chips"></div><div id="sa-slot-master"></div>') +
                    '<section class="sa-card" aria-labelledby="sa-x-dh"><h3 id="sa-x-dh" aria-live="polite"></h3><p id="sa-x-dlead"></p><div id="sa-x-river"></div>' +
                    '<h4>Who was on the line</h4><div id="sa-x-avg"></div></section></div>';
            },
            fill: function (P) {
                if (!l3sel || l3sel[1] > P.N) { l3sel = l3default(P); }
                const chips = P.periods.slice(-4).map(function (x) {
                    return '<button type="button" class="btn btn-default btn-sm" data-a="' + x[0] + '" data-b="' + Math.min(P.N, x[1]) + '">▲ Limited ' +
                        SA.esc(SH.span(P, x[0], x[1])) + '</button>';
                }).join('') + '<button type="button" class="btn btn-default btn-sm" data-a="' + Math.max(0, P.N - Math.ceil(P.N / 10)) + '" data-b="' + P.N + '">The newest tenth</button>' +
                    '<button type="button" class="btn btn-default btn-sm" data-a="0" data-b="' + P.N + '">The whole window</button>';
                const ch = document.getElementById('sa-x-chips');
                ch.innerHTML = '<span>Jump to:</span> ' + chips;
                if (insts.master && insts.master.setSel) { insts.master.setSel(l3sel); }
                l3detail(P);
            }
        },
        l4: {
            needs: ['g1'],
            html: function () {
                return '<div class="sa-readouts" id="sa-x-readouts"></div><div class="sa-grid2 sa-grid-wide">' + card('sa-slot-now', 'Who has the line right now') +
                    card('sa-x-top', 'Live top list') + '</div>' +
                    '<section class="sa-card"><ul class="nav nav-tabs" role="tablist" aria-label="More about this pipe">' +
                    [['hist', 'History'], ['cls', 'Classes'], ['all', 'All hosts']].map(function (t, k) {
                        return '<li role="presentation"' + (k === 0 ? ' class="active"' : '') + '><a href="#sa-p-' + t[0] + '" role="tab" id="sa-t-' + t[0] + '" aria-controls="sa-p-' + t[0] +
                            '" aria-selected="' + (k === 0) + '" tabindex="' + (k === 0 ? 0 : -1) + '">' + t[1] + '</a></li>';
                    }).join('') + '</ul>' + ['hist', 'cls', 'all'].map(function (t, k) {
                        return '<div role="tabpanel" id="sa-p-' + t + '" aria-labelledby="sa-t-' + t + '"' + (k ? ' hidden' : '') + '>' +
                            (t === 'hist' ? '<div id="sa-x-hist"></div><h4>Drops and ECN marks</h4><div id="sa-x-drops"></div>' : t === 'cls' ? '<div id="sa-x-tins"></div>' : '<div id="sa-x-table"></div>') + '</div>';
                    }).join('') + '</section>';
            },
            fill: function (P) {
                document.getElementById('sa-x-readouts').innerHTML = M.pipes.map(SH.readout).join('');
                document.getElementById('sa-x-top').innerHTML = SH.topList(M, P, 8);
                const tab = (document.querySelector('#sa-view [role=tab][aria-selected=true]') || {id: 'sa-t-hist'}).id.slice(5);
                if (tab === 'hist') {
                    if (!fixed.hist) { fixed.hist = SH.graphs.g1.create(document.getElementById('sa-x-hist'), {height: 240}); }
                    fixed.hist.update(M, P);
                    SH.dropsStrip(document.getElementById('sa-x-drops'), P);
                } else if (tab === 'cls') {
                    SH.tinsPart(document.getElementById('sa-x-tins'), P);
                } else {
                    SH.hostsTable(document.getElementById('sa-x-table'), P);
                }
            }
        },
        l5: {
            html: function () {
                return '<section class="sa-card" aria-labelledby="sa-x-sum-h"><h3 id="sa-x-sum-h">Every pipe now</h3><div class="sa-readouts sa-readouts-slim" id="sa-x-readouts"></div></section>' +
                    card('sa-slot-top', slotTitle('top')) + card('sa-x-table', 'Hosts') +
                    '<section class="sa-card"><details><summary><h3 class="sa-inline-h">Per class (CAKE tins)</h3></summary><div id="sa-x-tins"></div></details></section>';
            },
            fill: function (P) {
                document.getElementById('sa-x-readouts').innerHTML = M.pipes.map(SH.readout).join('');
                SH.hostsTable(document.getElementById('sa-x-table'), P);
                SH.tinsPart(document.getElementById('sa-x-tins'), P);
            }
        }
    };
    function l3detail(P) {
        const a = l3sel[0], b = l3sel[1];
        let lim = 0;
        for (let i = a; i < b; i++) { lim += P.lim[i] ? 1 : 0; }
        const tot = SH.avg(P.bits, a, b);
        const hs = SH.hostsOver(P, a, b).filter(function (h) { return !h.other; });
        let acc = 0, k = 0;
        while (k < hs.length && acc < tot * 0.6 && k < 3) { acc += hs[k].v; k++; }
        const names = hs.slice(0, k).map(function (h) { return '<bdi>' + SA.esc(SH.hostName(h.ip)) + '</bdi>'; });
        const td = P.tinDrops.map(function (x) { return SH.sum(x, a, b); });
        const all = td.reduce(function (s, x) { return s + x; }, 0);
        const bk = P.tins.indexOf('Bulk');
        const span = (b - a) * P.step;
        document.getElementById('sa-x-dh').textContent = 'Selected: ' + SH.span(P, a, b) + ' (' + SH.dur(span) + ')';
        document.getElementById('sa-x-dlead').innerHTML = (lim ? SA.badge('warn', 'Limited ' + SH.dur(lim * P.step) + ' of ' + SH.dur(span)) : SA.badge('ok', 'Not limited')) + ' ' +
            SA.esc(P.short) + ' averaged ' + SA.esc(SA.rate(tot)) + ' (' + SH.pct(tot / (P.cap || 1)) + ' of the cap). ' +
            (k ? (k === 1 ? names[0] + ' took ' : SH.list(names) + ' took ') + SH.pct(acc / (P.cap || 1)) + ' of the line. ' : '') +
            (all > 0.05 ? (bk >= 0 ? SH.pct(td[bk] / all) + ' of the drops were in Bulk (drops are counted per class).' : '') : 'No drops.');
        const lo = Math.max(0, a - Math.round((b - a) * 0.25)), hi = Math.min(P.N, b + Math.round((b - a) * 0.25));
        if (!fixed.river) { fixed.river = SH.graphs.g1.create(document.getElementById('sa-x-river'), {height: 220, range: function () { return fixed.range; }}); }
        fixed.range = [lo, Math.max(hi, Math.min(P.N, lo + 6))];
        fixed.river.update(M, P);
        const th = SH.theme();
        SA.table('#sa-x-avg', [
            {title: 'Name', field: 'name', type: 'html', swatch: true, sortField: 'sortName'},
            {title: 'IP', field: 'ip', type: 'ip', wide: true},
            {title: 'Average rate', field: 'rate', type: 'num', fmt: function (v) { return SA.rate(v); }},
            {title: 'Share of cap', field: 'share', type: 'num', fmt: function (v) { return SH.pct(v); }}
        ], SH.hostsOver(P, a, b).map(function (h) {
            return {colour: h.other ? th.other : h.colour, name: h.other ? 'other hosts (summed)' : SH.nameHtml(h.ip), sortName: h.other ? '~' : (SH.extra.names[h.ip] || h.ip),
                ip: h.other || h.ip === 'this firewall' ? '' : h.ip, rate: h.v, share: h.v / (P.cap || 1)};
        }), {sort: ['rate', 'desc'], label: P.title + ': average per host over the selected time', scroll: true, empty: 'No host traffic in the selected time.'});
    }
    document.addEventListener('click', function (e) {
        const b = e.target.closest && e.target.closest('#sa-x-chips button[data-a]');
        if (!b || !M) { return; }
        const P = M.byId[pipeN];
        l3sel = [Number(b.getAttribute('data-a')), Number(b.getAttribute('data-b'))];
        if (insts.master && insts.master.setSel) { insts.master.setSel(l3sel); }
        l3detail(P);
    });
    /* L4 tabs: arrow keys, Home and End (WAI-ARIA APG tabs) */
    function showTab(t) {
        document.querySelectorAll('#sa-view [role=tab]').forEach(function (a) {
            const on = a.id === 'sa-t-' + t;
            a.setAttribute('aria-selected', String(on)); a.tabIndex = on ? 0 : -1; a.parentNode.classList.toggle('active', on);
            document.getElementById(a.getAttribute('aria-controls')).hidden = !on;
        });
        if (M) { L.l4.fill(M.byId[pipeN]); }
    }
    document.addEventListener('click', function (e) {
        const a = e.target.closest && e.target.closest('#sa-view [role=tab]');
        if (a) { e.preventDefault(); showTab(a.id.slice(5)); a.focus(); }
    });
    document.addEventListener('keydown', function (e) {
        const a = e.target.closest && e.target.closest('#sa-view [role=tab]');
        if (!a) { return; }
        const tabs = Array.from(document.querySelectorAll('#sa-view [role=tab]')), i = tabs.indexOf(a);
        const j = e.key === 'ArrowRight' ? (i + 1) % tabs.length : e.key === 'ArrowLeft' ? (i + tabs.length - 1) % tabs.length :
            e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : null;
        if (j !== null) { e.preventDefault(); showTab(tabs[j].id.slice(5)); tabs[j].focus(); }
    });

    /* ------------------------------------------------------------------ build and update */
    function files(v) {
        const need = {};
        LAYOUTS[v.layout].slots.forEach(function (s) { need[v.graphs[s.id]] = 1; });
        (L[v.layout].needs || []).forEach(function (g) { need[g] = 1; });
        let out = [];
        Object.keys(need).forEach(function (g) { out = out.concat(window.SA_LAZY[g] || []); });
        return out;
    }
    function destroyAll() {
        Object.keys(insts).forEach(function (k) { insts[k].destroy(); });
        Object.keys(fixed).forEach(function (k) { if (fixed[k] && fixed[k].destroy) { fixed[k].destroy(); } });
        insts = {}; fixed = {};
        if (root()) { SH.destroyCharts(root()); }
    }
    function rebuild() {
        if (!M) { return; }
        const P = M.byId[pipeN];
        const my = ++token;
        destroyAll();
        l3sel = null;
        built = view.layout + JSON.stringify(view.graphs) + pipeN;
        const r = root();
        r.setAttribute('aria-busy', 'true');
        r.innerHTML = '<p class="sa-meta">Loading the graph...</p>';
        SH.load(files(view)).then(function () {
            if (my !== token) { return; }
            r.innerHTML = '<h3 class="sa-pipe-title" id="sa-pipe-title"></h3><p class="sa-meta" id="sa-pipe-state" aria-live="polite"></p><p class="sa-meta" id="sa-pipe-cap"></p>' + L[view.layout].html();
            LAYOUTS[view.layout].slots.forEach(function (s) {
                const g = view.graphs[s.id];
                const el = document.getElementById('sa-slot-' + s.id);
                insts[s.id] = SH.graphs[g].create(el, view.layout === 'l3' ? {select: function (a, b) { l3sel = [a, b]; l3detail(M.byId[pipeN]); }} :
                    {height: view.layout === 'l1' ? 300 : view.layout === 'l5' ? 220 : undefined, table: view.layout !== 'l5'});
            });
            r.removeAttribute('aria-busy');
            update(P);
        }, function (err) {
            r.removeAttribute('aria-busy');
            r.innerHTML = '<p>' + SA.badge('bad', 'Could not load the graph') + ' ' + SA.esc(err.message) + '</p>';
            document.body.setAttribute('data-sa-ready', '1');
        });
    }
    function update(P) {
        if (!document.getElementById('sa-pipe-title')) { return; }
        document.getElementById('sa-pipe-title').textContent = P.title + (P.where.length ? ' · ' + P.where.join(', ') : '');
        document.getElementById('sa-pipe-state').innerHTML = SH.stateHtml(P);
        document.getElementById('sa-pipe-cap').textContent = SH.capText(P);
        L[view.layout].fill(P);
        Object.keys(insts).forEach(function (k) { insts[k].update(M, P); });
        document.body.setAttribute('data-sa-ready', '1');
    }
    function needAll() {
        return Object.values(view.graphs).some(function (g) { return GRAPHS[g].perHost; });
    }

    /* ------------------------------------------------------------------ data: one full answer, then ?since= */
    let windowS = 3600, cached = null, cachedAt = 0, cachedAll = false, poller = null, extraAt = 0;
    function loadExtra() {
        if (Date.now() - extraAt < 60000) { return Promise.resolve(); }
        extraAt = Date.now();
        return Promise.all([
            SA.get('/api/shaperinsight/devices/list').then(function (r) {
                (r.devices || []).forEach(function (x) { if (x.hostname) { SH.extra.names[x.address] = x.hostname; } });
                SH.extra.aliases = {};
                (r.aliases || []).forEach(function (a) { SH.extra.aliases[a.name] = a; });
            }).catch(function () {}),
            SA.get('/api/shaperinsight/sampler/hosts').then(function (r) { SH.extra.hostInfo = r.hosts || {}; }).catch(function () {}),
            SA.get('/api/shaperinsight/priorities/status').then(function (r) { SH.extra.prio = r; }).catch(function () {})
        ]);
    }
    function render(d) {
        SAsharingTop.status(d);
        M = SH.build(d);
        if (!M.pipes.length) {
            destroyAll();
            built = '';
            root().innerHTML = '<p>' + SA.badge('info', 'Nothing recorded yet') + ' The first points appear 20 s after the sampler starts, for pipes that carry traffic.</p>';
            document.getElementById('sa-pipebar').innerHTML = '';
            document.body.setAttribute('data-sa-ready', '1');
            return;
        }
        if (!M.byId[pipeN]) {
            const busy = M.pipes.filter(function (P) { return P.busy; });
            pipeN = (busy[0] || M.pipes.slice().sort(function (a, b) { return (b.bits[b.iNow] || 0) - (a.bits[a.iNow] || 0); })[0]).n;
        }
        if (!document.getElementById('sa-layout')) { drawBar(); }
        else {
            /* option labels follow what the sampler stores; the rest of the bar stays (focus, open details) */
            document.querySelectorAll('#sa-viewbar select[data-slot] option').forEach(function (o) { o.textContent = graphOption(o.value); });
            document.querySelectorAll('#sa-pipebar button[data-pipe]').forEach(function (b) {
                const P = M.byId[b.getAttribute('data-pipe')];
                if (P) { b.textContent = P.short + (P.busy ? ' ▲' : ''); }
            });
            if (!document.querySelector('#sa-pipebar button') && M.pipes.length > 1) { drawBar(); }
        }
        const key = view.layout + JSON.stringify(view.graphs) + pipeN;
        if (key !== built || !document.getElementById('sa-pipe-title')) { rebuild(); } else { update(M.byId[pipeN]); }
    }
    function load() {
        loadExtra();
        const all = needAll();
        const url = '/api/shaperinsight/sampler/history/' + windowS;
        const fresh = cached && cached.window === windowS && cachedAll === all && Date.now() - cachedAt < 60000;
        const last = fresh ? SAdelta.lastRow(cached) : null;
        const qs = function (since) { return (since !== null || all ? '?' : '') + (since !== null ? 'since=' + since : '') + (since !== null && all ? '&' : '') + (all ? 'all=1' : ''); };
        return SA.get(url + qs(last)).then(function (d) {
            const m = SAdelta.merge(cached, d);
            return m === null ? SA.get(url + qs(null)) : m;
        }).then(function (d) {
            cached = d; cachedAt = Date.now(); cachedAll = all;
            render(d);
        }, function (e) {
            cached = null;
            SAsharingTop.failed(e);
            document.body.setAttribute('data-sa-ready', '1');
        });
    }
    /* a view change that needs (or no longer needs) every host's series asks for a full answer */
    const rebuild0 = rebuild;
    rebuild = function () { if (cachedAll !== needAll() && poller) { cached = null; poller.now(); return; } rebuild0(); };

    window.SAviews = {
        combos: combos,
        current: function () { return {layout: view.layout, graphs: LAYOUTS[view.layout].slots.map(function (s) { return view.graphs[s.id]; }), source: source}; },
        start: function (seconds) {
            loadExtra().finally(function () { poller = SA.poll(load, Math.max(10, seconds || 10)); });
        },
        setWindow: function (w) { windowS = w; cached = null; destroyAll(); built = ''; if (poller) { poller.now(); } },
        refresh: function () { if (poller) { poller.now(); } },
        restart: function (seconds) { if (poller) { poller.stop(); } poller = SA.poll(load, Math.max(10, seconds || 10)); }
    };
}());
