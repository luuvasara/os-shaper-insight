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

"""Redaction must fail closed: public addresses, MACs, DUIDs and DNS names never survive."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'net', 'shaper-insight', 'src', 'opnsense',
                                'scripts', 'shaperinsight'))
if os.environ.get('SA_REDACT_MODULE'):
    # mutation check: run these tests against another (e.g. the previous) redact.py
    import importlib.util
    _spec = importlib.util.spec_from_file_location('redact_under_test', os.environ['SA_REDACT_MODULE'])
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    Redactor = _mod.Redactor
else:
    from lib.redact import Redactor  # noqa: E402

ALLOW = {'kern.hz', 'net.pf.share_forward', 'net.inet.ip.dummynet.cake.stats', 'dummynet.ko', 'dn_cake.ko'}

LEAKS = ['nas.home.conf', 'net.example.org', '010.000.000.001', '300.1.1.1', 'a_b.example.com', 'bücher.example',
         'example.com',
         '203.0.113.5', '198.51.100.7', '2001:db8::1', '2001:db8:0:1::42', 'aa:bb:cc:dd:ee:ff',
         'AA-BB-CC-DD-EE-01', 'aabb.ccdd.ee02', '00:01:00:01:2a:3b:4c:5d:52:54:00:12:34:56', 'example.com',
         'nas.example.org', '8.8.8.8', '1.1.1.1', 'gw.isp.example']


class RedactTest(unittest.TestCase):
    def check(self, text, keep=(), allow=()):
        try:
            red = Redactor(allow)
        except TypeError:       # the pre-allow-list redactor, under SA_REDACT_MODULE
            red = Redactor()
        out = red.text(text)
        for leak in LEAKS:
            self.assertNotIn(leak.lower(), out.lower(), '%r leaked from %r -> %r' % (leak, text, out))
        for k in keep:
            self.assertIn(k, out, '%r should be kept in %r' % (k, out))
        return out

    def test_review_l1(self):
        # leading "::" IPv6, bare-hex MACs, free-text descriptions (review 2026-11-22, L1)
        r = Redactor()
        out = r.text('from ::2a01:4f8:c0c:1234 to 2001:db8::1')
        self.assertNotIn('2a01', out)
        self.assertNotIn('2001:db8', out)
        out = r.text('hwaddr aabbccddeeff count 123456789012')
        self.assertNotIn('aabbccddeeff', out)
        self.assertIn('123456789012', out)               # twelve digits are a counter, not a MAC
        out = r.text('pipe 10001: wash=1  # Johns-laptop upstairs\nadd 60001 queue 1 ip from any to any // Flat 3 TV',
                     descriptions=True)
        self.assertNotIn('Johns', out)
        self.assertNotIn('Flat 3', out)
        self.assertIn('wash=1', out)
        self.assertEqual(r.counts()['descr'], 2)
        self.assertIn('# Johns', Redactor().text('x # Johns'))   # only where the caller says so

    def test_trailing_punctuation(self):
        self.check('The WAN is 203.0.113.5.')
        self.check('servers 8.8.8.8, 1.1.1.1; done')
        self.check('(203.0.113.5)')
        self.check('"2001:db8::1".')

    def test_cidr_and_port(self):
        out = self.check('route 203.0.113.0/24 via 203.0.113.5:443')
        self.assertIn('/24', out)
        self.check('prefix 2001:db8:0:1::/64')

    def test_ipv6_forms(self):
        self.check('2001:db8::1 and 2001:DB8:0:0:0:0:0:1 and [2001:db8::1]:443')
        self.check('mapped ::ffff:203.0.113.5 ok')
        self.check('2001:db8:0:1::42%vtnet1')

    def test_private_kept(self):
        r = Redactor()
        out = r.text('lan 192.168.1.10 ula fd00::1 ll fe80::1%vtnet0 cgnat 100.64.3.9 lo 127.0.0.1')
        for k in ('192.168.1.10', 'fd00::1', 'fe80::1%vtnet0', '100.64.3.9', '127.0.0.1'):
            self.assertIn(k, out)
        self.assertEqual(r.counts()['private_kept'], 5)

    def test_macs_and_duid(self):
        out = self.check('mac aa:bb:cc:dd:ee:ff dash AA-BB-CC-DD-EE-01 cisco aabb.ccdd.ee02 '
                         'duid 00:01:00:01:2a:3b:4c:5d:52:54:00:12:34:56')
        self.assertIn('<duid-1>', out)
        self.assertIn('<mac-3>', out)

    def test_hostnames(self):
        out = self.check('host example.com and nas.example.org, gw.isp.example.')
        self.assertIn('<host-1>', out)

    def test_harmless_kept(self):
        out = self.check('kern.hz: 1000 net.pf.share_forward=1 dummynet.ko dn_cake.ko 15.1-RELEASE-p6 '
                         'net.inet.ip.dummynet.cake.stats 12:34:56 0x0 100.000 Mbit/s 26.7.9_3 1.4.15',
                         allow=ALLOW, keep=('100.000', '26.7.9_3', '1.4.15', 'kern.hz', 'net.pf.share_forward', 'dummynet.ko', 'dn_cake.ko', '15.1-RELEASE-p6',
                               'net.inet.ip.dummynet.cake.stats', '12:34:56'))
        self.assertNotIn('<', out)

    def test_no_prefix_or_suffix_exemption(self):
        # a host name that merely ends like a file or starts like a sysctl is still a host name
        out = self.check('backup to nas.home.conf, resolver net.example.org', allow=ALLOW)
        self.assertIn('<host-1>', out)
        self.assertIn('<host-2>', out)

    def test_non_canonical_quads(self):
        # leading zeros and octets > 255 do not parse as addresses, but must not survive either
        out = self.check('peer 010.000.000.001 and 300.1.1.1 and 0010.1.1.1 netstat 203.0.113.5.443')
        self.assertNotIn('0010.1.1.1', out)
        self.assertIn('<addr-1>', out)
        self.assertNotIn('203.0.113.5', out)

    def test_underscore_and_unicode_labels(self):
        out = self.check('dns a_b.example.com and bücher.example and ÄÖ.example')
        self.assertNotIn('cher', out)
        self.assertNotIn('ÄÖ', out)

    def test_private_quad_kept_in_netstat_form(self):
        # 192.168.1.10.443 (netstat style) keeps the private part, never invents a leak
        out = Redactor().text('lan 192.168.1.10.443')
        self.assertIn('192.168.1.10', out)

    def test_stable_placeholders(self):
        r = Redactor()
        a = r.text('203.0.113.5')
        b = r.text('again 203.0.113.5')
        self.assertEqual(a, '<public-v4-1>')
        self.assertEqual(b, 'again <public-v4-1>')


if __name__ == '__main__':
    unittest.main()
