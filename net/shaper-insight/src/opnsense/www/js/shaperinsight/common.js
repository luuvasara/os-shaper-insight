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
 *
 * Shared helpers for the Shaper Insight pages: escaping, number formats, glossary
 * term chips ({{term:id}} markers), "Why?" evidence dialogs, status badges that never
 * rely on colour alone, and a poller that pauses while the tab is hidden.
 */

window.SA = (function () {
    'use strict';
    let glossary = {terms: {}, claims: {}};
    const ICON = {ok: 'fa-check-circle', info: 'fa-info-circle', warn: 'fa-exclamation-triangle', bad: 'fa-times-circle'};
    const WORD = {ok: 'OK', info: 'Note', warn: 'Check', bad: 'Problem'};
    const CLS = {ok: 'success', info: 'info', warn: 'warning', bad: 'danger'};

    function esc(s) {
        return String(s === undefined || s === null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    /* "{{term:cake}} is running" -> escaped text with a chip button for each known term */
    function words(text) {
        /* {{term:id}} shows the glossary name, {{term:id|word}} the word as written */
        return esc(text).replace(/\{\{term:([a-z0-9-]+)(?:\|([^}]+))?\}\}/g, function (m, id, word) {
            const t = glossary.terms[id];
            const label = word || (t ? t.name : id);
            return '<button type="button" class="sa-term" data-term="' + id + '" aria-haspopup="dialog">' +
                esc(label) + '</button>';
        });
    }

    /* a chip for one term, optionally with its own label ("Drops" for the term "drop") */
    function chip(id, label) {
        const t = glossary.terms[id];
        return '<button type="button" class="sa-term" data-term="' + esc(id) + '" aria-haspopup="dialog">' +
            esc(label || (t ? t.name : id)) + '</button>';
    }

    /* status word with an icon; the word carries the meaning, the theme colours only the icon,
       because the stock label-* backgrounds fail contrast with white text (1.8:1) */
    function badge(level, text) {
        level = CLS[level] ? level : 'info';
        return '<span class="sa-badge sa-badge-' + level + '"><i class="fa ' + ICON[level] + ' text-' + CLS[level] +
            '" aria-hidden="true"></i> ' + esc(text || WORD[level]) + '</span>';
    }

    function why(claim) {
        if (!claim) {
            return '';
        }
        return ' <button type="button" class="sa-why btn btn-link btn-xs" data-claim="' + esc(claim) +
            '">Why?</button>';
    }

    /* several registry claims behind one reason: one button each, named by the claim, plus a
       note when the registry marks it contested (our own sources disagree) */
    function whys(ids) {
        return (ids || []).map(function (id) {
            const c = glossary.claims[id];
            if (!c) {
                return '';
            }
            return ' <button type="button" class="sa-why btn btn-link btn-xs" data-claim="' + esc(id) + '" title="' +
                esc(c.text) + '" aria-label="' + esc('Why: ' + c.text) + '">Why?</button>' +
                (c.contested ? ' <span class="sa-contested">(experts disagree)</span>' : '');
        }).join('');
    }

    function rate(bps) {
        if (bps === null || bps === undefined) {
            return '–';
        }
        const units = [['Gbit/s', 1e9], ['Mbit/s', 1e6], ['kbit/s', 1e3]];
        for (const [u, d] of units) {
            if (bps >= d) {
                return sig(bps / d) + ' ' + u;
            }
        }
        return Math.round(bps) + ' bit/s';
    }

    /* 3 significant figures, at most no decimals above 100 */
    function sig(v) {
        if (v >= 100) {
            return Math.round(v).toLocaleString();
        }
        return Number(v.toPrecision(3)).toLocaleString();
    }

    function num(v) {
        return (v === null || v === undefined) ? '–' : Number(v).toLocaleString();
    }

    function ms(us) {
        return (us === null || us === undefined) ? '–' : sig(us / 1000) + ' ms';
    }

    /* the six labelled blocks of a glossary entry, for the dialog and the glossary page */
    function termBlocks(t) {
        const src = t.source || {};
        const urls = (t.urls || []).length ? t.urls : (src.url ? [src.url] : []);
        const row = function (label, text) { return text ? '<dt>' + label + '</dt><dd>' + esc(text) + '</dd>' : ''; };
        return '<dl class="sa-def">' +
            row('In plain words', t.plain) +
            (t.say && t.say !== t.plain ? row('The correct term', t.say) : '') +
            row('Where it applies', t.applies) +
            row('Example', t.ui_example || t.example) +
            row('People say', t.people) +
            row('Common mix-up', t.mixup) +
            (t.details || []).map(function (d) { return row(d.label, d.text); }).join('') +
            '<dt>Source' + (t.secondary || t.source_note ? ' (' + esc(t.source_note || 'secondary') + ')' : '') + '</dt><dd>' +
            (t.source_text ? esc(t.source_text) : (src.quote ? '<q>' + esc(src.quote) + '</q>' : '')) +
            urls.map(function (u) {
                return '<br><a href="' + esc(u) + '" target="_blank" rel="noopener noreferrer">' + esc(u) + '</a>';
            }).join('') + '</dd></dl>';
    }

    function termDialog(id) {
        const t = glossary.terms[id];
        if (!t || id.charAt(0) === '_') {
            return;
        }
        BootstrapDialog.show({title: t.name, type: BootstrapDialog.TYPE_INFO,
            message: termBlocks(t) + '<p><a href="/ui/shaperinsight/glossary#t-' + esc(id) + '">Open in the glossary</a></p>',
            buttons: [{label: 'Close', action: function (d) { d.close(); }}]});
    }

    function claimDialog(id) {
        const c = glossary.claims[id];
        if (!c) {
            return;
        }
        const grades = {
            'measured-here': 'Measured on this firewall',
            'measured-lab': 'Measured in a lab',
            'cited': 'From the source below, not measured here',
            'example': 'Typical example from the source, not measured here',
            'expert': 'Expert opinion from the sources below, not measured here',
            'inference': 'Our reasoning (inference): not yet proven'
        };
        let html = '<p><strong>' + esc(c.text) + '</strong></p>' +
            '<p class="sa-grade">' + badge(c.grade === 'inference' ? 'warn' : 'info', grades[c.grade] || c.grade) + '</p>' +
            '<h4>How it works</h4><p>' + esc(c.mechanism) + '</p><h4>Source</h4>';
        (c.sources || []).forEach(function (s) {
            html += '<blockquote><p>' + esc(s.quote) + '</p><footer>' + esc(s.locator || '') +
                (s.secondary ? ' (secondary source)' : '') + ', <a href="' + esc(s.url) +
                '" target="_blank" rel="noopener noreferrer">' + esc(s.url) + '</a></footer></blockquote>';
        });
        if (c.contested) {
            html += '<h4>Where our sources disagree</h4><p>' + esc(c.contested) + '</p>';
        }
        if (c.verify) {
            html += '<h4>Check it here</h4><p>' + esc(c.verify) + '</p>';
        }
        BootstrapDialog.show({title: 'Why we say this', message: html,
            buttons: [{label: 'Close', action: function (d) { d.close(); }}]});
    }

    let glossaryLoad = null;
    function loadGlossary() {
        glossaryLoad = glossaryLoad || new Promise(function (resolve) {
            ajaxGet('/api/shaperinsight/glossary/get', {}, function (data) {
                if (data && data.terms) {
                    glossary = data._probe === '&lt;' ? unescapeApi(data) : data;
                }
                resolve(glossary);
            });
        });
        return glossaryLoad;
    }

    $(document).on('click', '.sa-term', function () { termDialog($(this).data('term')); });
    /* static page text marks a term with <span class="sa-chip-slot" data-term="id">word</span>;
       it becomes a chip once the glossary is loaded (a plain word until then, and without JS) */
    $(function () {
        if ($('.sa-chip-slot').length) {
            loadGlossary().then(function () {
                $('.sa-chip-slot').each(function () {
                    const id = $(this).data('term');
                    if (glossary.terms[id]) { $(this).replaceWith(chip(id, $(this).text())); }
                });
            });
        }
    });
    $(document).on('click', '.sa-why', function () { claimDialog($(this).data('claim')); });

    /*
     * Every technical term the pages show gets a chip (user feedback 2026-11-22: "WF2Q+ has no
     * tooltip, nor why?"): after each render, the first mention of a known word in every table
     * cell, and the first on the page outside tables, becomes a chip unless one is there already.
     * Raw text (pre, code), controls, links, headings and the glossary page itself are left alone.
     * tools/shots/terms-check.js checks the rendered pages for the same rule.
     */
    const AUTO_TERMS = [['Deficit Round Robin', 'drr'], ['WF2Q+', 'wf2q'], ['FQ-CoDel', 'fq-codel'], ['FQ-PIE', 'fq-pie'],
        ['QFQ', 'qfq'], ['FIFO', 'fifo-tail-drop'], ['PRIO', 'priority-strict'], ['CAKE', 'cake'], ['DSCP', 'dscp'],
        ['ECN', 'ecn'], ['AQM', 'aqm']].map(function (x) {
        return {word: x[0], id: x[1], re: new RegExp('(^|[^A-Za-z0-9+-])(' + x[0].replace(/[+]/g, '\\+') + ')(?![A-Za-z0-9+])')};
    });
    const AUTO_SKIP = 'pre, code, script, style, select, option, textarea, button, a, h1, h2, h3, h4, h5, h6, label, ' +
        'summary, canvas, nav, aside, header, footer, .modal, .bootstrap-dialog, .sa-term, .sa-no-terms, .bootgrid-header';

    function autoTerms(root) {
        if (!Object.keys(glossary.terms).length || /\/glossary\/?$/.test(location.pathname)) {
            return;
        }
        /* the page body: OPNsense's layout can hold more than one .page-content-main */
        root = root || document.querySelector('main') || document.body;
        const nodes = [];
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        let n;
        while ((n = walker.nextNode())) {
            if (n.parentElement && !n.parentElement.closest(AUTO_SKIP)) {
                nodes.push(n);
            }
        }
        nodes.forEach(function (node) {
            /* earliest wanted match first, then go on in the text after it */
            while (node && node.parentNode) {
                const scope = node.parentElement.closest('td, th, .tabulator-cell') || root;
                let best = null;
                AUTO_TERMS.forEach(function (t) {
                    if (!glossary.terms[t.id] || scope.querySelector('.sa-term[data-term="' + t.id + '"]')) {
                        return;
                    }
                    const m = t.re.exec(node.textContent);
                    if (m && (best === null || m.index + m[1].length < best.at)) {
                        best = {t: t, at: m.index + m[1].length};
                    }
                });
                if (best === null) {
                    return;
                }
                const rest = node.splitText(best.at);
                rest.textContent = rest.textContent.slice(best.t.word.length);
                const holder = document.createElement('span');
                holder.innerHTML = chip(best.t.id, best.t.word);
                rest.parentNode.insertBefore(holder.firstChild, rest);
                node = rest;
            }
        });
    }

    $(function () {
        const root = document.querySelector('main') || document.body;
        if (!window.MutationObserver) {
            return;
        }
        let timer = null;
        new MutationObserver(function () {
            if (timer === null) {
                timer = setTimeout(function () { timer = null; autoTerms(root); tooltips(root); }, 150);
            }
        }).observe(root, {childList: true, subtree: true});
        loadGlossary().then(function () { autoTerms(root); });
    });

    /*
     * SA.ref(kind, name, info): an alias, pipe, queue, rule, interface or gateway name as core's
     * firewall rules grid shows an alias (26.7 filter_rule.volt): the name with its content as a
     * tooltip, then a list icon that opens the object in its core page ("#tab&edit=UUID" deep links
     * are handled by core's opnsense_ui.js). info: {uuid, content, key (interface)}. The icon link gets
     * an accessible name, which core's lacks.
     */
    const REFS = {
        alias: ['/ui/firewall/alias/index#aliases', 'Edit alias'],
        pipe: ['/ui/trafficshaper#pipes', 'Edit pipe'],
        queue: ['/ui/trafficshaper#queues', 'Edit queue'],
        rule: ['/ui/trafficshaper#rules', 'Edit shaper rule'],
        interface: ['/interfaces.php?if=', 'Edit interface'],
        gateway: ['/ui/routing/configuration', 'Edit gateways']
    };

    function ref(kind, name, info) {
        info = info || {};
        const r = REFS[kind];
        const tip = info.content || info.description || name;
        let href = null;
        if (r && kind === 'interface') {
            href = info.key ? r[0] + encodeURIComponent(info.key) : null;
        } else if (r) {
            href = r[0] + (info.uuid ? (r[0].indexOf('#') >= 0 ? '&' : '#') + 'edit=' + encodeURIComponent(info.uuid) : '');
        }
        return '<span class="sa-ref" tabindex="0" data-toggle="tooltip" data-html="true" title="' + esc(esc(tip)) + '">' +
            esc(name) + '&nbsp;</span>' + (href ? '<a href="' + esc(href) + '" data-toggle="tooltip" title="' + esc(r[1]) +
            '" aria-label="' + esc(r[1] + ' ' + name) + '"><i class="fa fa-fw fa-list" aria-hidden="true"></i></a>' : '');
    }

    function tooltips(root) {
        if ($.fn.tooltip) {
            $(root || document).find('[data-toggle="tooltip"]').not('[data-sa-tip]').attr('data-sa-tip', '1')
                .tooltip({container: 'body'});
        }
    }

    /*
     * SA.table(el, columns, rows, opts): every table in the plugin (user feedback 2026-11-22: sortable columns
     * with sensible defaults, legend swatches where a row has a chart series, numbers right-aligned,
     * an empty state, usable at 390 px), built on core's Tabulator (26.7 loads it on every MVC page,
     * Base/ControllerBase.php). One instance per element id: a refresh replaces the data and keeps the
     * sort, also when the page re-renders the element.
     *   columns: [{title, field, type: 'text' | 'num' | 'ip' | 'html', fmt(value, row) for num/text display,
     *              swatch: true (row.colour), sortField (sort by another field), sort: false}]
     *   rows: plain objects; html cells are markup built with esc()/words(); rows with _children expand
     *   opts: {sort: [field, 'asc' | 'desc'], empty: text, label: accessible name, tree: rows may expand later,
     *          scroll: long list (own scroll box, virtual rendering beyond 100 rows)}
     *   column wide: true hides it at 767 px or less
     *   a num column with html: true shows fmt()'s markup as is (build it with esc())
     * Tabulator's sortable headers are not focusable: they get tabindex 0, and Enter or Space sorts.
     * tools/shots/tables-check.js checks every table on the rendered pages for this.
     */
    const tables = {};
    const tableSorts = {};

    function textOf(html) {
        const d = document.createElement('div');
        d.innerHTML = html || '';
        return d.textContent;
    }

    function natural(a, b) {
        return String(a === null || a === undefined ? '' : a).localeCompare(
            String(b === null || b === undefined ? '' : b), undefined, {numeric: true, sensitivity: 'base'});
    }

    function numeric(a, b) {
        const x = (a === null || a === undefined || a === '') ? -Infinity : Number(a);
        const y = (b === null || b === undefined || b === '') ? -Infinity : Number(b);
        return x === y ? 0 : (x < y ? -1 : 1);
    }

    function swatch(colour) {
        if (colour === 'hatch' || /^tin-[0-3]$/.test(colour || '')) {
            return '<span class="sa-swatch sa-' + colour + '" aria-hidden="true"></span>';
        }
        return colour ? '<span class="sa-swatch" style="background:' + esc(colour) + '" aria-hidden="true"></span>' : '';
    }

    function columnDef(c) {
        const d = {title: c.title, field: c.field, headerSort: c.sort !== false, vertAlign: 'middle',
            minWidth: c.minWidth || 48, resizable: false, headerWordWrap: true, variableHeight: true,
            /* wide: hidden on a phone (767 px or less), where the row shows it as a second line instead */
            visible: !(c.wide && window.matchMedia && window.matchMedia('(max-width: 767px)').matches)};
        let show;
        if (c.type === 'num') {
            d.hozAlign = 'right';
            d.headerHozAlign = 'right';
            d.sorter = numeric;
            show = function (v, row) {
                const out = c.fmt ? c.fmt(v, row) : (v === null || v === undefined ? '–' : v);
                return c.html ? out : esc(out);
            };
        } else if (c.type === 'ip') {
            d.sorter = function (a, b) { return window.SAip ? SAip.compare(a, b) : natural(a, b); };
            show = function (v) { return esc(v || ''); };
        } else if (c.type === 'html') {
            d.sorter = function (a, b) { return natural(textOf(a), textOf(b)); };
            show = function (v) { return v || ''; };
        } else {
            d.sorter = natural;
            show = function (v, row) { return esc(c.fmt ? c.fmt(v, row) : (v === null || v === undefined ? '' : v)); };
        }
        if (c.sortField) {
            d.sorter = function (a, b, ra, rb) {
                const x = ra.getData()[c.sortField];
                const y = rb.getData()[c.sortField];
                return typeof x === 'number' && typeof y === 'number' ? numeric(x, y) : natural(x, y);
            };
        }
        d.formatter = function (cell) {
            const row = cell.getRow().getData();
            return (c.swatch ? swatch(row.colour) : '') + show(cell.getValue(), row);
        };
        return d;
    }

    /* Tabulator makes its scroll box focusable (tabindex 0) inside role=grid, which breaks the grid's
       row structure for screen readers (axe aria-required-children). Only a box that really scrolls
       sideways needs to be keyboard-reachable (axe scrollable-region-focusable): keep it there only. */
    function fixScrollFocus(node) {
        const holder = node.querySelector('.tabulator-tableholder');
        if (!holder) {
            return;
        }
        if (holder.scrollWidth > holder.clientWidth + 2 || holder.scrollHeight > holder.clientHeight + 2) {
            holder.setAttribute('tabindex', '0');
        } else {
            holder.removeAttribute('tabindex');
        }
    }

    function keyboardSort(t) {
        t.getColumns().forEach(function (col) {
            if (col.getDefinition().headerSort === false) {
                return;
            }
            const h = col.getElement();
            if (h.getAttribute('tabindex') === '0') {
                return;
            }
            h.setAttribute('tabindex', '0');
            h.addEventListener('keydown', function (e) {
                if (e.key !== 'Enter' && e.key !== ' ') {
                    return;
                }
                e.preventDefault();
                const cur = t.getSorters().find(function (s) { return s.field === col.getField(); });
                t.setSort(col.getField(), cur && cur.dir === 'asc' ? 'desc' : 'asc');
            });
        });
    }

    function table(el, columns, rows, opts) {
        opts = opts || {};
        const node = el && el.jquery ? el[0] : (typeof el === 'string' ? document.querySelector(el) : el);
        if (!node || typeof Tabulator === 'undefined') {
            return null;
        }
        const id = node.id;
        let have = tables[id];
        /* up to 100 rows every row is in the page; beyond, only the visible window is (host simulation: 1,000 rows
           are 14,434 DOM nodes and 148 ms a sort, a 40-row window 5 ms) */
        const virtual = !!opts.scroll && rows.length > 100;
        if (have && have.element === node && have._saVirtual !== virtual) {
            tableSorts[id] = have.getSorters().map(function (s) { return {column: s.field, dir: s.dir}; });
            have.destroy();
            delete tables[id];
            have = null;
        }
        if (have && have.element === node) {
            have.replaceData(rows);
            return have;
        }
        const sort = tableSorts[id] || (opts.sort ? [{column: opts.sort[0], dir: opts.sort[1]}] : []);
        const tree = !!opts.tree || rows.some(function (r) { return r._children && r._children.length; });
        const t = new Tabulator(node, {
            /* narrow (phone): columns share the width and their text wraps, so the page does not scroll
               sideways (390 px); wide: each column as wide as its data, the table no wider than that */
            data: rows, columns: columns.map(columnDef), layout: node.clientWidth < 700 ? 'fitColumns' : 'fitDataTable', initialSort: sort,
            placeholder: opts.empty || 'Nothing to show.', dataTree: tree, dataTreeStartExpanded: false,
            dataTreeChildIndent: 14,
            dataTreeExpandElement: '<button type="button" class="sa-tree" aria-expanded="false" aria-label="Show the grouped rows">+</button>',
            dataTreeCollapseElement: '<button type="button" class="sa-tree" aria-expanded="true" aria-label="Hide the grouped rows">−</button>',
            /* a long list scrolls in its own box (header stays in view), at most min(60vh, 640px) */
            maxHeight: opts.scroll ? 'min(60vh, 640px)' : false, renderVertical: virtual ? 'virtual' : 'basic'
        });
        t._saVirtual = virtual;
        node.classList.add('sa-grid');
        if (columns.some(function (c) { return c.swatch; })) {
            node.setAttribute('data-sa-swatch', '1');   /* tables-check.js: rows must show their chart colour */
        }
        if (opts.label) {
            node.setAttribute('aria-label', opts.label);
        }
        t.on('tableBuilt', function () {
            keyboardSort(t);
            fixScrollFocus(node);
        });
        t.on('renderComplete', function () { fixScrollFocus(node); });
        t.on('dataSorted', function (sorters) {
            tableSorts[id] = sorters.map(function (s) { return {column: s.field, dir: s.dir}; });
        });
        tables[id] = t;
        return t;
    }

    /*
     * poll(fn, seconds): call fn() now and every `seconds` while the tab is visible;
     * fn returns a promise. Returns {stop(), now()}.
     */
    function poll(fn, seconds) {
        let timer = null;
        let busy = false;
        const interval = Math.max(5, seconds || 5) * 1000;
        function tick() {
            timer = null;
            if (document.visibilityState === 'hidden') {
                return;
            }
            if (busy) {
                schedule();
                return;
            }
            busy = true;
            Promise.resolve(fn()).finally(function () {
                busy = false;
                schedule();
            });
        }
        function schedule() {
            if (timer === null && document.visibilityState !== 'hidden') {
                timer = setTimeout(tick, interval);
            }
        }
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible' && timer === null && !busy) {
                tick();
            } else if (document.visibilityState === 'hidden' && timer !== null) {
                clearTimeout(timer);
                timer = null;
            }
        });
        tick();
        return {
            now: function () { if (timer !== null) { clearTimeout(timer); timer = null; } tick(); },
            stop: function () { if (timer !== null) { clearTimeout(timer); } timer = -1; }
        };
    }

    /* undo the API's HTML escaping of browser responses once (see the controllers' PROBE),
       so esc() below is the only escaping and copied text is the real text */
    function unescapeApi(v) {
        if (typeof v === 'string') {
            return v.replace(/&(lt|gt|amp);/g, function (m, e) { return {lt: '<', gt: '>', amp: '&'}[e]; });
        }
        if (Array.isArray(v)) { return v.map(unescapeApi); }
        if (v && typeof v === 'object') {
            const o = {};
            Object.keys(v).forEach(function (k) { o[k] = unescapeApi(v[k]); });
            return o;
        }
        return v;
    }

    function get(url) {
        return new Promise(function (resolve, reject) {
            $.ajax({url: url, type: 'GET', dataType: 'json', timeout: 30000})
                .done(function (d) { resolve(d && d._probe === '&lt;' ? unescapeApi(d) : d); })
                .fail(function (xhr, status) { reject(new Error(status || 'request failed')); });
        });
    }

    function cssVar(name, fallback) {
        const v = getComputedStyle(document.body).getPropertyValue(name).trim();
        return v || fallback;
    }

    /* label every cell with its column header, so narrow screens can show rows as cards (.sa-stack) */
    function labelCells(sel) {
        const heads = $(sel + ' thead th').map(function () { return $(this).text().trim(); }).get();
        $(sel + ' tbody tr').each(function () {
            $(this).children('td, th').each(function (i) { $(this).attr('data-label', heads[i] || ''); });
        });
    }

    /* The apply countdown, as a sticky banner at the top of whichever page the admin is on
       (status page, setup page, dashboard): all read the same server-side timer, so closing
       a tab changes nothing. a = {seconds_left, job} or null (no countdown). The big number
       ticks locally between polls; screen readers hear it every 10 s, and every second from
       10 s on (assertive). */
    let cd = null;
    let cdObserver = null;
    function cdRemove() {
        $('#sa-countdown, #sa-countdown-space').remove();
        $(window).off('resize.sacd');
        if (cdObserver) { cdObserver.disconnect(); cdObserver = null; }
        document.documentElement.style.scrollPaddingTop = '';
        document.documentElement.style.overflowAnchor = '';
        cd = null;
    }
    function countdown(a) {
        if (!a) {
            if (cd && cd.state === 'waiting') {
                clearInterval(cd.timer);
                cdRemove();
            }
            return;
        }
        if (!cd) {
            // in <body>, not in <main>: main is position:relative under the fixed page header, so
            // anything inside it, however high its z-index, stays beneath the header
            $('body').prepend(
                '<div id="sa-countdown" class="sa-countdown" role="region" aria-labelledby="sa-cd-title">' +
                '<div class="sa-cd-num" aria-hidden="true"><span id="sa-cd-left"></span><small> s</small></div>' +
                '<div class="sa-cd-text"><strong id="sa-cd-title">Keep the new shaper settings?</strong> ' +
                '<span id="sa-cd-what">At 0 the firewall puts the previous shaper settings back by itself. ' +
                'Nothing else is touched.</span></div>' +
                '<button type="button" id="sa-cd-keep" class="btn sa-cd-btn"><i class="fa fa-thumbs-up" aria-hidden="true"></i> ' +
                'Keep these settings</button>' +
                '<p id="sa-cd-live" class="sr-only" aria-live="polite"></p></div>');
            $('#sa-cd-keep').on('click', function () {
                ajaxCall('/api/shaperinsight/apply/confirm', {job: cd ? cd.job : ''}, function (r) {
                    if (r && r.status === 'ok') {
                        clearInterval(cd.timer);
                        cd.state = 'done';
                        $('#sa-cd-left').text('✓');
                        $('#sa-cd-title').text('Kept.');
                        $('#sa-cd-what').text('The new shaper settings stay.');
                        $('#sa-cd-keep').remove();
                        $('#sa-cd-live').attr('aria-live', 'polite').text('Kept. The new shaper settings stay.');
                        setTimeout(cdRemove, 5000);
                    } else {
                        $('#sa-cd-what').text((r && r.message) || 'The confirmation did not arrive.');
                    }
                });
            });
            // fixed just below OPNsense's fixed top bar, over the content column; a spacer of the
            // same height keeps the page's first lines from hiding under it
            const place = function () {
                const main = $('main.page-content');
                const nav = $('header.page-head');
                const banner = $('#sa-countdown');
                banner.css({top: (nav.length ? nav.outerHeight() : 0) + 'px', left: main.offset().left + 'px',
                    width: main.outerWidth() + 'px'});
                $('#sa-countdown-space').height(banner.outerHeight(true));
            };
            // no scroll anchoring while the banner is up: otherwise the browser scrolls down by
            // the spacer's height to keep the old view, and the page title ends up under the banner
            document.documentElement.style.overflowAnchor = 'none';
            $('main.page-content').prepend('<div id="sa-countdown-space" aria-hidden="true"></div>');
            $(window).on('resize.sacd', place);
            place();
            // the banner wraps to two or three lines on narrow screens: keep the spacer (and the
            // scroll offset for in-page links) exactly as tall as the banner whenever it changes
            if (window.ResizeObserver) {
                cdObserver = new ResizeObserver(function () {
                    const h = $('#sa-countdown').outerHeight();
                    $('#sa-countdown-space').height(h + 8);
                    document.documentElement.style.scrollPaddingTop = (h + ($('header.page-head').outerHeight() || 0)) + 'px';
                });
                cdObserver.observe(document.getElementById('sa-countdown'));
            }
            cd = {state: 'waiting', timer: setInterval(tick, 1000), place: place};
        }
        cd.job = a.job;
        cd.deadline = Date.now() + a.seconds_left * 1000;
        tick();
    }
    function tick() {
        if (!cd || cd.state !== 'waiting') {
            return;
        }
        const left = Math.max(0, Math.round((cd.deadline - Date.now()) / 1000));
        $('#sa-cd-left').text(left);
        $('#sa-countdown').toggleClass('sa-cd-urgent', left <= 10);
        const live = $('#sa-cd-live');
        if (left <= 10) {
            live.attr('aria-live', 'assertive').text(left ? left + ' seconds left to keep the new shaper settings.' :
                'Putting the previous shaper settings back.');
        } else if (left % 10 === 0) {
            live.attr('aria-live', 'polite').text(left + ' seconds left to keep the new shaper settings.');
        }
        if (left === 0) {
            clearInterval(cd.timer);
            cd.state = 'done';
            $('#sa-cd-title').text('Going back.');
            $('#sa-cd-what').text('The previous shaper settings are being put back.');
            $('#sa-cd-keep').remove();
            setTimeout(cdRemove, 8000);
        }
    }

    return {esc, words, chip, autoTerms, table, swatch, ref, tooltips, badge, why, rate, num, ms, sig, poll, get, loadGlossary, cssVar, labelCells, countdown, unescapeApi,
        termDialog, termBlocks, claimDialog, whys, CLS, ICON, WORD};
})();
