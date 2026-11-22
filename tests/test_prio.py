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

"""Priority marks: which links get rules, how targets resolve, table entries normalised."""
import os
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'net', 'shaper-insight', 'src', 'opnsense',
                                'scripts', 'shaperinsight'))
import prio  # noqa: E402

CFG = """<opnsense><interfaces><wan><if>igb0</if></wan><opt1><if>igb1</if></opt1></interfaces><OPNsense>
<TrafficShaper><pipes><pipe uuid="p1"/><pipe uuid="p2"/></pipes><queues><queue uuid="q1"><pipe>p2</pipe></queue></queues>
<rules><rule uuid="r1"><enabled>1</enabled><interface>wan</interface><target>p1</target></rule>
<rule uuid="r2"><enabled>1</enabled><interface>opt1</interface><target>q1</target></rule></rules></TrafficShaper>
<Cake><pipes><pipe uuid="c1"><enabled>1</enabled><pipe>p1</pipe><diffserv>diffserv4</diffserv></pipe>
<pipe uuid="c2"><enabled>1</enabled><pipe>p2</pipe><diffserv>besteffort</diffserv></pipe></pipes></Cake>
<ShaperInsight><priorities><priority uuid="a"><enabled>1</enabled><target>AA:bb:cc:dd:ee:01</target><class>interactive</class></priority>
<priority uuid="b"><enabled>0</enabled><target>10.0.0.5</target><class>background</class></priority>
</priorities></ShaperInsight></OPNsense></opnsense>"""


class PrioTest(unittest.TestCase):
    def setUp(self):
        self.root = ET.fromstring(CFG)

    def test_links_only_where_cake_sorts_by_dscp(self):
        # igb1's pipe runs besteffort CAKE: a mark there changes nothing, so no rule
        self.assertEqual(prio.links(self.root), ['igb0'])

    def test_enabled_entries_only(self):
        self.assertEqual([e['target'] for e in prio.priorities(self.root)], ['AA:bb:cc:dd:ee:01'])

    def test_resolve(self):
        macs = {'aa:bb:cc:dd:ee:01': {'192.168.1.20', '2001:db8::20'}}
        self.assertEqual(prio.resolve('AA:bb:cc:dd:ee:01', macs), ['192.168.1.20', '2001:db8::20'])
        self.assertEqual(prio.resolve('10.0.0.5', {}), ['10.0.0.5/32'])
        self.assertEqual(prio.resolve('10.0.0.0/24', {}), ['10.0.0.0/24'])
        self.assertEqual(prio.resolve('aa:bb:cc:dd:ee:99', macs), [])


class SharesTest(unittest.TestCase):
    """ a MAC with several addresses in use gets several CAKE shares (claim host-isolation-per-address) """
    ARP = '''? (192.168.1.1) at 52:54:00:5d:00:01 on vtnet0 permanent [ethernet]
? (192.168.1.20) at aa:bb:cc:dd:ee:01 on vtnet0 expires in 1184 seconds [ethernet]
? (192.168.1.30) at aa:bb:cc:dd:ee:02 on vtnet0 expires in 900 seconds [ethernet]
? (10.98.0.1) at 52:54:00:00:00:99 on vtnet1 expires in 900 seconds [ethernet]'''
    NDP = '''Neighbor                             Linklayer Address  Netif Expire    S Flags
fe80::1%vtnet0                       aa:bb:cc:dd:ee:01 vtnet0 23h      R
2001:db8::20                         aa:bb:cc:dd:ee:01 vtnet0 20s      R
2001:db8::21                         aa:bb:cc:dd:ee:01 vtnet0 20s      R
2001:db8::30                         aa:bb:cc:dd:ee:02 vtnet0 4h       S
2001:db8::1                          52:54:00:5d:00:01 vtnet0 permanent R'''

    def test_parse(self):
        d = prio.parse_neighbours(self.ARP, self.NDP, {'vtnet1'})
        self.assertEqual(sorted(d['aa:bb:cc:dd:ee:01']['addresses']), ['192.168.1.20', '2001:db8::20', '2001:db8::21'])
        self.assertEqual(d['aa:bb:cc:dd:ee:02']['addresses'], ['192.168.1.30'])     # stale IPv6 not counted
        self.assertNotIn('52:54:00:5d:00:01', d)                                     # the firewall itself
        self.assertNotIn('52:54:00:00:00:99', d)                                     # the internet side


class PrioFailureTest(unittest.TestCase):
    """ review M5/L6: failures are reported, not swallowed; previous marks stay on failure """
    def setUp(self):
        self.root = ET.fromstring(CFG)
        self.calls = []
        self._ipfw = prio.ipfw

    def tearDown(self):
        prio.ipfw = self._ipfw

    def fake(self, fail_on):
        def ipfw(*args):
            self.calls.append(args)
            if args[:3] == ('table', 'si_prio_interactive', 'list'):
                return 0, '', ''
            if any(f in ' '.join(args) for f in fail_on):
                return 1, '', 'ipfw: boom'
            return 0, '', ''
        prio.ipfw = ipfw

    def test_table_failure_is_an_error(self):
        self.fake(['add 10.0.0.9'])
        errors = []
        prio.sync_tables({'interactive': {'x': ['10.0.0.9']}, 'background': {}}, errors)
        self.assertEqual(len(errors), 1)
        self.assertIn('add 10.0.0.9/32 failed', errors[0])

    def test_failed_rule_keeps_previous_set(self):
        self.fake(['setdscp af41'])
        from unittest import mock
        with mock.patch.object(prio, 'wanted', lambda root: ([{'class': 'interactive', 'target': 'x', 'description': ''}],
                                                             {'interactive': {'x': ['10.0.0.9']}, 'background': {}})):
            out = prio.load(self.root)
        self.assertEqual(out['status'], 'failed')
        self.assertTrue(out['kept_previous'])
        # never swapped in, and set 7 was never deleted
        self.assertNotIn(('set', 'swap', '7', '30'), self.calls)
        self.assertNotIn(('-q', 'delete', 'set', '7'), self.calls)

    def test_more_than_ten_links_reported(self):
        from unittest import mock
        with mock.patch.object(prio, 'links', lambda root: ['igb%d' % i for i in range(12)]):
            rules, errors = prio.build_rules(self.root, [{'class': 'interactive'}, {'class': 'background'}])
        self.assertEqual(len(errors), 1)
        self.assertIn('igb10, igb11', errors[0])
        numbers = [int(r[0]) for r in rules]
        self.assertEqual(len(numbers), len(set(numbers)))         # no collisions
        self.assertLessEqual(max(numbers), 2099)


class SideEffectTest(unittest.TestCase):
    """ review N5: no priority = nothing created; sets 7/30 touched only while they are ours """
    def setUp(self):
        self.root = ET.fromstring(CFG)
        self.calls = []
        self._ipfw = prio.ipfw

    def tearDown(self):
        prio.ipfw = self._ipfw

    def fake(self, show='', sets='enable 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30',
             tables=()):
        def ipfw(*args):
            self.calls.append(args)
            if args == ('-S', 'show'):
                return 0, show, ''
            if args == ('set', 'show'):
                return 0, sets, ''
            if args[0] == 'table' and args[2] == 'list':
                return (0, '', '') if args[1] in tables else (1, '', 'no such table')
            return 0, '', ''
        prio.ipfw = ipfw

    def changes(self):
        return [c for c in self.calls if c not in (('-S', 'show'), ('set', 'show')) and 'list' not in c]

    def test_no_priority_changes_nothing(self):
        from unittest import mock
        self.fake()
        with mock.patch.object(prio, 'wanted', lambda root: ([], {'interactive': {}, 'background': {}})):
            out = prio.load(self.root)
        self.assertEqual(out['status'], 'ok')
        self.assertEqual(self.changes(), [])            # no table create, no set disable

    def test_no_priority_cleans_only_ours(self):
        from unittest import mock
        self.fake(show='02010 0 0 set 7 setdscp cs1 ip from table(si_prio_background) to any out via igb0\n'
                       '05000 9 9 set 7 count ip from any to any\n', tables=('si_prio_background',))
        with mock.patch.object(prio, 'wanted', lambda root: ([], {'interactive': {}, 'background': {}})):
            prio.load(self.root)
        self.assertIn(('-q', 'set', '7', 'delete', '2010'), self.calls)      # by number: 5000 is not ours
        self.assertNotIn(('-q', 'delete', 'set', '7'), self.calls)
        self.assertIn(('table', 'si_prio_background', 'destroy'), self.calls)

    def test_foreign_rules_block_load(self):
        from unittest import mock
        self.fake(show='00100 0 0 set 30 count ip from any to any\n')
        with mock.patch.object(prio, 'wanted', lambda root: ([{'class': 'interactive', 'target': 'x', 'description': ''}],
                                                             {'interactive': {'x': ['10.0.0.9']}, 'background': {}})):
            out = prio.load(self.root)
        self.assertEqual(out['status'], 'failed')
        self.assertIn('set 30 holds rules', out['errors'][0])
        self.assertEqual(self.changes(), [])

    def test_staging_set_enabled_again(self):
        from unittest import mock
        self.fake()
        with mock.patch.object(prio, 'wanted', lambda root: ([{'class': 'interactive', 'target': 'x', 'description': ''}],
                                                             {'interactive': {'x': ['10.0.0.9']}, 'background': {}})):
            prio.load(self.root)
        self.assertEqual(self.calls[-1], ('set', 'enable', '30'))        # as it was before

    def test_set_disabled_parse(self):
        self.fake(sets='disable 30 enable 0 1 2 31')
        self.assertTrue(prio.set_disabled('30'))
        self.assertFalse(prio.set_disabled('3'))


if __name__ == '__main__':
    unittest.main()
