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
 * Incremental polling of sampler/history (sim100: send only what changed). The page keeps the last full
 * answer; each poll asks ?since=<its newest row> and gets the rows from two steps before it on
 * (sampler.py delta()). merge() puts them in place, trims the rows that left the window and returns the
 * merged answer, or null when it cannot be merged safely (other step, other pipes, rows that do not
 * line up, another set of coloured hosts): the page then asks for everything. Tested with node
 * (tests/test_tables.py).
 */
(function (root) {
    'use strict';

    function startOf(d) {
        const s = Object.keys(d.pipes || {}).map(function (n) { return d.pipes[n].start; });
        return s.length && s.every(function (x) { return x === s[0]; }) ? s[0] : null;
    }

    function rows(d) {
        const n = Object.keys(d.pipes || {})[0];
        return n === undefined ? 0 : (d.pipes[n].series.bytes || []).length;
    }

    /* the stamp of the newest row the page has, or null */
    function lastRow(d) {
        const s = startOf(d);
        return s === null || !rows(d) ? null : s + (rows(d) - 1) * d.step;
    }

    function join(old, add, idx, drop) {
        if (!Array.isArray(old) || !Array.isArray(add)) {
            return add;
        }
        return old.slice(0, idx).concat(add).slice(drop);
    }

    function joinMap(old, add, idx, drop) {
        const out = {};
        Object.keys(add || {}).forEach(function (k) { out[k] = join((old || {})[k], add[k], idx, drop); });
        return out;
    }

    function merge(old, d) {
        if (!d || !d.delta) {
            return d;
        }
        if (!old || old.window !== d.window || old.step !== d.step) {
            return null;
        }
        const s0 = startOf(old);
        const s1 = startOf(d);
        const keys = Object.keys(d.pipes).sort().join(' ');
        if (s0 === null || s1 === null || keys !== Object.keys(old.pipes).sort().join(' ')) {
            return null;
        }
        const idx = (s1 - s0) / d.step;
        if (idx !== Math.floor(idx) || idx < 0 || idx > rows(old)) {
            return null;
        }
        const newest = s1 + ((d.pipes[Object.keys(d.pipes)[0]].series.bytes || []).length - 1) * d.step;
        const first = Math.max(s0, newest - d.window + d.step);
        const drop = Math.max(0, Math.round((first - s0) / d.step));
        for (const p of Object.keys(d.hosts || {})) {
            const a = (old.hosts || {})[p] || [];
            const b = d.hosts[p];
            if (a.length !== b.length || a.some(function (h, i) { return h[0] !== b[i][0]; })) {
                return null;
            }
        }
        const out = Object.assign({}, d, {delta: false, pipes: {}, hosts: {}, hosts_offered: {}});
        Object.keys(d.pipes).forEach(function (n) {
            const o = old.pipes[n];
            const e = d.pipes[n];
            out.pipes[n] = Object.assign({}, e, {start: s0 + drop * d.step,
                series: joinMap(o.series, e.series, idx, drop), max_bits: join(o.max_bits, e.max_bits, idx, drop),
                cap_rows: join(o.cap_rows, e.cap_rows, idx, drop)});
            if (e.tin_series) {
                out.pipes[n].tin_series = joinMap(o.tin_series, e.tin_series, idx, drop);
            }
        });
        Object.keys(d.hosts || {}).forEach(function (p) {
            out.hosts[p] = d.hosts[p].map(function (h, i) { return [h[0], join(old.hosts[p][i][1], h[1], idx, drop)]; });
        });
        Object.keys(d.hosts_offered || {}).forEach(function (p) {
            out.hosts_offered[p] = join((old.hosts_offered || {})[p], d.hosts_offered[p], idx, drop);
        });
        return out;
    }

    const api = {merge: merge, lastRow: lastRow};
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    } else {
        root.SAdelta = api;
    }
}(this));
