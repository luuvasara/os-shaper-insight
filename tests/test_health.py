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

"""Health checks S1-S8 on a small hand-made snapshot."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'net', 'shaper-insight', 'src', 'opnsense',
                                'scripts', 'shaperinsight'))
from lib.health import run_checks  # noqa: E402


def rule(uuid, seq, iface, direction, target, **kw):
    r = {'uuid': uuid, 'enabled': '1', 'sequence': str(seq), 'interface': iface, 'direction': direction,
         'proto': 'ip', 'source': 'any', 'destination': 'any', 'src_port': 'any', 'dst_port': 'any',
         'target': target, 'description': uuid}
    r.update(kw)
    return r


SNAP = {
    'config': {
        'pipes': [{'uuid': 'p1', 'number': '10000', 'enabled': '1', 'scheduler': 'fq_codel', 'description': 'down'},
                  {'uuid': 'p2', 'number': '10001', 'enabled': '0', 'scheduler': 'fq_codel', 'description': 'old'},
                  {'uuid': 'p3', 'number': '10002', 'enabled': '1', 'scheduler': '', 'description': 'spare'}],
        'queues': [{'uuid': 'q1', 'number': '10000', 'enabled': '1', 'pipe': 'p1', 'weight': '100'},
                   {'uuid': 'q2', 'number': '10001', 'enabled': '1', 'pipe': 'p1', 'weight': '20'}],
        'rules': [rule('r1', 10, 'wan', 'in', 'q1'), rule('r2', 20, 'wan', 'in', 'q2', proto='udp'),
                  rule('r3', 30, 'wan2', 'in', 'q1', enabled='0')],
    },
    'kernel': {
        'dnctl': {'pipes': {'10000': {'type': 'CAKE'}, '10001': {'type': 'FQ_CODEL'}, '10002': {'type': 'WF2Q+'}},
                  'queues': {'10000': {}, '10001': {}}},
        'ipfw': {'rules': {'r1': {'num': 60001, 'pkts': 5, 'bytes': 9, 'body': 'x'},
                           'r2': {'num': 60002, 'pkts': 0, 'bytes': 0, 'body': 'queue 10001 udp'}}},
    },
}


class HealthTest(unittest.TestCase):
    def setUp(self):
        self.c = {c['id']: c for c in run_checks(SNAP, ['wan', 'wan2'])}

    def text(self, cid):
        return ' '.join(i['text'] for i in self.c[cid]['items'])

    def test_all_nine(self):
        self.assertEqual(sorted(self.c, key=lambda x: int(x[1:])), ['S%d' % i for i in range(1, 11)])

    def test_no_shaper_is_not_eight_ok(self):
        # review M7: a box with no shaper gets "not applicable", never a column of green OKs
        import copy
        snap = copy.deepcopy(SNAP)
        for k in ('pipes', 'queues', 'rules'):
            snap['config'][k] = []
        checks = run_checks(snap, ['wan'])
        self.assertFalse([c['id'] for c in checks if c['level'] == 'ok'])
        self.assertTrue(all(i['text'].startswith('Not applicable') for c in checks for i in c['items']))

    def test_s9_priority_marks(self):
        bad = {c['id']: c for c in run_checks(SNAP, ['wan'], {'status': 'failed', 'command': 'load',
                                                             'errors': ['rule 2050: boom']}, True)}['S9']
        self.assertEqual(bad['level'], 'bad')
        self.assertIn('rule 2050: boom', bad['items'][0]['text'])
        self.assertEqual({c['id']: c for c in run_checks(SNAP, ['wan'], None, True)}['S9']['level'], 'warn')
        self.assertEqual({c['id']: c for c in run_checks(SNAP, ['wan'], {'status': 'ok', 'rules': 3,
                                                                        'command': 'load'}, True)}['S9']['level'], 'ok')

    def test_s1_weight_under_cake_uses_kernel_type(self):
        self.assertEqual(self.c['S1']['level'], 'warn')
        self.assertIn('CAKE', self.text('S1'))

    def test_s2_disabled_but_loaded(self):
        self.assertIn('pipe 10001 "old" is disabled but still loaded', self.text('S2'))

    def test_s3_zero_packets(self):
        self.assertIn('matched 0 packets', self.text('S3'))

    def test_s3_counters_reset_by_a_reload(self):
        # review N5: right after an ipfw reload 0 packets means "wait", not a warning
        fresh = {c['id']: c for c in run_checks(SNAP, ['wan'], reloaded=1000.0, now=1060.0)}['S3']
        item = [i for i in fresh['items'] if 'counters reset at' in i['text']]
        self.assertEqual(len(item), 1)
        self.assertEqual(item[0]['level'], 'info')
        self.assertIn('wait for traffic', item[0]['text'])
        old = {c['id']: c for c in run_checks(SNAP, ['wan'], reloaded=1000.0, now=1000.0 + 3600)}['S3']
        self.assertIn('matched 0 packets', ' '.join(i['text'] for i in old['items']))

    def test_s4_missing_on_second_wan(self):
        self.assertIn('not on wan2', self.text('S4'))

    def test_s6_after_catch_all(self):
        self.assertIn('after the catch-all', self.text('S6'))

    def test_s7_unused_pipe(self):
        self.assertIn('pipe 10002 "spare" is enabled, but no enabled rule', self.text('S7'))

    def test_s8_skips_cake(self):
        # pipe 10000 is fq_codel in the config but CAKE in the kernel: no FQ-CoDel memory note
        self.assertNotIn('10000', self.text('S8'))


if __name__ == '__main__':
    unittest.main()
