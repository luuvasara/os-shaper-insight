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

"""
    The table convention (user feedback 2026-11-22): address-aware sorting, MAC and manufacturer per host,
    and no hand-built tables left in the pages.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, '..', 'net', 'shaper-insight', 'src', 'opnsense')
SCRIPTS = os.path.join(SRC, 'scripts', 'shaperinsight')
sys.path.insert(0, SCRIPTS)
import hostinfo  # noqa: E402

IPCOMPARE = os.path.join(SRC, 'www', 'js', 'shaperinsight', 'ipcompare.js')


def node_sort(values):
    code = ("const ip = require(%s); const v = JSON.parse(process.argv[1]);"
            "console.log(JSON.stringify(v.slice().sort(ip.compare)));") % json.dumps(IPCOMPARE)
    out = subprocess.run(['node', '-e', code, json.dumps(values)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class AddressSort(unittest.TestCase):
    def test_ipv4_numeric(self):
        self.assertEqual(node_sort(['192.0.2.10', '192.0.2.9', '192.0.2.100', '10.0.0.1']),
                         ['10.0.0.1', '192.0.2.9', '192.0.2.10', '192.0.2.100'])

    def test_ipv4_before_ipv6_before_names(self):
        self.assertEqual(node_sort(['host10', 'fe80::1', '192.168.1.1', 'host2', '::1']),
                         ['192.168.1.1', '::1', 'fe80::1', 'host2', 'host10'])

    def test_ipv6_numeric_not_text(self):
        # as text "2001:db8::10" < "2001:db8::9"; by value 9 < 0x10
        self.assertEqual(node_sort(['2001:db8::10', '2001:db8::9', '2001:db8:0:1::', '2001:db8::a']),
                         ['2001:db8::9', '2001:db8::a', '2001:db8::10', '2001:db8:0:1::'])

    def test_prefix_and_zone(self):
        self.assertEqual(node_sort(['10.0.0.0/24', '10.0.0.0/8', 'fe80::2%igb0', 'fe80::1%igb1']),
                         ['10.0.0.0/8', '10.0.0.0/24', 'fe80::1%igb1', 'fe80::2%igb0'])

    def test_invalid_is_text(self):
        self.assertEqual(node_sort(['999.1.1.1', '1.2.3.4', '1:2:3']), ['1.2.3.4', '1:2:3', '999.1.1.1'])

    def test_control_plain_text_sort_differs(self):
        """ the check can fail: a plain text sort puts .10 before .9 """
        self.assertNotEqual(sorted(['192.0.2.10', '192.0.2.9']), node_sort(['192.0.2.10', '192.0.2.9']))


VERDICTS = os.path.join(SRC, 'www', 'js', 'shaperinsight', 'verdicts.js')


def node_limiting(rates, drops, cap):
    code = ("const v = require(%s); const a = JSON.parse(process.argv[1]);"
            "console.log(JSON.stringify(v.limiting(a[0], a[1], a[2])));") % json.dumps(VERDICTS)
    out = subprocess.run(['node', '-e', code, json.dumps([rates, drops, cap])], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class Limiting(unittest.TestCase):
    """ "Limiting now" only for a pipe at its cap, not for one stray drop (review 2026-11-22) """
    CAP = 38.9e6

    def test_idle_with_a_drop_is_not_limiting(self):
        # the screenshot case: 10.5 kbit/s of 38.9 Mbit/s with a fraction of a drop per second
        self.assertFalse(node_limiting([5e3, 8e3, 10.5e3], [0, 0, 0.04], self.CAP))
        self.assertFalse(node_limiting([5e3, 8e3, 10.5e3], [0, 0, 3], self.CAP))

    def test_sustained_near_cap(self):
        self.assertTrue(node_limiting([1e6, 36e6, 38e6], [0, 0, 0], self.CAP))
        self.assertFalse(node_limiting([1e6, 2e6, 38e6], [0, 0, 0], self.CAP))   # one step is a burst

    def test_drops_at_half_the_cap(self):
        self.assertTrue(node_limiting([1e6, 2e6, 25e6], [0, 0, 2.4], self.CAP))

    def test_gaps_and_no_cap(self):
        self.assertTrue(node_limiting([None, None, 38e6], [None, None, 0], self.CAP))
        self.assertFalse(node_limiting([], [], self.CAP))
        self.assertFalse(node_limiting([38e6], [5], 0))


class HostInfo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.oui = os.path.join(self.tmp, 'oui.csv')
        with open(self.oui, 'w') as f:
            f.write('Registry,Assignment,Organization Name,Organization Address\n'
                    'MA-L,B827EB,Raspberry Pi Foundation,Cambridge\n'
                    'MA-L,3C5282,"Hewlett Packard, Enterprise",Houston\n')

    def table(self):
        with mock.patch.object(hostinfo, 'OUI_FILE', self.oui), mock.patch.object(hostinfo, 'cache_dir', lambda: self.tmp):
            return hostinfo.oui_table()

    def test_vendor_from_core_list(self):
        db = self.table()
        self.assertEqual(hostinfo.vendor(db, 'b8:27:eb:01:02:03'), 'Raspberry Pi Foundation')
        self.assertEqual(hostinfo.vendor(db, '3C-52-82-00-00-01'), 'Hewlett Packard, Enterprise')

    def test_locally_administered(self):
        self.assertTrue(hostinfo.is_random('52:54:00:12:34:56'))
        self.assertTrue(hostinfo.is_random('DA-7F-77-BF-1D-D0'))
        self.assertFalse(hostinfo.is_random('00:11:22:33:44:55'))
        self.assertEqual(hostinfo.vendor({}, '52:54:00:12:34:56'), '')

    def test_cache_follows_the_list(self):
        self.table()
        cache = os.path.join(self.tmp, 'oui.json')
        self.assertTrue(os.path.exists(cache))
        with open(self.oui, 'a') as f:
            f.write('MA-L,001122,Example Corp,Somewhere\n')
        os.utime(self.oui, (1, 12345))
        self.assertEqual(self.table().get('001122'), 'Example Corp')

    def test_missing_list(self):
        with mock.patch.object(hostinfo, 'OUI_FILE', os.path.join(self.tmp, 'none.csv')):
            self.assertEqual(hostinfo.oui_table(), {})

    def test_shared_mac(self):
        """ ipvlan: several addresses on one MAC, each names the others """
        info = hostinfo.host_info({'b8:27:eb:00:00:01': {'192.168.1.10', '192.168.1.11/32', '2001:db8::5'},
                                   '00:11:22:33:44:55': {'192.168.1.20'}}, {'B827EB': 'Raspberry Pi Foundation'})
        self.assertEqual(sorted(info['192.168.1.10']['shared']), ['192.168.1.11', '2001:db8::5'])
        self.assertEqual(info['192.168.1.11']['vendor'], 'Raspberry Pi Foundation')
        self.assertEqual(info['192.168.1.20']['shared'], [])


class NewestStep(unittest.TestCase):
    """ host simulation (TESTING.md): the group rows, search and paging come from the newest step, server side """
    def setUp(self):
        import sampler
        self.s = sampler
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        p = mock.patch.object(sampler, 'NEWEST', os.path.join(self.tmp, 'newest.json'))
        p.start()
        self.addCleanup(p.stop)
        hosts = [['192.168.1.%d' % i, 1000 * i] for i in range(1, 251)] + [['2001:db8::%x' % i, 10] for i in range(1, 2851)]
        self.s.write_newest({'t': 100.0, 'dt': 10.0, 'p': {'10000': hosts}})

    def test_paging_and_cap(self):
        r = self.s.newest('10000', '', 'rate', 0, 99999)
        self.assertEqual(r['total'], 3100)
        self.assertEqual(len(r['hosts']), self.s.PAGE_MAX)
        self.assertEqual(r['hosts'][0], ['192.168.1.250', 200000])
        r2 = self.s.newest('10000', '', 'rate', 50, 50)
        self.assertEqual(len(r2['hosts']), 50)
        self.assertEqual(r2['hosts'][0][0], '192.168.1.200')

    def test_search_and_ip_sort(self):
        r = self.s.newest('10000', '192.168.1.1', 'ip', 0, 50)
        self.assertEqual([h[0] for h in r['hosts']][:4], ['192.168.1.1', '192.168.1.10', '192.168.1.11', '192.168.1.12'])
        self.assertEqual(r['total'], 1 + 10 + 100)
        r = self.s.newest('10000', '', 'ip', 249, 3)
        self.assertEqual([h[0] for h in r['hosts']], ['192.168.1.250', '2001:db8::1', '2001:db8::2'])

    def test_missing_file(self):
        os.unlink(self.s.NEWEST)
        self.assertEqual(self.s.newest('10000')['hosts'], [])

    def test_ring_keeps_top_and_sums_rest(self):
        full = {'10000': {'hosts': [['a', 5], ['b', 4], ['c', 3]], 'other': 1}}
        self.assertEqual(self.s.ring_slots(full, 2), {'10000': {'hosts': [['a', 5], ['b', 4]], 'other': 4}})

    def test_expander_numbers(self):
        series = {'10000': [['a', []], ['b', []], ['', []]]}
        nd = {'p': {'10000': [['a', 5], ['c', 3], ['d', 0], ['e', 2]]}}
        self.assertEqual(self.s.hosts_other(series, {'10000': ['c', 'f']}, nd)['10000'], {'seen': 3, 'sending': 2})

    def test_shown_capped_at_eight(self):
        with mock.patch.object(self.s, 'config_root') as cr:
            cr.return_value.findtext.return_value = '12'
            self.assertEqual(self.s.hosts_shown(), 8)


class TablesInPages(unittest.TestCase):
    """ every table is SA.table (core's Tabulator) unless it is a form, with the reason in data-sa-form """
    VIEWS = os.path.join(SRC, 'mvc', 'app', 'views', 'OPNsense', 'ShaperInsight')

    def test_no_hand_built_tables(self):
        bad = []
        for path in glob.glob(os.path.join(self.VIEWS, '*.volt')):
            with open(path) as f:
                text = f.read()
            for m in re.finditer(r'<table\b[^>]*>', text):
                if 'data-sa-form="' not in m.group(0):
                    bad.append('%s: %s' % (os.path.basename(path), m.group(0)[:60]))
        self.assertEqual(bad, [])

    def test_control_detects_a_table(self):
        self.assertTrue(re.search(r'<table\b[^>]*>', '<div><table class="x"></table></div>'))

    def test_host_tables_have_separate_columns(self):
        with open(os.path.join(self.VIEWS, 'sharing.volt')) as f:
            text = f.read()
        for col in ('"Name"', '"IP"', '"MAC"', '"Manufacturer"'):
            self.assertIn("lang._(%s)" % col, text)
        self.assertIn("type: 'ip'", text)


if __name__ == '__main__':
    unittest.main()
