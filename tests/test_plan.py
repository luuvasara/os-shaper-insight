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

"""Plan defaults: fair by default, ACK filter only on asymmetric links, control lane sizing."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'net', 'shaper-insight', 'src', 'opnsense',
                                'scripts', 'shaperinsight'))
from lib.plan import build  # noqa: E402

DET = {'links': [{'name': 'Link A', 'root': 'igb0', 'members': ['wan'], 'why': 'x'}], 'interfaces': []}
SNAP = {'config': {'pipes': [], 'queues': [], 'rules': []}, 'oscake': {},
        'kernel': {'dnctl': {'pipes': {}, 'queues': {}}, 'sysctl': {}}}


def objs(plan, kind):
    return [o for o in plan['links'][0]['objects'] if o['kind'] == kind]


class PlanTest(unittest.TestCase):
    def test_fair_by_default(self):
        cake = objs(build(DET, SNAP, 1000, 100), 'cake')
        self.assertEqual({c['fields']['diffserv'] for c in cake}, {'besteffort'})
        self.assertEqual([c['fields']['wash'] for c in cake], ['1', '0'])     # wash on download only

    def test_priorities_switch_to_diffserv4(self):
        cake = objs(build(DET, SNAP, 1000, 100, priorities=True), 'cake')
        self.assertEqual({c['fields']['diffserv'] for c in cake}, {'diffserv4'})

    def test_wash_both_ways_with_priorities(self):
        # CAKE sorts before it washes: with priorities, wash both ways; without, download only
        plain = {o['pipe_ref']: o['model']['wash'] for o in objs(build(DET, SNAP, 500, 50), 'cake')}
        prio = {o['pipe_ref']: o['model']['wash'] for o in objs(build(DET, SNAP, 500, 50, priorities=True), 'cake')}
        self.assertEqual(sorted(plain.values()), ['0', '1'])
        self.assertEqual(set(prio.values()), {'1'})

    def test_reapply_changes_own_objects_in_place(self):
        import copy
        first = build(DET, SNAP, 100, 20)
        snap = copy.deepcopy(SNAP)
        for i, o in enumerate(first['links'][0]['objects']):
            m = dict(o['model'], uuid='u%d' % i)
            if o['kind'] == 'cake':
                m['pipe'] = 'u%d' % [j for j, x in enumerate(first['links'][0]['objects'])
                                     if x.get('ref') == o['pipe_ref']][0]
            snap['config'].setdefault({'pipe': 'pipes', 'queue': 'queues', 'rule': 'rules',
                                       'cake': 'cake'}[o['kind']], []).append(m)
        again = build(DET, snap, 50, 20)['links'][0]['objects']
        self.assertEqual({o['action'] for o in again}, {'same', 'change'})
        changed = [o for o in again if o['action'] == 'change']
        self.assertEqual([(o['kind'], o['description']) for o in changed], [('pipe', 'Insight download Link A')])
        self.assertEqual(changed[0]['changes']['bandwidth'], ['94', '46500'])       # 47.5 Mbit - 1 Mbit lane
        self.assertEqual(changed[0]['changes']['bandwidthMetric'], ['Mbit', 'Kbit'])
        # an own object nothing in the new plan matches is listed for removal, nothing else is
        snap['config']['rules'].append({'uuid': 'orphan', 'origin': 'ShaperInsight', 'description': 'Insight download Link Z',
                                        'interface': 'lan', 'direction': 'out', 'sequence': '900'})
        plan = build(DET, snap, 50, 20)
        self.assertEqual([(o['kind'], o['uuid']) for o in plan['remove']], [('rule', 'orphan')])
        self.assertEqual(plan['totals']['remove'], 1)
        # numbers kept: no second set
        self.assertEqual(sorted(o['number'] for o in again if o['kind'] == 'pipe'),
                         sorted(o['number'] for o in first['links'][0]['objects'] if o['kind'] == 'pipe'))

    def test_ack_filter_ratio(self):
        self.assertEqual([c['fields']['ack_filter'] for c in objs(build(DET, SNAP, 1000, 100), 'cake')], ['0', '0'])
        up = objs(build(DET, SNAP, 1000, 50), 'cake')[1]
        self.assertEqual(up['fields']['ack_filter'], '1')
        self.assertIn('20:1', up['why'])
        self.assertTrue(up['contested'])

    def test_control_lane(self):
        pipes = objs(build(DET, SNAP, 1000, 50), 'pipe')
        self.assertEqual(pipes[0]['fields']['bandwidth'], '10 Mbit/s')       # 1 % of 1000
        self.assertEqual(pipes[2]['fields']['bandwidth'], '1 Mbit/s')        # 1 % of 50 < 1
        self.assertTrue(pipes[0]['fields']['scheduler'].startswith('WF2Q+'))
        self.assertEqual(pipes[1]['fields']['bandwidth'], '940 Mbit/s')      # 1000 x 0.95 - 10
        self.assertEqual(pipes[0]['claims'], ['icmpv6-control'])

    def test_apply_steps_and_summary(self):
        p = build(DET, SNAP, 0, 0)
        self.assertEqual(len(p['apply_steps']), 6)
        # CAKE's host is an address (claim host-isolation-per-address): never "each device"
        self.assertIn('Each address gets a fair share', p['summary'][0]['text'])
        self.assertIn('counts once per address', p['summary'][0]['text'])
        self.assertEqual(p['summary'][0]['claims'], ['host-isolation', 'host-isolation-per-address'])

    def test_no_cake_no_device_fairness_claim(self):
        # review M6: without os-cake the plan must not promise per-device fairness, nor a CAKE check
        snap = dict(SNAP, oscake=None)
        p = build(DET, snap, 100, 20)
        self.assertNotIn('Each address gets', p['summary'][0]['text'])
        self.assertIn('per connection, not per device', p['summary'][0]['text'])
        self.assertEqual(p['summary'][0]['claims'], ['fq-codel-per-flow'])
        self.assertNotIn('Check that CAKE is running', [s['step'] for s in p['apply_steps']])
        self.assertIn('does not share between devices', [x for x in p['preflight'] if 'os-cake' in x['text']][0]['why'])
        p = build(DET, snap, 100, 20, priorities=True)
        self.assertTrue(any('no effect' in s['text'] for s in p['summary']))


def kbit(o):
    return int(o['model']['bandwidth']) * (1000 if o['model']['bandwidthMetric'] == 'Mbit' else 1)


class RateBudgetTest(unittest.TestCase):
    """ review H3: control + bulk <= rate x 95 %, on every link speed """
    def lanes(self, down, up):
        p = build(DET, SNAP, down, up)
        out = {}
        for o in objs(p, 'pipe'):
            d = 'down' if 'download' in o['description'] else 'up'
            out.setdefault(d, {})['control' if ' control ' in o['description'] else 'bulk'] = kbit(o)
        return p, out

    def check(self, down, up, expect):
        p, lanes = self.lanes(down, up)
        for d, rate in (('down', down), ('up', up)):
            total = sum(lanes[d].values())
            self.assertLessEqual(total, round(rate * 950), (d, lanes[d]))
            self.assertEqual(lanes[d], expect[d], d)
            if 'control' in lanes[d]:
                self.assertLessEqual(lanes[d]['control'], rate * 1000 * 0.95 * 0.05)
        return p

    def test_1_mbit(self):
        p = self.check(1, 1, {'down': {'bulk': 950}, 'up': {'bulk': 950}})
        self.assertEqual(sum(1 for x in p['preflight'] if 'no IPv6 housekeeping lane' in x['text']), 2)
        self.assertFalse(any(' control ' in o['description'] for o in objs(p, 'rule')))
        self.assertFalse(any(' control ' in o['description'] for o in objs(p, 'queue')))

    def test_2_mbit(self):
        self.check(2, 2, {'down': {'control': 95, 'bulk': 1805}, 'up': {'control': 95, 'bulk': 1805}})

    def test_10_2_mbit(self):
        p = self.check(10, 2, {'down': {'control': 475, 'bulk': 9025}, 'up': {'control': 95, 'bulk': 1805}})
        up_bulk = [o for o in objs(p, 'pipe') if o['description'] == 'Insight upload Link A'][0]
        self.assertEqual(up_bulk['model']['bandwidthMetric'], 'Kbit')
        self.assertEqual(up_bulk['fields']['bandwidth'], '1.80 Mbit/s')

    def test_1000_50_mbit(self):
        p = self.check(1000, 50, {'down': {'control': 10000, 'bulk': 940000}, 'up': {'control': 1000, 'bulk': 46500}})
        down_bulk = [o for o in objs(p, 'pipe') if o['description'] == 'Insight download Link A'][0]
        self.assertEqual((down_bulk['model']['bandwidth'], down_bulk['model']['bandwidthMetric']), ('940', 'Mbit'))

    def test_no_cake_uses_92_percent(self):
        p = build(DET, dict(SNAP, oscake=None), 100, 20)
        pipes = {o['description']: kbit(o) for o in objs(p, 'pipe')}
        self.assertEqual(pipes['Insight download Link A'] + pipes['Insight download control Link A'], 92000)

    def test_15_mbit_no_truncation(self):
        # was int(15 x 0.95) - 1 = 13; now 14250 kbit/s total
        _, lanes = self.lanes(15, 15)
        self.assertEqual(sum(lanes['down'].values()), 14250)


class DigestTest(unittest.TestCase):
    """ review M8: the digest changes when what Apply would write changes, and only then """
    def test_digest(self):
        a, b = build(DET, SNAP, 100, 20), build(DET, SNAP, 100, 20)
        self.assertEqual(a['digest'], b['digest'])
        self.assertEqual(len(a['digest']), 64)
        self.assertNotEqual(a['digest'], build(DET, SNAP, 90, 20)['digest'])
        self.assertNotEqual(a['digest'], build(DET, SNAP, 100, 20, priorities=True)['digest'])
        import copy
        snap = copy.deepcopy(SNAP)
        snap['config']['rules'].append({'uuid': 'mine', 'enabled': '1', 'interface': 'wan', 'origin': 'TrafficShaper',
                                        'sequence': '1', 'direction': 'in'})
        self.assertNotEqual(a['digest'], build(DET, snap, 100, 20)['digest'])


if __name__ == '__main__':
    unittest.main()
