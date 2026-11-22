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

"""Bufferbloat test: grade bands (Waveform), added delay against idle, worse direction decides."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'net', 'shaper-insight', 'src', 'opnsense',
                                'scripts', 'shaperinsight'))
import bbtest  # noqa: E402

PHASES = [('idle', 10, []), ('download', 10, ['down']), ('upload', 10, ['up'])]


def probes(phase, target, rtts, lost=0):
    return [{'phase': phase, 'target': target, 'rtt': r, 't': 0} for r in rtts] + \
        [{'phase': phase, 'target': target, 'rtt': None, 't': 0}] * lost


class BbTest(unittest.TestCase):
    def test_grades(self):
        self.assertEqual([bbtest.grade(x) for x in (0, 4.9, 5, 29.9, 59, 199, 399, 400, None)],
                         ['A+', 'A+', 'A', 'A', 'B', 'C', 'D', 'F', '?'])

    def test_added_delay_and_worse_direction(self):
        s = {'probes': probes('idle', 'r1', [10] * 20) + probes('download', 'r1', [50] * 20) +
             probes('upload', 'r1', [12] * 18, lost=2) + probes('idle', 'gw', [1] * 20) +
             probes('download', 'gw', [500] * 20) + probes('upload', 'gw', [1] * 20),
             'rates': []}
        out, worst = bbtest.summarise(s, ['gw', 'r1'], 'gw', PHASES)
        self.assertEqual(out['download']['added_p50'], 40)
        self.assertEqual(out['upload']['added_p50'], 2)
        self.assertEqual(worst, 40)            # the gateway (500 ms) does not decide the grade
        self.assertEqual(out['upload']['targets']['r1']['loss_pct'], 10.0)


if __name__ == '__main__':
    unittest.main()
