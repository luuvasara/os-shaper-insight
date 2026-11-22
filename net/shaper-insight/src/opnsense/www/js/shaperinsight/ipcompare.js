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
 * Address-aware sorting for the plugin's tables (user feedback 2026-11-22: "x.x.x.10 after .9"):
 * IPv4 first, then IPv6, each by numeric value; a prefix length (/24) after the address; a zone
 * (%igb0) is ignored; anything that is not an address sorts after the addresses, naturally
 * ("host2" before "host10"). Used by SA.table (common.js) and tested with node (tests/test_tables.py).
 */
(function (root) {
    'use strict';

    function v4(s) {
        const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(s);
        if (!m) {
            return null;
        }
        const parts = m.slice(1).map(Number);
        return parts.some(function (x) { return x > 255; }) ? null : parts;
    }

    /* eight 16-bit groups, or null; "::" and a trailing dotted IPv4 are expanded */
    function v6(s) {
        if (s.indexOf(':') < 0 || !/^[0-9a-fA-F:.]+$/.test(s)) {
            return null;
        }
        const halves = s.split('::');
        if (halves.length > 2) {
            return null;
        }
        function groups(x) {
            let g = x ? x.split(':') : [];
            const last = g[g.length - 1];
            if (last && last.indexOf('.') >= 0) {
                const q = v4(last);
                if (!q) {
                    return null;
                }
                g = g.slice(0, -1).concat([((q[0] << 8) | q[1]).toString(16), ((q[2] << 8) | q[3]).toString(16)]);
            }
            return g;
        }
        const head = groups(halves[0]);
        const tail = halves.length === 2 ? groups(halves[1]) : [];
        if (head === null || tail === null) {
            return null;
        }
        let all;
        if (halves.length === 1) {
            all = head;
        } else {
            const fill = 8 - head.length - tail.length;
            if (fill < 1) {
                return null;
            }
            all = head.concat(new Array(fill).fill('0'), tail);
        }
        if (all.length !== 8) {
            return null;
        }
        const out = [];
        for (let i = 0; i < 8; i++) {
            if (!/^[0-9a-fA-F]{1,4}$/.test(all[i])) {
                return null;
            }
            out.push(parseInt(all[i], 16));
        }
        return out;
    }

    function key(value) {
        const s = String(value === null || value === undefined ? '' : value).trim();
        let addr = s;
        let prefix = -1;
        const slash = s.indexOf('/');
        if (slash >= 0) {
            addr = s.slice(0, slash);
            prefix = Number(s.slice(slash + 1));
            if (!isFinite(prefix)) {
                return {family: 2, text: s};
            }
        }
        addr = addr.replace(/%.*$/, '');
        let parts = v4(addr);
        if (parts) {
            return {family: 0, parts: parts, prefix: prefix};
        }
        parts = v6(addr);
        if (parts) {
            return {family: 1, parts: parts, prefix: prefix};
        }
        return {family: 2, text: s};
    }

    function compare(x, y) {
        const a = key(x);
        const b = key(y);
        if (a.family !== b.family) {
            return a.family - b.family;
        }
        if (a.family === 2) {
            return a.text.localeCompare(b.text, undefined, {numeric: true, sensitivity: 'base'});
        }
        for (let i = 0; i < a.parts.length; i++) {
            if (a.parts[i] !== b.parts[i]) {
                return a.parts[i] - b.parts[i];
            }
        }
        return a.prefix - b.prefix;
    }

    const api = {compare: compare, key: key};
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    } else {
        root.SAip = api;
    }
}(this));
