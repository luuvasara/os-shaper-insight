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
        # 1.0.k newest rows carry more fields: [host, sent, offered, {method: bytes}] (the lab caught the unpack)
        nd2 = {'p': {'10000': [['a', 5, 6], ['c', 3, 9, {'fair': 3}], ['d', 0, 0], ['e', 2, 2]]}}
        self.assertEqual(self.s.hosts_other(series, {'10000': ['c', 'f']}, nd2)['10000'], {'seen': 3, 'sending': 2})

    def test_shown_capped_at_eight(self):
        with mock.patch.object(self.s, 'config_root') as cr:
            cr.return_value.findtext.return_value = '12'
            self.assertEqual(self.s.hosts_shown(), 8)


DELTA_JS = os.path.join(SRC, 'www', 'js', 'shaperinsight', 'delta.js')


def fake_history(start, n, step=10, window=3600, hosts=('a', 'b', ''), gap=()):
    """ a history answer shaped as sampler.history() makes it: row k is stamped start + k * step;
    row values encode their own stamp so a merge can be checked row by row """
    def col(f=1):
        return [None if start + k * step in gap else (start + k * step) * f for k in range(n)]
    pipes = {'10000': {'start': start, 'step': step, 'series': {'bytes': col(), 'drops': col(0)},
                       'max_bits': col(), 'cap_rows': col(0), 'tin_series': {'t0b': col(), 't1b': col()}, 'above_cap': 0},
             '10001': {'start': start, 'step': step, 'series': {'bytes': col(2), 'drops': col(0)},
                       'max_bits': col(2), 'cap_rows': col(0), 'above_cap': 0}}
    return {'window': window, 'step': step, 'pipes': pipes, 'delta': False,
            'hosts': {p: [[h, col(3)] for h in hosts] for p in pipes}, 'hosts_offered': {p: col(4) for p in pipes}}


def node_merge(old, new):
    code = ("const m = require(%s); const a = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
            "console.log(JSON.stringify(m.merge(a[0], a[1])));") % json.dumps(DELTA_JS)
    out = subprocess.run(['node', '-e', code], input=json.dumps([old, new]), capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


class IncrementalHistory(unittest.TestCase):
    """ ?since= polling: the server sends the rows from two steps before since; the page merges them """
    def setUp(self):
        import sampler
        self.s = sampler

    def test_delta_sends_only_new_rows(self):
        full = fake_history(1000, 360)
        last = 1000 + 359 * 10
        d = self.s.delta(fake_history(1010, 360), last - 10)
        self.assertTrue(d['delta'])
        self.assertEqual(d['from'], last - 20)
        self.assertEqual(d['pipes']['10000']['series']['bytes'], [last - 20, last - 10, last, last + 10])
        self.assertEqual(len(d['hosts']['10000'][0][1]), 4)
        self.assertEqual(len(d['pipes']['10000']['tin_series']['t1b']), 4)
        self.assertLess(len(json.dumps(d)), len(json.dumps(full)) / 20)

    def test_since_off_the_step_grid(self):
        # a client stamp between two RRD rows counts as the row it falls in (clock vs RRD step boundary)
        a = self.s.delta(fake_history(1000, 360), 1000 + 300 * 10 + 7.5)
        b = self.s.delta(fake_history(1000, 360), 1000 + 300 * 10)
        self.assertEqual(a['from'], b['from'])
        self.assertEqual(a['pipes']['10000']['series']['bytes'][0], 1000 + 299 * 10)

    def test_full_when_since_left_the_window_or_grids_differ(self):
        self.assertFalse(self.s.delta(fake_history(5000, 360), 4000)['delta'])     # gap too long
        self.assertFalse(self.s.delta(fake_history(1000, 360), 'x')['delta'])
        f = fake_history(1000, 360)
        f['pipes']['10001']['start'] = 1010                                         # pipes not on one grid
        self.assertFalse(self.s.delta(f, 3000)['delta'])

    @unittest.skipUnless(shutil.which('node'), 'node not installed')
    def test_page_merge_equals_full_answer(self):
        old = fake_history(1000, 360)
        new_full = fake_history(1030, 360)            # three steps later
        d = self.s.delta(new_full, 1000 + 359 * 10)
        m = node_merge(old, d)
        for n in ('10000', '10001'):
            self.assertEqual(m['pipes'][n]['start'], 1030)
            self.assertEqual(m['pipes'][n]['series'], new_full['pipes'][n]['series'])
            self.assertEqual(m['pipes'][n]['max_bits'], new_full['pipes'][n]['max_bits'])
        self.assertEqual(m['pipes']['10000']['tin_series'], new_full['pipes']['10000']['tin_series'])
        self.assertEqual(m['hosts'], new_full['hosts'])
        self.assertEqual(m['hosts_offered'], new_full['hosts_offered'])

    @unittest.skipUnless(shutil.which('node'), 'node not installed')
    def test_sampler_restart_mid_window(self):
        # the sampler was down for 5 steps: those rows are empty in the RRD, before and after the merge
        gap = {1000 + k * 10 for k in range(350, 355)}
        old = fake_history(1000, 352, gap=gap)        # polled while it was down: trailing rows not there yet
        new_full = fake_history(1020, 360, gap=gap)   # back up, the window moved on two steps
        m = node_merge(old, self.s.delta(new_full, 1000 + 351 * 10))
        self.assertEqual(m['pipes']['10000']['start'], 1020)
        self.assertEqual(m['pipes']['10000']['series']['bytes'], new_full['pipes']['10000']['series']['bytes'])
        self.assertIsNone(m['pipes']['10000']['series']['bytes'][350])

    @unittest.skipUnless(shutil.which('node'), 'node not installed')
    def test_page_asks_for_everything_when_it_cannot_merge(self):
        old = fake_history(1000, 360)
        d = self.s.delta(fake_history(1010, 360, hosts=('a', 'c', '')), 1000 + 359 * 10)
        self.assertIsNone(node_merge(old, d))                                       # other coloured hosts
        d = self.s.delta(fake_history(1010, 360), 1000 + 359 * 10)
        self.assertIsNone(node_merge(dict(old, window=600), d))                     # window changed
        d2 = dict(d, pipes={'10000': d['pipes']['10000']})
        self.assertIsNone(node_merge(old, d2))                                      # a pipe went away
        self.assertIsNone(node_merge(None, d))                                      # nothing to merge into

    @unittest.skipUnless(shutil.which('node'), 'node not installed')
    def test_every_host_series_merge_by_name(self):
        # ?all=1: hosts come and go; the merge must equal the full answer three steps later
        old = fake_history(1000, 360)
        old['hosts_all'] = {'10000': [['a', [1] * 360], ['b', [2] * 360]]}
        new_full = fake_history(1030, 360)
        new_full['hosts_all'] = {'10000': [['b', [2] * 357 + [5, 5, 5]], ['a', [1] * 360], ['c', [0] * 357 + [7, 7, 7]]]}
        d = self.s.delta(new_full, 1000 + 359 * 10)
        self.assertEqual(len(d['hosts_all']['10000'][0][1]), 5)
        m = node_merge(old, d)
        self.assertEqual(m['hosts_all'], new_full['hosts_all'])
        self.assertIsNone(node_merge(fake_history(1000, 360), d))                  # page had no host list yet


class TablesInPages(unittest.TestCase):
    """ every table is SA.table (core's Tabulator) unless it is a form, with the reason in data-sa-form """
    VIEWS = os.path.join(SRC, 'mvc', 'app', 'views', 'OPNsense', 'ShaperInsight')

    SHARING_JS = os.path.join(SRC, 'www', 'js', 'shaperinsight', 'sharing')

    def test_no_hand_built_tables(self):
        bad = []
        for path in glob.glob(os.path.join(self.VIEWS, '*.volt')) + glob.glob(os.path.join(self.SHARING_JS, '*.js')):
            with open(path) as f:
                text = f.read()
            for m in re.finditer(r'<table\b[^>]*>', text):
                if 'data-sa-form="' not in m.group(0):
                    bad.append('%s: %s' % (os.path.basename(path), m.group(0)[:60]))
        self.assertEqual(bad, [])

    def test_control_detects_a_table(self):
        self.assertTrue(re.search(r'<table\b[^>]*>', '<div><table class="x"></table></div>'))

    def test_host_tables_have_separate_columns(self):
        # the hosts table moved from sharing.volt into the sharing data layer (1.0.j)
        with open(os.path.join(self.SHARING_JS, 'model.js')) as f:
            text = f.read()
        for col in ("'Name'", "'IP'", "'MAC'", "'Manufacturer'"):
            self.assertIn("title: %s" % col, text)
        self.assertIn("type: 'ip'", text)


if __name__ == '__main__':
    unittest.main()
