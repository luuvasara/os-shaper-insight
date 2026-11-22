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
 * Page verdicts that are pure arithmetic, tested with node (tests/test_tables.py).
 * limiting(rates, drops, cap): is the shaper the bottleneck now? (review 2026-11-22: "Limiting now" showed at
 * 10.5 kbit/s of 38.9 Mbit/s because one stray drop counted). Yes when the pipe ran near its cap (90 %) in at
 * least 2 of the last 3 steps, or when it dropped at least 1 packet/s in the newest step while running at
 * half the cap or more (a short burst that overflowed the queue). A drop at a low rate is not limiting: it
 * is CAKE's AQM trimming one flow, or a burst between two reads.
 */
(function (root) {
    'use strict';

    function limiting(rates, drops, cap) {
        const i = rates.length - 1;
        if (i < 0 || !cap) {
            return false;
        }
        let near = 0;
        let seen = 0;
        for (let k = i; k >= 0 && k > i - 3; k--) {
            if (rates[k] === null || rates[k] === undefined) {
                continue;
            }
            seen++;
            if (rates[k] >= 0.9 * cap) {
                near++;
            }
        }
        const rate = rates[i] || 0;
        const d = drops && drops[i] ? drops[i] : 0;
        return near >= Math.min(2, Math.max(seen, 1)) && near > 0 || (d >= 1 && rate >= 0.5 * cap);
    }

    const api = {limiting: limiting};
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    } else {
        root.SAverdict = api;
    }
}(this));
