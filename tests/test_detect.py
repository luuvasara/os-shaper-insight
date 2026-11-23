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

"""WAN detection: signals, VLAN/PPPoE walk to the physical port, one link group per port."""
import os
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'net', 'shaper-insight', 'src', 'opnsense',
                                'scripts', 'shaperinsight'))
from lib.detect import detect  # noqa: E402

CONFIG = """<opnsense>
  <interfaces>
    <wan><if>vlan0.101</if><descr>WAN</descr><enable>1</enable><ipaddr>dhcp</ipaddr></wan>
    <opt1><if>vlan0.102</if><descr>WAN2</descr><enable>1</enable><ipaddr>dhcp</ipaddr></opt1>
    <opt2><if>pppoe0</if><descr>Fibre</descr><enable>1</enable><ipaddr>pppoe</ipaddr></opt2>
    <lan><if>igb1</if><descr>LAN</descr><enable>1</enable><ipaddr>192.168.1.1</ipaddr></lan>
    <opt3><if>wg0</if><descr>VPN</descr><enable>1</enable><gateway>WG_GW</gateway></opt3>
    <opt4><if>igb3</if><descr>Spare</descr></opt4>
  </interfaces>
  <vlans>
    <vlan><if>igb0</if><tag>101</tag><vlanif>vlan0.101</vlanif></vlan>
    <vlan><if>igb0</if><tag>102</tag><vlanif>vlan0.102</vlanif></vlan>
  </vlans>
  <ppps><ppp><if>pppoe0</if><ports>igb2</ports><type>pppoe</type></ppp></ppps>
  <OPNsense><Gateways>
    <gateway_item><name>WAN_DHCP</name><interface>wan</interface></gateway_item>
    <gateway_item><name>OLD_GW</name><interface>opt1</interface><disabled>1</disabled></gateway_item>
  </Gateways></OPNsense>
</opnsense>"""

IFCONFIG = """vlan0.101: flags=1008843<UP,BROADCAST,RUNNING> metric 0 mtu 1500
\tinet 203.0.113.7 netmask 0xffffff00 broadcast 203.0.113.255
\tgroups: vlan
\tvlan: 101 vlanproto: 802.1q vlanpcp: 0 parent interface: igb0
igb1: flags=1008843<UP,BROADCAST,RUNNING> metric 0 mtu 1500
\tinet 192.168.1.1 netmask 0xffffff00
pppoe0: flags=88d1<UP,POINTOPOINT,RUNNING> metric 0 mtu 1492
\tinet 100.64.9.9 --> 100.64.0.1 netmask 0xffffffff
"""
NETSTAT = """Routing tables

Internet:
Destination        Gateway            Flags     Netif Expire
default            203.0.113.1        UGS  vlan0.101
"""


class DetectTest(unittest.TestCase):
    def setUp(self):
        self.d = detect(ET.fromstring(CONFIG), IFCONFIG, NETSTAT)
        self.by = {i['key']: i for i in self.d['interfaces']}

    def test_roles(self):
        self.assertEqual(self.by['wan']['role'], 'wan')
        self.assertEqual(self.by['opt1']['role'], 'wan')       # DHCP client, disabled gateway ignored
        self.assertEqual(self.by['opt2']['role'], 'wan')       # PPPoE client
        self.assertEqual(self.by['lan']['role'], 'lan')
        self.assertEqual(self.by['opt3']['role'], 'vpn')       # a tunnel with a gateway is not a WAN
        self.assertNotIn('opt4', self.by)                      # not enabled

    def test_reasons(self):
        texts = ' '.join(s['text'] for s in self.by['wan']['signals'] if s['true'])
        self.assertIn('WAN_DHCP', texts)
        self.assertIn('default route', texts)
        self.assertIn('public address', texts)
        self.assertNotIn('OLD_GW', ' '.join(s['text'] for s in self.by['opt1']['signals']))
        self.assertIn('Carrier-grade NAT', ' '.join(s['text'] for s in self.by['opt2']['signals']))

    def test_one_shaper_per_physical_port(self):
        links = {lk['root']: sorted(lk['members']) for lk in self.d['links']}
        self.assertEqual(links, {'igb0': ['opt1', 'wan'], 'igb2': ['opt2']})
        self.assertEqual(self.by['opt2']['path'], ['pppoe0', 'igb2'])

    def test_vlan_loop_terminates(self):
        cfg = CONFIG.replace('<if>igb0</if><tag>101</tag>', '<if>vlan0.102</if><tag>101</tag>').replace(
            '<if>igb0</if><tag>102</tag>', '<if>vlan0.101</if><tag>102</tag>')
        d = detect(ET.fromstring(cfg), '', '')
        self.assertTrue(any(i['note'] == 'loop' for i in d['interfaces']))



class Ipv6StateTest(unittest.TestCase):
    """ health S3 (2026.11.23a): IPv6 off = nd6 IFDISABLED (as on a VLAN WAN without IPv6) or no inet6 at all """

    def test_ifdisabled_and_no_inet6(self):
        from lib.detect import parse_ifconfig
        ifc = parse_ifconfig(
            "vlan0.102: flags=1008843<UP,BROADCAST,RUNNING> metric 0 mtu 1500\n"
            "\tinet 10.97.0.2 netmask 0xffffff00 broadcast 10.97.0.255\n"
            "\tnd6 options=29<PERFORMNUD,IFDISABLED,AUTO_LINKLOCAL>\n"
            "vlan0.101: flags=1008843<UP,BROADCAST,RUNNING> metric 0 mtu 1500\n"
            "\tinet6 fe80::1%vlan0.101 prefixlen 64 scopeid 0x8\n"
            "\tnd6 options=23<PERFORMNUD,ACCEPT_RTADV,AUTO_LINKLOCAL>\n")
        self.assertTrue(ifc['vlan0.102']['ipv6_disabled'])
        self.assertFalse(ifc['vlan0.101']['ipv6_disabled'])

    def test_rows_carry_ipv6_off_and_gateway(self):
        by = {i['key']: i for i in detect(ET.fromstring(CONFIG), IFCONFIG, NETSTAT)['interfaces']}
        self.assertTrue(by['wan']['ipv6_off'])          # vlan0.101 above has no inet6 line
        self.assertTrue(by['wan']['gateway'])           # WAN_DHCP


class PublicTest(unittest.TestCase):
    def test_site_local_is_private(self):
        from lib.detect import _public
        self.assertEqual(_public('fec0::5'), 'private')
        self.assertEqual(_public('2a01:4f8::1'), 'public')


if __name__ == '__main__':
    unittest.main()
