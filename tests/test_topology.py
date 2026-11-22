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

"""Topology test 2026-11-22 (TESTING.md): one test per finding that was fixed.

F1 a gateway to a downstream router is not an internet sign; WireGuard/OSPF hub tunnels with
gateways are never links. F2 per-link speeds. F3 the user's tick wins. F4 a PD-tracking LAN is
local. F5 runtime (dynamic) gateways. F6 disabled interfaces and interface groups are hidden.
F7 line type presets, derivation only as the suggestion. F8 legacy VLAN names get +4. F9 "Keep
mine" shows what Apply does. F10 the shared-forwarding warning needs a gateway rule. F11 no
os-cake wording without os-cake. F13 (UI) and F14 (wording) are checked here where testable.
"""
import copy
import os
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'net', 'shaper-insight', 'src', 'opnsense',
                                'scripts', 'shaperinsight'))
from lib.detect import detect  # noqa: E402
from lib.plan import build, link_choices, plan_digest  # noqa: E402

# a multi-WAN example: five VLAN WANs on one port, a static FIBER with an upstream gateway, a transit
# network to a downstream router, two WireGuard hub tunnels with gateways, a PD-tracking LAN, a
# disabled spare "wan", the WireGuard interface group, and an old-style VLAN name
CONFIG = """<opnsense>
  <interfaces>
    <wan><if>igb3</if><descr>SPARE</descr><ipaddr>dhcp</ipaddr><gateway>SPARE_GW</gateway></wan>
    <lan><enable>1</enable><if>igb1</if><descr>LAN</descr><ipaddr>192.168.1.1</ipaddr>
      <ipaddrv6>track6</ipaddrv6><track6-interface>opt5</track6-interface></lan>
    <opt1><enable>1</enable><if>igb2</if><descr>FIBER</descr><ipaddr>198.51.100.2</ipaddr></opt1>
    <opt2><enable>1</enable><if>igb4</if><descr>TRANSIT</descr><ipaddr>10.199.0.1</ipaddr></opt2>
    <opt3><enable>1</enable><if>wg1</if><descr>TUN1</descr></opt3>
    <opt4><enable>1</enable><if>wg2</if><descr>TUN2</descr></opt4>
    <opt5><enable>1</enable><if>vlan0.101</if><descr>WAN1</descr><ipaddr>dhcp</ipaddr><ipaddrv6>dhcp6</ipaddrv6></opt5>
    <opt6><enable>1</enable><if>igb5_vlan101</if><descr>LEGACY</descr><ipaddr>dhcp</ipaddr></opt6>
    <wireguard><internal_dynamic>1</internal_dynamic><descr>WireGuard (Group)</descr><if>wireguard</if>
      <virtual>1</virtual><enable>1</enable><type>group</type></wireguard>
  </interfaces>
  <vlans>
    <vlan><if>igb0</if><tag>101</tag><vlanif>vlan0.101</vlanif></vlan>
    <vlan><if>igb5</if><tag>101</tag><vlanif>igb5_vlan101</vlanif></vlan>
  </vlans>
  <OPNsense><Gateways>
    <gateway_item><name>FIBER_GW</name><interface>opt1</interface><defaultgw>1</defaultgw></gateway_item>
    <gateway_item><name>DOWNSTREAM_GW</name><interface>opt2</interface><defaultgw>0</defaultgw></gateway_item>
    <gateway_item><name>TUN1_GW</name><interface>opt3</interface><defaultgw>1</defaultgw></gateway_item>
    <gateway_item><name>TUN2_GW</name><interface>opt4</interface></gateway_item>
  </Gateways>
  <Firewall><Filter><rules>
    <rule><enabled>1</enabled><description>LAN out FIBER</description><gateway>OUT</gateway></rule>
    <rule><enabled>0</enabled><description>off</description><gateway>FIBER_GW</gateway></rule>
  </rules></Filter></Firewall></OPNsense>
  <gateways><gateway_group><name>OUT</name><item>FIBER_GW|1|address,WAN1_DHCP|2|address</item></gateway_group></gateways>
</opnsense>"""

IFCONFIG = """igb1: flags=1008843<UP,BROADCAST,RUNNING> metric 0 mtu 1500
\tinet 192.168.1.1 netmask 0xffffff00
\tinet6 2a01:4f8:0:aa01::1 prefixlen 64
igb2: flags=1008843<UP,BROADCAST,RUNNING> metric 0 mtu 1500
\tinet 198.51.100.2 netmask 0xffffff00
igb4: flags=1008843<UP,BROADCAST,RUNNING> metric 0 mtu 1500
\tinet 10.199.0.1 netmask 0xffffff00
vlan0.101: flags=1008843<UP,BROADCAST,RUNNING> metric 0 mtu 1500
\tinet 100.70.0.2 netmask 0xffffff00
\tvlan: 101 vlanproto: 802.1q vlanpcp: 0 parent interface: igb0
"""
NETSTAT = """Internet:
Destination        Gateway            Flags     Netif Expire
default            198.51.100.1       UGS        igb2
"""
# core's runtime list (gateways.php): the dynamic DHCP gateways exist only here
RUNTIME = [
    {'name': 'FIBER_GW', 'interface': 'opt1', 'upstream': True, 'dynamic': False, 'disabled': False},
    {'name': 'DOWNSTREAM_GW', 'interface': 'opt2', 'upstream': False, 'dynamic': False, 'disabled': False},
    {'name': 'TUN1_GW', 'interface': 'opt3', 'upstream': True, 'dynamic': False, 'disabled': False},
    {'name': 'TUN2_GW', 'interface': 'opt4', 'upstream': False, 'dynamic': False, 'disabled': False},
    {'name': 'WAN1_DHCP', 'interface': 'opt5', 'upstream': False, 'dynamic': True, 'disabled': False},
    {'name': 'WAN1_DHCP6', 'interface': 'opt5', 'upstream': False, 'dynamic': True, 'disabled': False},
    {'name': 'LEGACY_DHCP', 'interface': 'opt6', 'upstream': False, 'dynamic': True, 'disabled': False},
]
SNAP = {'config': {'pipes': [], 'queues': [], 'rules': []}, 'oscake': {},
        'kernel': {'dnctl': {'pipes': {}, 'queues': {}}, 'sysctl': {}}}


def run(selection=None, gateways=RUNTIME):
    d = detect(ET.fromstring(CONFIG), IFCONFIG, NETSTAT, gateways, selection)
    return d, {i['key']: i for i in d['interfaces']}


def texts(row):
    return [s['text'] for s in row['signals'] if s['true']]


class DetectionTest(unittest.TestCase):
    def test_f1_downstream_gateway_is_only_maybe(self):
        d, by = run()
        self.assertEqual(by['opt1']['role'], 'wan')                   # upstream gateway + default route
        self.assertEqual(by['opt2']['role'], 'maybe')                 # plain gateway: maybe a local router
        self.assertFalse(by['opt2']['selected'])
        self.assertNotIn('opt2', [m for lk in d['links'] for m in lk['members']])
        self.assertTrue(any('not marked "Upstream gateway"' in t for t in texts(by['opt2'])))

    def test_f1_wireguard_hub_tunnels_never_links(self):
        # hub tunnels have gateways (one even marked upstream): still not shaped
        d, by = run(selection={'opt3': True, 'opt4': True})
        self.assertEqual((by['opt3']['role'], by['opt4']['role']), ('vpn', 'vpn'))
        self.assertFalse(by['opt3']['selected'] or by['opt4']['selected'])
        members = [m for lk in d['links'] for m in lk['members']]
        self.assertNotIn('opt3', members)
        self.assertNotIn('opt4', members)

    def test_f5_dynamic_gateways_from_runtime(self):
        _, by = run()
        self.assertTrue(any(t.startswith('Has upstream gateway WAN1_DHCP, WAN1_DHCP6') for t in texts(by['opt5'])))
        self.assertNotIn('No gateway', [s['text'] for s in by['opt5']['signals']])
        # without the runtime list the dynamic gateway is unknown, but DHCP still makes it a WAN
        _, cfg = run(gateways=None)
        self.assertEqual(cfg['opt5']['role'], 'wan')

    def test_f4_pd_tracking_lan_is_local(self):
        _, by = run()
        self.assertEqual(by['lan']['role'], 'lan')       # the global /64 came from the delegated prefix
        self.assertTrue(any('delegated through WAN1' in t for t in texts(by['lan'])))

    def test_f6_disabled_and_groups_hidden(self):
        _, by = run()
        self.assertNotIn('wan', by)          # no <enable>: disabled, even though it is "wan" with a gateway
        self.assertNotIn('wireguard', by)    # the interface group, not an interface

    def test_f3_ticks_win_and_are_reported(self):
        d, by = run(selection={'opt2': True, 'opt5': False})
        self.assertTrue(by['opt2']['selected'] and by['opt2']['chosen'])
        self.assertFalse(by['opt5']['selected'])
        members = {m for lk in d['links'] for m in lk['members']}
        self.assertIn('opt2', members)
        self.assertNotIn('opt5', members)

    def test_f10_gateway_rules_resolved_through_groups(self):
        d, _ = run()
        self.assertEqual(d['gateway_rules'], [{'descr': 'LAN out FIBER', 'gateway': 'OUT',
                                               'interfaces': ['opt1', 'opt5']}])


class PlanTopologyTest(unittest.TestCase):
    def setUp(self):
        self.det, _ = run()
        self.by_root = {lk['root']: lk for lk in self.det['links']}

    def link(self, plan, root):
        return next(lk for lk in plan['links'] if lk['root'] == root)

    def cake(self, plan, root):
        return [o for o in self.link(plan, root)['objects'] if o['kind'] == 'cake']

    def test_f2_each_link_its_own_speeds(self):
        plan = build(self.det, SNAP, rates={'igb2': {'down': 1000, 'up': 1000}, 'igb0': {'down': 50, 'up': 10}})
        bulk = {lk['root']: [o['model']['bandwidth'] for o in lk['objects']
                             if o['kind'] == 'pipe' and ' control ' not in o['description']] for lk in plan['links']}
        self.assertEqual(bulk['igb2'], ['940', '940'])
        self.assertEqual(bulk['igb0'], ['46500', '9025'])   # 10 up: 9500 minus a 475 kbit lane (5 % cap)
        self.assertFalse(plan['ready'])                 # LEGACY (igb5) has no speeds yet
        self.assertTrue(any(p['level'] == 'bad' and '(opt6): both speeds' in p['text']
                            for p in plan['preflight']))
        ready = build(self.det, SNAP, rates={'igb2': {'down': 1000, 'up': 1000}, 'igb0': {'down': 50, 'up': 10},
                                             'igb5': {'down': 100, 'up': 20}})
        self.assertTrue(ready['ready'])
        self.assertNotEqual(plan_digest(ready), plan_digest(build(self.det, SNAP, 100, 20)))

    def test_f8_legacy_vlan_name_gets_the_tag(self):
        plan = build(self.det, SNAP, 100, 20)
        self.assertEqual({c['model']['overhead'] for c in self.cake(plan, 'igb5')}, {'42'})   # vtnet1_vlan101 style
        self.assertEqual({c['model']['overhead'] for c in self.cake(plan, 'igb0')}, {'42'})
        self.assertEqual({c['model']['overhead'] for c in self.cake(plan, 'igb2')}, {'38'})
        # by name alone too, when config.xml has no VLAN entry for it
        det = copy.deepcopy(self.det)
        for i in det['interfaces']:
            i['vlan'] = False
        self.assertEqual({c['model']['overhead'] for c in self.cake(build(det, SNAP, 100, 20), 'igb5')}, {'42'})

    def test_f7_line_type_presets(self):
        plan = build(self.det, SNAP, 100, 20, rates={'igb2': {'down': 100, 'up': 20, 'line': 'docsis'},
                                                     'igb0': {'down': 100, 'up': 20, 'line': 'pppoe-ptm'}})
        docsis = self.cake(plan, 'igb2')[0]['model']
        self.assertEqual((docsis['link'], docsis['overhead'], docsis['mpu']), ('docsis', '18', '64'))
        ptm = self.cake(plan, 'igb0')[0]['model']
        self.assertEqual((ptm['link'], ptm['overhead'], ptm['mpu']), ('pppoe-ptm', '34', ''))   # 30 + VLAN tag
        self.assertEqual(self.link(plan, 'igb2')['rates']['overhead_suggested'], 38)
        self.assertIn('which you picked', self.cake(plan, 'igb2')[0]['why'])
        auto = self.cake(build(self.det, SNAP, 100, 20), 'igb2')[0]
        self.assertIn('suggestion from the interface', auto['why'])
        self.assertIn('overhead-preset', auto['claims'])

    def test_f9_keep_mine_plans_nothing_for_that_link(self):
        snap = copy.deepcopy(SNAP)
        snap['config']['rules'].append({'uuid': 'mine', 'enabled': '1', 'interface': 'opt1', 'direction': 'in',
                                        'sequence': '10', 'description': 'hand-made'})
        det = copy.deepcopy(self.det)
        det['links'] = [lk for lk in det['links'] if lk['root'] == 'igb2']
        keep = build(det, snap, 100, 20, mode='skip')
        self.assertTrue(keep['links'][0]['skipped'])
        self.assertEqual(keep['links'][0]['objects'], [])
        self.assertFalse(keep['totals'].get('add'))
        self.assertEqual(keep['nothing'], 'nothing to apply while you keep your own rules')
        self.assertIn('skipped', keep['summary'][0]['text'])
        use = build(det, snap, 100, 20, mode='disable')
        self.assertFalse(use['links'][0]['skipped'])
        self.assertEqual(use['totals']['add'], 14)
        self.assertNotEqual(plan_digest(keep), plan_digest(use))

    def test_f10_shared_forwarding_needs_a_gateway_rule(self):
        snap = copy.deepcopy(SNAP)
        snap['kernel']['sysctl'] = {'net.pf.share_forward': '0'}
        warned = [p for p in build(self.det, snap, 100, 20)['preflight'] if 'share_forward' in p['text']]
        self.assertEqual(len(warned), 1)
        self.assertIn('"LAN out FIBER" via OUT', warned[0]['text'])
        det = copy.deepcopy(self.det)
        det['gateway_rules'] = []
        self.assertFalse([p for p in build(det, snap, 100, 20)['preflight'] if 'share_forward' in p['text']])

    def test_f11_f14_wording(self):
        snap = copy.deepcopy(SNAP)
        snap['oscake'] = None
        plan = build(self.det, snap, 100, 20)
        whys = [o['why'] for lk in plan['links'] for o in lk['objects'] if o['kind'] == 'queue']
        self.assertFalse([w for w in whys if 'os-cake' in w or 'CAKE' in w])
        cake = self.cake(build(self.det, SNAP, 100, 20), 'igb2')[0]
        self.assertNotIn('every device', cake['why'])


class ChoicesTest(unittest.TestCase):
    def test_saved_choices_read_back(self):
        import tempfile
        xml = """<opnsense><OPNsense><ShaperInsight><links>
          <choice uuid="a"><interface>opt2</interface><include>yes</include></choice>
          <choice uuid="b"><interface>opt5</interface><include>no</include></choice>
          <choice uuid="c"><interface>opt7</interface><include></include></choice>
          <link uuid="d"><port>igb0</port><download>50</download><upload>10</upload><line>docsis</line></link>
        </links></ShaperInsight></OPNsense></opnsense>"""
        with tempfile.NamedTemporaryFile('w', suffix='.xml', delete=False) as f:
            f.write(xml)
        try:
            c = link_choices(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(c['select'], {'opt2': True, 'opt5': False})
        self.assertEqual(c['rates'], {'igb0': {'down': 50, 'up': 10, 'line': 'docsis'}})
        self.assertEqual(link_choices('/nonexistent'), {'select': {}, 'rates': {}})


if __name__ == '__main__':
    unittest.main()
