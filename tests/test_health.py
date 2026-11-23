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


# --- S3 with interface counters (2026.11.23a): a multi-WAN setup -------------------
# five WANs on VLANs, all upload rules into queue 10001, the default route on vlan0.101; replies to
# connections that came in on 102-105 leave there by pf's reply-to after ipfw saw them on 101
# (reproduced on the lab VM, TESTING.md); IPv6 only on vlan0.101
WANS = [('wan', 'WAN', 'vlan0.101'), ('opt1', 'WAN2', 'vlan0.102'), ('opt2', 'WAN3', 'vlan0.103'),
        ('opt3', 'WAN4', 'vlan0.104'), ('opt4', 'WAN5', 'vlan0.105')]
UP_PKTS = {'wan': 2000000000, 'opt1': 0, 'opt2': 410, 'opt3': 0, 'opt4': 0}
SENT = {'vlan0.101': 2.5e12, 'vlan0.102': 6.4e9, 'vlan0.103': 3.1e12, 'vlan0.104': 18e9,
        'vlan0.105': 52e6}


def multiwan_snap():
    rules, krules = [], {}
    for n, (key, descr, dev) in enumerate(WANS):
        up = rule('up-' + key, 40 + 10 * n, key, 'out', 'qup', description='%sOutLAN ShapeUP' % descr)
        dn = rule('dn-' + key, 140 + 10 * n, key, 'in', 'qdn', description='%sInLAN ShapeDOWN' % descr)
        rules += [up, dn]
        krules[up['uuid']] = {'num': 60011 + n, 'pkts': UP_PKTS[key], 'bytes': 0,
                              'body': 'queue 10001 ip from any to any out via %s' % dev}
        krules[dn['uuid']] = {'num': 60016 + n, 'pkts': 1000 + n, 'bytes': 0,
                              'body': 'queue 10000 ip from any to any in via %s' % dev}
        if key != 'wan':
            c6 = rule('c6-' + key, 10 + n, key, 'out', 'qctl', proto='ipv6-icmp', description='%s ICMPv6' % descr)
            rules.append(c6)
            krules[c6['uuid']] = {'num': 60001 + n, 'pkts': 0, 'bytes': 0,
                                  'body': 'queue 10002 ipv6-icmp from any to any out via %s' % dev}
    return {
        'config': {
            'pipes': [{'uuid': 'pdn', 'number': '10000', 'enabled': '1', 'scheduler': 'fq_codel'},
                      {'uuid': 'pup', 'number': '10001', 'enabled': '1', 'scheduler': 'fq_codel'},
                      {'uuid': 'pctl', 'number': '10002', 'enabled': '1', 'scheduler': ''}],
            'queues': [{'uuid': 'qdn', 'number': '10000', 'enabled': '1', 'pipe': 'pdn'},
                       {'uuid': 'qup', 'number': '10001', 'enabled': '1', 'pipe': 'pup'},
                       {'uuid': 'qctl', 'number': '10002', 'enabled': '1', 'pipe': 'pctl'}],
            'rules': rules},
        'kernel': {'dnctl': {'pipes': {'10000': {'type': 'CAKE'}, '10001': {'type': 'CAKE'},
                                       '10002': {'type': 'WF2Q+'}},
                             'queues': {'10000': {}, '10001': {}, '10002': {}}},
                   'ipfw': {'rules': krules}},
    }


def multiwan_det(gateways=True):
    # every WAN has a (DHCP) gateway, so pf adds reply-to to the rules on it
    return {'interfaces': [{'key': k, 'descr': d, 'device': dev, 'ipv6_off': dev != 'vlan0.101',
                            'gateway': gateways} for k, d, dev in WANS],
            'routes': {'inet': ['vlan0.101'], 'inet6': ['vlan0.101']}}


def netstat(sent):
    import json
    rows = []
    for dev, b in sent.items():
        rows.append({'name': dev, 'network': '<Link#%d>' % (len(rows) + 1), 'received-bytes': int(b),
                     'sent-bytes': int(b)})
        rows.append({'name': dev, 'network': '10.0.%d.0/24' % len(rows), 'received-bytes': 1, 'sent-bytes': 1})
    return json.dumps({'statistics': {'interface': rows}})


class S3InterfaceTest(unittest.TestCase):
    """ the four cases of S3 (2026.11.23a): redundant sibling, IPv6 off, unshaped, idle """

    def s3(self, snap=None, det=None, now_sent=None, base_sent=None, reloaded=None):
        from lib.health import interface_view
        iv = interface_view(det or multiwan_det(), netstat(now_sent or SENT),
                            netstat(base_sent) if base_sent else None, reloaded, boot=1763700000.0)
        c = {x['id']: x for x in run_checks(snap or multiwan_snap(), [k for k, _, _ in WANS], reloaded=reloaded,
                                            now=(reloaded or 0) + 86400, ifview=iv)}
        return c['S3']

    def item(self, s3, needle):
        hits = [i for i in s3['items'] if needle in i['text']]
        self.assertEqual(len(hits), 1, [i['text'] for i in s3['items']])
        return hits[0]

    def test_1_policy_routed_upload_is_shaped_by_the_sibling(self):
        s3 = self.s3()
        it = self.item(s3, 'WAN2OutLAN')
        self.assertEqual(it['level'], 'info')
        self.assertIn('redundant', it['text'])
        self.assertIn('WAN2 (vlan0.102), 6.4 GB sent since boot', it['text'])
        self.assertIn('rule "WANOutLAN ShapeUP" (sequence 40, WAN out)', it['text'])
        self.assertIn('same queue 10001', it['text'])
        self.assertIn('Nothing is unshaped', it['text'])
        self.assertIn('the routing table chose, WAN (vlan0.101), which holds the default route.', it['why'])
        self.assertIn('reply-to', it['why'])
        self.assertIn('ipfw rule 60012', it['example'])
        # 104 and 105 too; 103 matched 410 packets and is OK
        for d in ('WAN4OutLAN', 'WAN5OutLAN'):
            self.assertEqual(self.item(s3, d)['level'], 'info')
        self.assertEqual(self.item(s3, 'WAN3OutLAN')['level'], 'ok')
        # nothing in this picture is a warning any more
        self.assertEqual(s3['level'], 'info')
        self.assertFalse([i for i in s3['items'] if i['level'] in ('warn', 'bad')])

    def test_1_without_a_gateway_says_likely(self):
        it = self.item(self.s3(det=multiwan_det(gateways=False)), 'WAN2OutLAN')
        self.assertEqual(it['level'], 'info')
        self.assertIn('has no gateway, so the likely cause', it['why'])

    def test_2_ipv6_rule_on_ipv6_off_interface(self):
        s3 = self.s3()
        for d in ('WAN2', 'WAN3', 'WAN4', 'WAN5'):
            it = self.item(s3, '"%s ICMPv6"' % d)
            self.assertEqual(it['level'], 'info')
            self.assertTrue(it['text'].startswith('Not applicable'))
            self.assertIn('IPv6 is off on %s' % d, it['text'])

    def test_2_ipv6_off_wins_over_the_reload_grace(self):
        # right after a reload every 0 is "wait for traffic", but an IPv6 rule on an IPv6-off link never will
        from lib.health import interface_view
        iv = interface_view(multiwan_det(), netstat(SENT))
        c = {x['id']: x for x in run_checks(multiwan_snap(), ['wan'], reloaded=1000.0, now=1060.0, ifview=iv)}['S3']
        self.assertTrue(self.item(c, '"WAN2 ICMPv6"')['text'].startswith('Not applicable'))
        self.assertIn('wait for traffic', self.item(c, 'WAN2OutLAN')['text'])

    def test_2_ipv6_rule_with_ipv6_on_is_judged_by_traffic(self):
        det = multiwan_det()
        for i in det['interfaces']:
            i['ipv6_off'] = False
        it = self.item(self.s3(det=det), '"WAN2 ICMPv6"')
        self.assertNotIn('Not applicable', it['text'])

    def test_3_unshaped_when_no_sibling_matched(self):
        snap = multiwan_snap()
        for k in snap['kernel']['ipfw']['rules'].values():
            if 'queue 10001' in k['body']:
                k['pkts'] = 0
        it = self.item(self.s3(snap=snap), 'WAN4OutLAN')
        self.assertEqual(it['level'], 'warn')
        self.assertIn('WAN4 (vlan0.104) sent 18.0 GB since boot', it['text'])
        self.assertIn('left unshaped', it['text'])
        self.assertIn('pipe 10001', it['text'])

    def test_3_sibling_into_another_pipe_does_not_count(self):
        snap = multiwan_snap()
        # the 101 upload rule now feeds the download queue (another pipe): no sibling for 102
        for r in snap['config']['rules']:
            if r['uuid'] in ('up-wan', 'up-opt2'):
                r['target'] = 'qdn'
        self.assertEqual(self.item(self.s3(snap=snap), 'WAN2OutLAN')['level'], 'warn')

    def test_narrow_rule_with_traffic_stays_a_plain_warning(self):
        # a tcp-only rule cannot be judged by interface bytes: no "redundant", no "unshaped"
        snap = multiwan_snap()
        r = rule('narrow', 45, 'opt1', 'out', 'qup', proto='tcp', source='192.168.1.11', description='lan2 bulk')
        snap['config']['rules'].append(r)
        snap['kernel']['ipfw']['rules']['narrow'] = {
            'num': 60030, 'pkts': 0, 'bytes': 0, 'body': 'queue 10001 tcp from 192.168.1.11 to any out via vlan0.102'}
        it = self.item(self.s3(snap=snap), '"lan2 bulk"')
        self.assertEqual(it['level'], 'warn')
        self.assertIn('(tcp from 192.168.1.11 to any)', it['why'])
        self.assertNotIn('redundant', it['text'])

    def test_sibling_on_the_same_interface_is_not_policy_routing(self):
        # lab 2026-11-23: "lan2 bulk upload" and "WAN upload" both on vtnet1; only another link counts
        snap = multiwan_snap()
        snap['kernel']['ipfw']['rules']['up-opt2']['pkts'] = 0
        twin = rule('twin', 41, 'opt1', 'out', 'qup', description='WAN2 twin')
        snap['config']['rules'].append(twin)
        snap['kernel']['ipfw']['rules']['twin'] = {'num': 60031, 'pkts': 99, 'bytes': 0,
                                                   'body': 'queue 10001 ip from any to any out via vlan0.102'}
        it = self.item(self.s3(snap=snap), 'WAN2OutLAN')
        self.assertIn('shaped by rule "WANOutLAN ShapeUP"', it['text'])     # not by its twin on 102

    def test_4_idle_interface(self):
        sent = dict(SENT, **{'vlan0.105': 2000})
        it = self.item(self.s3(now_sent=sent), 'WAN5OutLAN')
        self.assertEqual(it['level'], 'info')
        self.assertTrue(it['text'].startswith('No traffic this way since boot'))
        self.assertIn('2.0 kB', it['text'])

    def test_deltas_since_the_reload(self):
        # counters stored at the reload: 105 sent 52 MB before it and 0 after -> idle since the reload
        base = dict(SENT)
        s3 = self.s3(base_sent=base, now_sent=dict(SENT, **{'vlan0.102': SENT['vlan0.102'] + 2e9}),
                     reloaded=1763800000.0)
        it = self.item(s3, 'WAN5OutLAN')
        self.assertTrue(it['text'].startswith('No traffic this way since the last rule reload'))
        it = self.item(s3, 'WAN2OutLAN')
        self.assertIn('2.0 GB sent since the last rule reload', it['text'])

    def test_counter_went_down_falls_back_to_boot(self):
        base = dict(SENT, **{'vlan0.102': SENT['vlan0.102'] * 2})     # interface re-created
        it = self.item(self.s3(base_sent=base, reloaded=1763800000.0), 'WAN2OutLAN')
        self.assertIn('since boot', it['text'])

    def test_no_counters_keeps_the_old_warning(self):
        from lib.health import interface_view
        iv = interface_view(multiwan_det(), '')
        c = {x['id']: x for x in run_checks(multiwan_snap(), ['wan'], ifview=iv)}['S3']
        it = self.item(c, 'WAN2OutLAN')
        self.assertEqual(it['level'], 'warn')
        self.assertIn('could not be read', it['why'])

    def test_rule_names_are_what_the_gui_shows(self):
        it = self.item(self.s3(), 'WAN2OutLAN')
        # "Sequence" in Firewall: Shaper: Rules, the interface's description, never "opt1"
        self.assertIn('Rule "WAN2OutLAN ShapeUP" (sequence 50, WAN2 out)', it['text'])
        self.assertNotIn('opt1', it['text'])


class ParseNetstatTest(unittest.TestCase):
    def test_vlan_frames_come_off_the_parent(self):
        # lab 2026-11-23: 3,000 echo replies on vlan0.102 -> +4,326,000 there, +4,337,996 on vtnet1
        # (about 4 bytes of 802.1Q tag a frame); here vtnet1 also sent 500 bytes of its own
        import json
        from lib.health import interface_view
        rows = [{'name': 'vtnet1', 'network': '<Link#2>', 'received-bytes': 100, 'received-packets': 1,
                 'sent-bytes': 4326000 + 4 * 3000 + 500, 'sent-packets': 3005},
                {'name': 'vlan0.102', 'network': '<Link#8>', 'received-bytes': 0, 'received-packets': 0,
                 'sent-bytes': 4326000, 'sent-packets': 3000}]
        det = {'interfaces': [{'key': 'wan', 'device': 'vtnet1'},
                              {'key': 'opt2', 'device': 'vlan0.102', 'vlan_parent': 'vtnet1'}]}
        iv = interface_view(det, json.dumps({'statistics': {'interface': rows}}))
        self.assertEqual(iv['bytes']['vtnet1']['out'], 500)
        self.assertEqual(iv['bytes']['vlan0.102']['out'], 4326000)

    def test_parent_and_vlan_cover_the_same_span(self):
        # the reload copy lacks the VLAN (created after it): parent and VLAN both count from boot
        import json
        from lib.health import interface_view
        now = [{'name': 'vtnet1', 'network': '<Link#2>', 'received-bytes': 900, 'sent-bytes': 900},
               {'name': 'vlan0.102', 'network': '<Link#8>', 'received-bytes': 400, 'sent-bytes': 400}]
        base = [{'name': 'vtnet1', 'network': '<Link#2>', 'received-bytes': 100, 'sent-bytes': 100}]
        det = {'interfaces': [{'key': 'wan', 'device': 'vtnet1'},
                              {'key': 'opt2', 'device': 'vlan0.102', 'vlan_parent': 'vtnet1'}]}
        iv = interface_view(det, json.dumps({'statistics': {'interface': now}}),
                            json.dumps({'statistics': {'interface': base}}), reloaded=5.0, boot=1.0)
        self.assertEqual(iv['bytes']['vtnet1'], {'in': 500, 'out': 500, 'since': 'boot', 'ts': 1.0})

    def test_link_rows_only_and_garbage(self):
        from lib.health import parse_netstat
        self.assertEqual(parse_netstat(netstat({'em0': 5})), {'em0': {'in': 5, 'out': 5, 'in_pkts': 0,
                                                                     'out_pkts': 0}})
        self.assertEqual(parse_netstat('not json'), {})
        self.assertEqual(parse_netstat('{"statistics": {"interface": "x"}}'), {})


if __name__ == '__main__':
    unittest.main()
