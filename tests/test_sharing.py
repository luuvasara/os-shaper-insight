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

"""Who uses each pipe (2026-11-22, TESTING.md speed-test analysis F1-F5): pf state attribution, the
payload cap, counter stamps taken at the read, the history sampler's RRD layout and host rows,
and priorities by port with the shared-MAC warning."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'net', 'shaper-insight', 'src', 'opnsense', 'scripts', 'shaperinsight')
sys.path.insert(0, SCRIPTS)
import prio  # noqa: E402
import sampler  # noqa: E402
import status  # noqa: E402
from lib import traffic  # noqa: E402

# pfctl -vss as FreeBSD 15 / OPNsense 26.7 prints it (floating states, "all")
STATES = """No ALTQ support in kernel
all tcp 198.51.100.2:61000 (192.168.50.9:49110) -> 203.0.113.5:443       ESTABLISHED:ESTABLISHED
   [1 + 2] wscale 7  [3 + 4] wscale 7
   age 00:10:00, expires in 23:59:58, 100:200 pkts, 1000000:5000000 bytes, rule 80
all tcp 203.0.113.5:443 <- 192.168.50.9:49110       ESTABLISHED:ESTABLISHED
   age 00:10:00, expires in 23:59:58, 100:200 pkts, 1000000:5000000 bytes, rule 12
all udp 2001:db8:1::20[51820] -> 2001:db8:ffff::1[51820]       MULTIPLE:MULTIPLE
   age 00:00:05, expires in 00:01:00, 10:10 pkts, 70000:3000 bytes, rule 81
all udp 198.51.100.2:123 -> 192.0.2.123:123       MULTIPLE:SINGLE
   age 00:00:40, expires in 00:12:06, 1:1 pkts, 76:76 bytes, rule 75
all tcp 192.168.50.1:443 <- 192.168.50.30:50000       ESTABLISHED:ESTABLISHED
   age 00:00:40, expires in 00:12:06, 9:9 pkts, 900:900 bytes, rule 7
all tcp 192.168.50.20:8443 (198.51.100.2:8443) <- 203.0.113.77:40000       ESTABLISHED:ESTABLISHED
   age 01:00:00, expires in 23:59:58, 5:5 pkts, 4000:9000 bytes, rule 90
"""
IFCONFIG = """igb0: flags=1008843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST,LOWER_UP> metric 0 mtu 1500
	inet 198.51.100.2 netmask 0xffffff00 broadcast 198.51.100.255
	inet6 fe80::1%igb0 prefixlen 64 scopeid 0x1
	inet6 2001:db8:0:1::2 prefixlen 64
igb1: flags=1008843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST,LOWER_UP> metric 0 mtu 1500
	inet 192.168.50.1 netmask 0xffffff00 broadcast 192.168.50.255
	inet6 2001:db8:1::1 prefixlen 64
lo0: flags=1008049<UP,LOOPBACK,RUNNING,MULTICAST,LOWER_UP> metric 0 mtu 16384
	inet 127.0.0.1 netmask 0xff000000
"""
CFG = {
    'pipes': [{'uuid': 'p1', 'number': '10000', 'bandwidth': '910', 'bandwidthMetric': 'Mbit', 'enabled': '1'},
              {'uuid': 'p2', 'number': '10001', 'bandwidth': '840', 'bandwidthMetric': 'Mbit', 'enabled': '1'},
              {'uuid': 'p3', 'number': '10002', 'bandwidth': '10', 'bandwidthMetric': 'Mbit', 'enabled': '1'}],
    'queues': [{'uuid': 'q1', 'pipe': 'p2', 'enabled': '1'}],
    'rules': [{'uuid': 'r1', 'interface': 'wan', 'direction': 'in', 'target': 'p1', 'enabled': '1'},
              {'uuid': 'r2', 'interface': 'wan', 'direction': 'out', 'target': 'q1', 'enabled': '1'},
              {'uuid': 'r3', 'interface': 'wan', 'direction': 'out', 'target': 'p3', 'enabled': '1'}],
    'cake': [{'pipe': 'p1', 'enabled': '1', 'overhead': '42', 'link': ''},
             {'pipe': 'p2', 'enabled': '1', 'overhead': '', 'link': 'docsis'}],
    'interfaces': {'wan': {'if': 'igb0'}, 'lan': {'if': 'igb1'}},
}


def topo():
    return traffic.Topology(traffic.parse_ifconfig(IFCONFIG), {'igb0'})


class PayloadCap(unittest.TestCase):
    def test_840_with_42_bytes_is_817(self):
        # TESTING.md speed-test analysis 2.2: 840 x 1500/1542 = ~817 Mbit/s of IP packets
        self.assertEqual(round(traffic.payload_cap(840e6, 42) / 1e6, 1), 817.1)

    def test_overheads_numeric_first_then_link_preset(self):
        self.assertEqual(traffic.cake_overheads(CFG), {'10000': 42, '10001': 18})


class States(unittest.TestCase):
    def test_parse(self):
        st = traffic.parse_states(STATES)
        self.assertEqual(len(st), 6)
        self.assertEqual(str(st[0]['left_nat']), '192.168.50.9')
        self.assertEqual((st[0]['b0'], st[0]['b1'], st[0]['age']), (1000000, 5000000, 600))
        self.assertEqual(str(st[2]['left']), '2001:db8:1::20')

    def test_attribution(self):
        t = topo()
        got = [traffic.attribute(s, t) for s in traffic.parse_states(STATES)]
        # NAT'd upload is credited to the LAN host on the WAN device
        self.assertEqual(got[0], ('192.168.50.9', 'igb0', 1000000, 5000000))
        # the LAN-side twin of the same connection is not counted again
        self.assertIsNone(got[1])
        # IPv6 without NAT: the host itself, on the only shaped device with IPv6
        self.assertEqual(got[2], ('2001:db8:1::20', 'igb0', 70000, 3000))
        # the firewall's own traffic
        self.assertEqual(got[3], (traffic.FIREWALL, 'igb0', 76, 76))
        # LAN to the firewall: not internet traffic
        self.assertIsNone(got[4])
        # a port forward: inbound, so b0 (in) is the host's download
        self.assertEqual(got[5], ('192.168.50.20', 'igb0', 9000, 4000))

    def test_pipe_map_takes_the_big_pipe_per_direction(self):
        self.assertEqual(traffic.pipe_map(CFG), {('igb0', 'down'): '10000', ('igb0', 'up'): '10001'})

    def test_deltas_between_reads(self):
        t = topo()
        hc = traffic.HostCounter()
        self.assertEqual(hc.update(traffic.parse_states(STATES), t, 10), {})     # first read: baseline
        later = STATES.replace('1000000:5000000 bytes', '3000000:6000000 bytes').replace(
            '70000:3000 bytes', '90000:3000 bytes')
        later += ("all tcp 198.51.100.2:61001 (192.168.50.10:50000) -> 203.0.113.6:443       ESTABLISHED:ESTABLISHED\n"
                  "   age 00:00:04, expires in 23:59:58, 1:1 pkts, 500:700 bytes, rule 80\n"
                  "all tcp 198.51.100.2:61002 (192.168.50.11:50000) -> 203.0.113.6:443       ESTABLISHED:ESTABLISHED\n"
                  "   age 02:00:00, expires in 23:59:58, 1:1 pkts, 99999:99999 bytes, rule 80\n")
        d = hc.update(traffic.parse_states(later), t, 10)
        up = d[('igb0', 'up')]
        self.assertEqual(up['192.168.50.9'], 2000000)
        self.assertEqual(up['2001:db8:1::20'], 20000)
        self.assertEqual(up['192.168.50.10'], 500)          # new since the last read: counted in full
        self.assertNotIn('192.168.50.11', up)               # older than the sampler: from now on only
        self.assertEqual(d[('igb0', 'down')]['192.168.50.9'], 1000000)
        p = traffic.by_pipe(d, traffic.pipe_map(CFG), top=2)
        self.assertEqual(p['10001']['hosts'][0], ['192.168.50.9', 2000000])
        self.assertEqual(len(p['10001']['hosts']), 2)
        self.assertEqual(p['10001']['other'], 500)


class StampAtRead(unittest.TestCase):
    """ the "900 on an 840 pipe": the stamp must be the counter read, not the snapshot start """

    def snap(self, generated, read_at, cake_bytes):
        line = 'sched 10001 inst 0 diffserv 0 tin 0 bytes %d pkts 1 drops 0 marks 0 backlog 0' % cake_bytes
        from lib import parse_cake_stats
        return {'generated': generated, 'config': {'pipes': [], 'queues': [], 'rules': [], 'cake': []},
                'kernel': {'cake_stats': parse_cake_stats(line), 'ipfw': {'rules': {}}, 'dnctl': {'pipes': {}},
                           'counters_ts': {'cake': read_at, 'ipfw': read_at}}}

    def test_rate_uses_read_times(self):
        # 100 Mbit/s of traffic. Snapshot 1 started at 100 and read the counters at once;
        # snapshot 2 started at 105 and read them at 106 (config re-parse, os-cake status): 6 s of
        # bytes. Divided by the 5 s between the starts that is 120 Mbit/s, 20 % high (the "900 on
        # an 840 pipe"); divided by the 6 s between the reads it is right
        a = status.rates(self.snap(100.0, 100.0, 0))
        b = status.rates(self.snap(105.0, 106.0, 100000000 // 8 * 6))
        pa, pb = a['pipes']['10001'], b['pipes']['10001']
        rate = (pb['bytes'] - pa['bytes']) * 8 / (pb['ts'] - pa['ts'])
        self.assertAlmostEqual(rate / 1e6, 100.0, places=6)
        self.assertEqual((pa['ts'], pb['ts'], b['ts']), (100.0, 106.0, 106.0))

    def test_live_chart_skips_short_pairs_and_flags_above_cap(self):
        src = open(os.path.join(SCRIPTS, '..', '..', 'mvc', 'app', 'views', 'OPNsense', 'ShaperInsight',
                                'index.volt')).read()
        self.assertIn('dt >= 2', src)
        self.assertIn('a.payload_cap * 1.01', src)


class Sampler(unittest.TestCase):
    def test_rrd_layout(self):
        args = sampler.create_args('/x/pipe10000-shaper.rrd')
        self.assertIn('--step', args)
        self.assertEqual(args[args.index('--step') + 1], '10')
        self.assertIn('DS:bytes:DERIVE:30:0:12500000000', args)
        # 10 s x 6 h, 1 min x 3 d, 10 min x 30 d, 1 h x 1 y, AVERAGE and MAX
        self.assertEqual([a for a in args if a.startswith('RRA:AVERAGE')],
                         ['RRA:AVERAGE:0.5:1:2160', 'RRA:AVERAGE:0.5:6:4320', 'RRA:AVERAGE:0.5:60:4320',
                          'RRA:AVERAGE:0.5:360:8760'])
        self.assertEqual(len([a for a in args if a.startswith('RRA:MAX')]), 4)
        tins = sampler.create_args('/x/pipe10000-tins-shaper.rrd', 4)
        self.assertEqual(len([a for a in tins if a.startswith('DS:')]), 8)
        self.assertFalse([a for a in tins if a.startswith('RRA:MAX')])

    def test_health_page_naming(self):
        # core's listReports.py: topic = text after the last '-', so these show under "shaper"
        self.assertTrue(sampler.rrd_path('10000').endswith('/pipe10000-shaper.rrd'))
        self.assertTrue(sampler.rrd_path('10000', True).endswith('/pipe10000-tins-shaper.rrd'))

    def test_choose_step(self):
        self.assertEqual(sampler.choose_step(3600), 10)
        self.assertEqual(sampler.choose_step(21600), 60)
        self.assertEqual(sampler.choose_step(259200), 600)
        self.assertEqual(sampler.choose_step(2592000), 3600)

    def test_parse_fetch(self):
        out = "                bytes drops\n\n1795353810: 4.8e+06 -nan\n1795353820: nan 1.0\n"
        names, rows = sampler.parse_fetch(out)
        self.assertEqual(names, ['bytes', 'drops'])
        self.assertEqual(rows, [(1795353810, [4.8e6, None]), (1795353820, [None, 1.0])])

    def test_host_rows_line_up_with_rrd_rows(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'hosts.jsonl')
            with open(path, 'w') as f:
                for t, b in ((1010.004, 1000), (1020.006, 2000), (1030.003, 4000)):
                    f.write(json.dumps({'t': t, 'dt': 10.0, 'p': {'10001': {'hosts': [['h', b]], 'other': 0}}}) + '\n')
            with mock.patch.object(sampler, 'HOSTS', path):
                series, first, _ = sampler.host_series(1010, 10, 3)
        # each sample measured the 10 s before it: row 1010 holds the sample at 1010.004
        self.assertEqual(series['10001'], [['h', [800, 1600, 3200]]])
        self.assertEqual(first, 1010.004)

    def test_ring_is_cut_back(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'hosts.jsonl')
            with mock.patch.object(sampler, 'HOSTS', path), mock.patch.object(sampler, 'HOSTS_KEEP', 8):
                if hasattr(sampler.append_hosts, 'count'):
                    del sampler.append_hosts.count
                for i in range(25):
                    sampler.append_hosts({'t': i})
                with open(path) as f:
                    lines = f.readlines()
                del sampler.append_hosts.count
        self.assertLessEqual(len(lines), 10)
        self.assertEqual(json.loads(lines[-1])['t'], 24)

    def test_ring_restarts_when_the_file_vanishes(self):
        # a /var/run cleanup removed the file under a running sampler (lab, 2026-11-22): the line
        # count starts again instead of trusting the old one
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'hosts.jsonl')
            with mock.patch.object(sampler, 'HOSTS', path), mock.patch.object(sampler, 'HOSTS_KEEP', 8):
                for i in range(9):
                    sampler.append_hosts({'t': i})
                os.unlink(path)
                sampler.append_hosts({'t': 99})
                self.assertEqual(sampler.append_hosts.count, 1)
                del sampler.append_hosts.count

    def test_supervisor_identity(self):
        with mock.patch.object(sampler, 'PIDFILE', '/nonexistent/pid'):
            self.assertIsNone(sampler.live_pid())


class PortPriorities(unittest.TestCase):
    def test_port_spec(self):
        self.assertEqual(prio.port_spec('udp/51820'), (['udp'], '51820'))
        self.assertEqual(prio.port_spec('TCP+UDP/6881-6889'), (['tcp', 'udp'], '6881-6889'))
        self.assertIsNone(prio.port_spec('udp/70000'))
        self.assertIsNone(prio.port_spec('tcp/90-80'))
        self.assertIsNone(prio.port_spec('192.168.1.0/24'))
        self.assertEqual(prio.resolve('udp/51820', {}), [])

    def test_rules_both_ends_both_directions(self):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(open(os.path.join(HERE, 'test_prio.py')).read().split('CFG = """')[1].split('"""')[0])
        rules, errors = prio.build_rules(root, [{'class': 'background', 'target': 'udp/51820'},
                                                {'class': 'background', 'target': 'tcp+udp/6881-6889'}])
        self.assertEqual(errors, [])
        text = [' '.join(r) for r in rules]
        self.assertIn('2010 setdscp cs1 udp from any 51820,6881-6889 to any out via igb0', text)
        self.assertIn('2010 setdscp cs1 udp from any to any 51820,6881-6889 out via igb0', text)
        self.assertIn('2011 setdscp cs1 tcp from any 6881-6889 to any in via igb0', text)
        self.assertIn('2011 setdscp cs1 tcp from any to any 6881-6889 in via igb0', text)
        # the address rules of the class stay, and no number outside the class block is used
        self.assertIn('2010 setdscp cs1 ip from table(si_prio_background) to any out via igb0', text)
        self.assertTrue(all(2000 <= int(r[0]) <= 2049 for r in rules))

    def test_shared_mac_warning(self):
        self.assertEqual(prio.mac_warning('aa:bb:cc:dd:ee:01', ['192.168.1.20', '2001:db8::20']), '')
        w = prio.mac_warning('aa:bb:cc:dd:ee:01', ['192.168.50.8', '192.168.50.9', '192.168.50.10'])
        self.assertIn('3 IPv4 addresses use this MAC', w)
        self.assertIn('ipvlan', w)
        self.assertEqual(prio.mac_warning('10.0.0.5', ['10.0.0.5/32', '10.0.0.6/32']), '')


class Verification4(unittest.TestCase):
    """ TESTING.md, Verification 4: N9-N14 and the pkg add -f note """

    def ring(self, d, recs):
        path = os.path.join(d, 'hosts.jsonl')
        with open(path, 'w') as f:
            for r in recs:
                f.write(json.dumps(r) + '\n')
        return path

    def test_n9_hosts_scaled_to_delivered(self):
        # the lab case: hosts 31.5 + 3.79 + 27.4 = 62.7 Mbit/s offered, the pipe sent 58.4
        mb = 1e6 / 8 * 10       # bytes in 10 s per Mbit/s
        rec = {'t': 1010.0, 'dt': 10.0, 'p': {'10000': {'hosts': [['a', 31.5 * mb], ['b', 3.79 * mb], ['c', 27.4 * mb]],
                                                        'other': 0, 'delivered': 58.4 * mb}}}
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(sampler, 'HOSTS', self.ring(d, [rec])):
                series, _, offered = sampler.host_series(1010, 10, 1)
        total = sum(h[1][0] for h in series['10000'])
        self.assertAlmostEqual(total / 1e6, 58.4, places=1)         # never more than the pipe sent
        self.assertAlmostEqual(offered['10000'][0] / 1e6, 62.69, places=1)
        shares = [h[1][0] / total for h in series['10000']]
        self.assertAlmostEqual(sum(shares), 1.0, places=3)           # not 107 %

    def test_n9_no_scaling_up(self):
        # delivered above what pf saw: the rest is "not attributed", hosts are not inflated
        rec = {'t': 1010.0, 'dt': 10.0, 'p': {'1': {'hosts': [['a', 1000]], 'other': 0, 'delivered': 5000}}}
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(sampler, 'HOSTS', self.ring(d, [rec])):
                series, _, _ = sampler.host_series(1010, 10, 1)
        self.assertEqual(series['1'][0][1], [800])

    def test_n9_long_interval_spread_over_rows(self):
        # adaptive interval: one 30 s record covers three 10 s rows
        rec = {'t': 1030.0, 'dt': 30.0, 'p': {'1': {'hosts': [['a', 3000]], 'other': 0}}}
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(sampler, 'HOSTS', self.ring(d, [rec])):
                series, _, _ = sampler.host_series(1010, 10, 3)
        self.assertEqual(series['1'][0][1], [800, 800, 800])

    def test_n9_long_interval_follows_the_pipe(self):
        # lab: a 20 s record (35.9 Mbit/s then nothing) must not show 17.9 + 17.9
        rec = {'t': 1020.0, 'dt': 20.0, 'p': {'1': {'hosts': [['a', 2000]], 'other': 0, 'delivered': 2000}}}
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(sampler, 'HOSTS', self.ring(d, [rec])):
                series, _, _ = sampler.host_series(1010, 10, 2, {'1': [1600, 0]})
        self.assertEqual(series['1'][0][1], [1600, 0])

    def test_n10_failed_read_is_not_off(self):
        with tempfile.TemporaryDirectory() as d:
            half = os.path.join(d, 'config.xml')
            with open(half, 'w') as f:
                f.write('<opnsense><OPNsense><ShaperIns')            # a save caught half way
            with mock.patch.object(sampler, 'CONFIG', half), mock.patch.object(sampler.time, 'sleep'):
                self.assertIsNone(sampler.enabled())
            with open(half, 'w') as f:
                f.write('<opnsense><OPNsense><ShaperInsight><general><sampler>0</sampler></general>'
                        '</ShaperInsight></OPNsense></opnsense>')
            with mock.patch.object(sampler, 'CONFIG', half):
                self.assertIs(sampler.enabled(), False)
            with mock.patch.object(sampler, 'CONFIG', os.path.join(d, 'missing.xml')), \
                    mock.patch.object(sampler.time, 'sleep'):
                self.assertIsNone(sampler.enabled())

    def test_n10_shared_reader_retries_then_raises(self):
        import lib
        import xml.etree.ElementTree as ET
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'config.xml')
            with open(path, 'w') as f:
                f.write('<opnsense><TrafficSh')
            with mock.patch.object(lib.time, 'sleep') as sl:
                with self.assertRaises(ET.ParseError):
                    lib.config_root(path, tries=3)
                self.assertEqual(sl.call_count, 2)          # retried, not given up on the first read
            with open(path, 'w') as f:
                f.write('<opnsense><OPNsense/></opnsense>')
            self.assertEqual(lib.config_root(path).tag, 'opnsense')

    def test_n10_read_waits_for_the_writer_lock(self):
        import fcntl
        import threading
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'config.xml')
            with open(path, 'w') as f:
                f.write('<opnsense/>')
            w = open(path, 'r+')
            fcntl.flock(w, fcntl.LOCK_EX)               # core's Config::save holds this while it writes
            got = []
            with mock.patch.object(sampler, 'CONFIG', path):
                t = threading.Thread(target=lambda: got.append(sampler.enabled(tries=1)))
                t.start()
                t.join(0.3)
                self.assertTrue(t.is_alive())           # still waiting for the lock
                w.truncate(0)
                w.write('<opnsense><OPNsense><ShaperInsight><general><sampler>1</sampler></general>'
                        '</ShaperInsight></OPNsense></opnsense>')
                w.flush()
                fcntl.flock(w, fcntl.LOCK_UN)
                t.join(3)
            w.close()
        self.assertEqual(got, [True])

    def test_n10_watchdog_starts_a_stopped_sampler(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(sampler, 'enabled', return_value=True), \
                    mock.patch.object(sampler, 'live_pid', return_value=None), \
                    mock.patch.object(sampler, 'start', return_value={'status': 'ok', 'running': True}), \
                    mock.patch.object(sampler, 'WATCHDOG', os.path.join(d, 'wd.json')), \
                    mock.patch.object(sampler, 'cache_dir'), mock.patch.object(sampler, 'log') as log:
                self.assertEqual(sampler.ensure()['action'], 'started')
                self.assertEqual(sampler.watchdog_info()['count'], 1)
                self.assertIn('not running', log.call_args[0][0])
            with mock.patch.object(sampler, 'enabled', return_value=None), \
                    mock.patch.object(sampler, 'start') as st:
                self.assertEqual(sampler.ensure()['action'], 'none')   # unknown setting: leave it be
                st.assert_not_called()

    def test_n10_health_flags_a_stopped_sampler(self):
        from lib.health import run_checks
        sys.path.insert(0, HERE)
        from test_health import SNAP
        c = {x['id']: x for x in run_checks(SNAP, ['wan'], sampler={'enabled': True, 'state': 'stopped'})}
        self.assertEqual(c['S10']['level'], 'warn')
        c = {x['id']: x for x in run_checks(SNAP, ['wan'], sampler={'enabled': True, 'state': 'running'})}
        self.assertEqual(c['S10']['level'], 'ok')

    def test_n11_state_cap_and_adaptive_interval(self):
        with mock.patch.object(sampler, 'run', return_value=(0, 'State Table\n  current entries   60000\n', '')):
            ts, secs, text, n, why = sampler.read_states()
        self.assertIsNone(text)
        self.assertEqual(n, 60000)
        self.assertIn('paused', why)
        # the per-host read is spaced out by its cost: 470 ms -> every 4th step, capped at 6
        self.assertEqual(max(1, min(6, int(470 // sampler.PF_BUDGET_MS) + 1)), 4)

    def test_n11_states_streamed_with_small_keys(self):
        from lib.traffic import iter_states, HostCounter
        text = ('all tcp 192.0.2.1:1 -> 198.51.100.1:443       ESTABLISHED:ESTABLISHED\n'
                '   [0 + 0]  [0 + 0]\n   age 00:00:05, expires in 23:59:59, 3:2 pkts, 300:200 bytes\n') * 3
        it = iter_states(text)
        self.assertFalse(isinstance(it, list))
        st = next(it)
        self.assertIsInstance(st['key'], int)
        hc = HostCounter()
        hc.update(iter_states(text), None if False else type('T', (), {'own': {}, 'is_local': lambda s, a: False,
                                                                       'is_remote': lambda s, a: True, 'single': {}})(), 10)
        self.assertEqual(hc.count, 3)

    def test_n12_pipes_do_not_wait_for_pf(self):
        calls = []

        def fake(argv, timeout=10):
            calls.append((argv[0], timeout))
            return 0, '', ''
        with mock.patch.object(sampler, 'run', side_effect=fake):
            sampler.read_pipes()
        self.assertNotIn(sampler.PFCTL, [c[0] for c in calls])
        calls.clear()
        with mock.patch.object(sampler, 'run', side_effect=fake):
            sampler.read_states()
        self.assertTrue(all(t <= sampler.PF_TIMEOUT for _, t in calls))

    def test_n12_slow_pf_logged_once(self):
        with mock.patch.object(sampler, 'log') as log:
            q = sampler.Quiet()
            for _ in range(59):
                q.fail('pfctl -vss took over 5 s')
            self.assertEqual(log.call_count, 1)
            q.fail('pfctl -vss took over 5 s')
            self.assertEqual(log.call_count, 2)
            q.ok('per-host view back')
            self.assertEqual(log.call_count, 3)

    def test_n13_cap_of_its_time(self):
        caps = [[1000, 885e6], [1100, 58.4e6]]          # the lab lowered the pipe at t=1100
        rows = sampler.cap_rows(caps, 1050, 50, 3)       # rows at 1050, 1100, 1150
        self.assertEqual(rows, [885e6, 58.4e6, 58.4e6])
        self.assertEqual(sampler.above_cap([90e6, 57e6, 58e6], rows), 0)    # 90 at 910 is fine
        self.assertEqual(sampler.above_cap([90e6, 70e6, 58e6], rows), 1)
        self.assertEqual(sampler.cap_rows([[2000, 1e6]], 1000, 10, 2), [None, None])  # unknown: not judged
        caps2 = {}
        sampler.note_caps(caps2, {'1': {'payload_cap': 5}}, 10)
        sampler.note_caps(caps2, {'1': {'payload_cap': 5}}, 20)
        sampler.note_caps(caps2, {'1': {'payload_cap': 6}}, 30)
        self.assertEqual(caps2['1'], [[10, 5], [30, 6]])

    def test_n14_restarting_is_not_running(self):
        with mock.patch.object(sampler, 'live_pid', return_value=123), \
                mock.patch.object(sampler, 'child_alive', return_value=False):
            self.assertEqual(sampler.run_state(), 'restarting')
        with mock.patch.object(sampler, 'live_pid', return_value=None):
            self.assertEqual(sampler.run_state(), 'stopped')

    def test_n14_broken_rrd_moved_aside(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'pipe1-shaper.rrd')
            open(path, 'w').close()
            r = sampler.Rrd()

            class P:
                def poll(self):
                    return None
            r.proc = P()
            r.proc.stdin = mock.MagicMock()
            r.proc.stdout = mock.MagicMock()
            r.proc.stdout.readline.return_value = 'ERROR: not an RRD file\n'
            with mock.patch.object(r, '_readline', side_effect=lambda: 'ERROR: not an RRD file\n'), \
                    mock.patch.object(sampler, 'log'):
                for _ in range(3):
                    r.cmd(['update', path, 'N:1'])
            self.assertFalse(os.path.exists(path))
            self.assertTrue(os.path.exists(path + '.broken'))

    def test_n14_symlinked_rrd_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'pipe1-shaper.rrd')
            os.symlink(os.path.join(d, 'elsewhere'), path)
            r = sampler.Rrd()
            with mock.patch.object(r, 'cmd', return_value=True) as cmd:
                r.ensure(path)
            self.assertFalse(os.path.islink(path))
            self.assertEqual(cmd.call_args[0][0][0], 'create')

    def test_pkg_change_stops_the_sampler(self):
        with tempfile.TemporaryDirectory() as d:
            v = os.path.join(d, 'shaper-insight')
            with open(v, 'w') as f:
                f.write('{"product_version": "1.0.g"}')
            with mock.patch.object(sampler, 'VERSION_FILE', v):
                before = sampler.package_id()
                with open(v, 'w') as f:
                    f.write('{"product_version": "1.0.d"}')     # pkg add -f of an older package
                self.assertNotEqual(sampler.package_id(), before)
                os.unlink(v)
                self.assertIsNone(sampler.package_id())         # pkg delete
        with open(os.path.join(SCRIPTS, 'sampler.py')) as f:
            src = f.read()
        self.assertIn("if pkg is not None and now_pkg != pkg:", src)

    def test_end_supervisor_spares_a_newer_supervisor(self):
        # the pidfile may already name the new package's supervisor: only our own parent is ended
        with mock.patch.object(sampler, 'run', return_value=(0, '/usr/local/bin/python3 other', '')), \
                mock.patch.object(sampler.os, 'kill') as kill:
            sampler.end_supervisor()
        kill.assert_not_called()


class BulkTin(unittest.TestCase):
    """ Bulk tin (TESTING.md): diffserv4 with Background on an alias """
    INFO = {'cake': True, 'diffserv': 2, 'tins': ['Best Effort', 'Bulk', 'Video', 'Voice'],
            'payload_cap': 910e6 * 1500 / 1542, 'bandwidth': 910e6}

    def latest(self, be_mbit, bulk_mbit, drops=0, backlog=0):
        def b(m):
            return int(m * 1e6 / 8 * 10)
        return {'step': 10, 'tins': [[b(be_mbit), 0, 0, 0], [b(bulk_mbit), drops, 0, backlog], [0, 0, 0, 0], [0, 0, 0, 0]]}

    def test_yielding(self):
        st = sampler.bulk_status(self.INFO, self.latest(800, 53, drops=40, backlog=12), {'background': 1})
        self.assertEqual(st['state'], 'yielding')
        self.assertIn('Background is yielding: 53.0 Mbit/s, while 800.0 Mbit/s of other traffic', st['text'])
        self.assertIn('whole line when nothing else needs it', st['text'])
        self.assertIn('about 55.3 Mbit/s (1/16 of the line)', st['floor_text'])
        self.assertIn('all Background devices share this floor together', st['floor_text'])

    def test_free_line(self):
        st = sampler.bulk_status(self.INFO, self.latest(2, 854), {'background': 1})
        self.assertEqual(st['state'], 'free')
        self.assertIn('Background is using the free line: 854.0 Mbit/s', st['text'])

    def test_free_only_when_other_classes_carry_nothing(self):
        # 3 % of the line in Best Effort next to a busy Bulk was "free" before (other < Bulk/10): not nothing
        st = sampler.bulk_status(self.INFO, self.latest(27, 780), {'background': 1})
        self.assertEqual(st['state'], 'sharing')
        # 2 % of the line or less is nothing: the free line
        self.assertEqual(sampler.bulk_status(self.INFO, self.latest(15, 860), {'background': 1})['state'], 'free')

    def test_yielding_near_threshold_without_drop_counters(self):
        # other classes fill the line, Bulk sits near its 1/16 threshold; no drops or backlog in this step
        st = sampler.bulk_status(self.INFO, self.latest(820, 60), {'background': 1})
        self.assertEqual(st['state'], 'yielding')
        self.assertIn('Background is yielding: 60.0 Mbit/s, while 820.0 Mbit/s', st['text'])

    def test_low_bulk_on_a_quiet_line_is_not_yielding(self):
        # Bulk near the threshold only because it wants little, the line half empty: not yielding
        self.assertEqual(sampler.bulk_status(self.INFO, self.latest(300, 50), {'background': 1})['state'], 'sharing')

    def test_idle_and_besteffort(self):
        self.assertEqual(sampler.bulk_status(self.INFO, self.latest(300, 0), {'background': 1})['state'], 'idle')
        be = dict(self.INFO, diffserv=0, tins=['Best Effort'])
        st = sampler.bulk_status(be, {}, {'background': 1})
        self.assertEqual(st['state'], 'besteffort')
        self.assertIn('Priorities have no effect on this pipe: CAKE is set to besteffort', st['text'])
        self.assertIsNone(sampler.bulk_status(be, {}, {}))

    def test_tin_order_matches_this_dn_cake(self):
        from lib import parse_cake_stats  # noqa: F401
        import lib
        src = open(lib.__file__).read()
        self.assertIn("1: ['Best Effort', 'Bulk', 'Voice']", src)
        self.assertIn("2: ['Best Effort', 'Bulk', 'Video', 'Voice']", src)

    def test_latest_tins_carry_drops_and_marks_apart(self):
        before = {'1': {'bytes': 0, 'tinx': [[0, 0, 0, 0], [0, 0, 0, 0]]}}
        now = {'1': {'bytes': 900, 'tinx': [[800, 1, 2, 3], [100, 4, 5, 6]]}}
        d = sampler.pipe_deltas(now, before)['1']
        self.assertEqual(d['delivered'], 900)
        self.assertEqual(d['tins'], [[800, 1, 2, 3], [100, 4, 5, 6]])


class SchedulerTerms(unittest.TestCase):
    """ user feedback 2026-11-22: "WF2Q+ has no tooltip, nor why?" """

    def setUp(self):
        from lib import analysis
        self.a = analysis
        base = os.path.join(SCRIPTS, '..', '..', 'mvc', 'app', 'library', 'OPNsense', 'ShaperInsight')
        with open(os.path.join(base, 'glossary.json')) as f:
            self.glossary = json.load(f)
        with open(os.path.join(SCRIPTS, '..', '..', 'www', 'js', 'shaperinsight', 'common.js')) as f:
            self.common = f.read()
        with open(os.path.join(HERE, '..', 'tools', 'shots', 'terms-check.js')) as f:
            self.check = f.read()

    def test_every_scheduler_word_has_a_glossary_entry(self):
        words = set(self.a.SCHED_WORDS.values()) | set(self.a.KERNEL_WORDS.values())
        for w in words:
            self.assertIn(w, self.a.SCHED_TERM, w)
            tid = self.a.SCHED_TERM[w]
            self.assertIn(tid, self.glossary, w)
            for field in ('say', 'example', 'source_text'):
                self.assertTrue(self.glossary[tid].get(field) or tid in ('cake', 'fq-codel', 'priority-strict'),
                                '%s lacks %s' % (tid, field))
            # the page pass and the rendered-page check know the same words
            self.assertIn("'%s', '%s'" % (w, tid), self.common)
            self.assertIn("'%s': '%s'" % (w, tid), self.check)

    def test_no_link_ends_in_punctuation(self):
        # "https://en.wikipedia.org/wiki/Leaky_bucket:" was a dead link (publication check, 2026-11-22)
        bad = [u for v in self.glossary.values() if isinstance(v, dict) for u in v.get('urls', [])
               if u[-1] in ':,.;']
        self.assertEqual(bad, [])

    def test_sched_words_chip(self):
        self.assertEqual(self.a.sched_words('WF2Q+'), '{{term:wf2q|WF2Q+}}')
        self.assertEqual(self.a.sched_words('something new'), 'something new')

    def test_why_for_a_user_control_lane(self):
        # a hand-made setup: the 10 Mbit/s ICMPv6 control pipe, WF2Q+, one queue, made by hand
        why = self.a.sched_why('WF2Q+', False, False, 1, 10000000)
        self.assertIn('Chosen in your pipe settings', why)
        self.assertIn('{{term:wf2q|WF2Q+}}', why)
        self.assertIn('like {{term:fifo-tail-drop|FIFO}}', why)
        self.assertIn('small lane such as an ICMPv6 control pipe', why)
        big = self.a.sched_why('WF2Q+', False, False, 1, 840000000)
        self.assertIn('{{term:fq-codel}} or {{term:cake}} keep the delay low', big)
        self.assertIn('between its 3 queues', self.a.sched_why('QFQ', False, False, 3, 840000000))

    def test_why_for_our_pipes(self):
        self.assertIn('Set by Shaper Insight', self.a.sched_why('WF2Q+', True, False, 1, 1000000))
        self.assertIn('{{term:cake}}', self.a.sched_why('CAKE', True, True, 1, 840000000))

    def test_pipe_rows_carry_words_and_why(self):
        from lib import analysis
        snap = {'config': {'pipes': [{'uuid': 'p', 'number': '10002', 'enabled': '1', 'bandwidth': '10',
                                      'bandwidthMetric': 'Mbit', 'scheduler': '', 'description': 'ctl'}],
                           'queues': [{'uuid': 'q', 'pipe': 'p', 'enabled': '1', 'number': '1', 'weight': '100'}],
                           'rules': [], 'cake': [], 'cake_general': {}, 'interfaces': {}},
                'kernel': {'dnctl': {'pipes': {10002: {'bandwidth': 10000000, 'type': 'WF2Q+', 'params': '',
                                                       'children': [], 'buckets': 0, 'active': 0, 'mask': False}},
                                     'queues': {}},
                           'ipfw': {'rules': {}, 'other': []}, 'sysctl': {},
                           'kld': {'modules': ['dummynet.ko'], 'dummynet': True, 'dn_cake': False,
                                   'dummynet_st': False, 'ipfw': True},
                           'cake_stats': {}, 'cake_stats_available': False, 'raw': {}},
                'oscake': {}, 'versions': {}}
        out = analysis.analyse(snap)
        p = out['pipes'][0]
        self.assertEqual(p['intended_words'], '{{term:wf2q|WF2Q+}}')
        self.assertEqual(p['loaded_words'], '{{term:wf2q|WF2Q+}}')
        self.assertIn('Chosen in your pipe settings', p['sched_why'])


if __name__ == '__main__':
    unittest.main()
