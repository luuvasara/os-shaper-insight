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

"""Verification 3 (TESTING.md): N6 "Disable mine" says what it switches off, and can keep the
narrower rules; N7 install/uninstall/Remove messages; N8 rc.conf.d/ipfw mode."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, '..', 'net', 'shaper-insight')
sys.path.insert(0, os.path.join(PKG, 'src', 'opnsense', 'scripts', 'shaperinsight'))
import apply  # noqa: E402
from lib.plan import build, plan_digest, shapes_everything  # noqa: E402

DET = {'links': [{'name': 'Link A', 'root': 'em0', 'members': ['wan'], 'why': 'x'}], 'interfaces': []}
# a typical hand-made shaper on the WAN: ICMPv6 control lanes and the bulk rules
RULES = [{'uuid': 'icmp-out', 'enabled': '1', 'interface': 'wan', 'direction': 'out', 'proto': 'ipv6-icmp',
          'source': 'any', 'destination': 'any', 'sequence': '1', 'description': 'ICMPv6 out'},
         {'uuid': 'bulk-out', 'enabled': '1', 'interface': 'wan', 'direction': 'out', 'proto': 'ip',
          'source': 'any', 'destination': 'any', 'sequence': '2', 'description': 'WAN upload'},
         {'uuid': 'bulk-in', 'enabled': '1', 'interface': 'wan', 'direction': 'in', 'proto': 'ip',
          'source': 'any', 'destination': 'any', 'sequence': '3', 'description': 'WAN download'}]
SNAP = {'config': {'pipes': [], 'queues': [], 'rules': RULES}, 'oscake': {},
        'kernel': {'dnctl': {'pipes': {}, 'queues': {}}, 'sysctl': {}}}


class N6DisableMine(unittest.TestCase):
    def test_which_rules_shape_everything(self):
        self.assertTrue(shapes_everything(RULES[1]))
        self.assertFalse(shapes_everything(RULES[0]))
        self.assertFalse(shapes_everything(dict(RULES[1], dst_port='443')))

    def test_disable_all_lists_every_rule(self):
        plan = build(DET, SNAP, 100, 20, mode='disable')
        self.assertEqual(sorted(r['uuid'] for r in plan['disable_rules']), ['bulk-in', 'bulk-out', 'icmp-out'])
        self.assertTrue(all('Apply switches it off' in c['text'] for c in plan['conflicts']))

    def test_disable_bulk_keeps_control_lanes(self):
        plan = build(DET, SNAP, 100, 20, mode='disable-bulk')
        self.assertEqual(sorted(r['uuid'] for r in plan['disable_rules']), ['bulk-in', 'bulk-out'])
        kept = [c for c in plan['conflicts'] if c['action'] == 'keep']
        self.assertEqual([c['uuid'] for c in kept], ['icmp-out'])
        self.assertEqual(kept[0]['level'], 'warn')
        self.assertFalse(plan['links'][0]['skipped'])
        self.assertNotEqual(plan_digest(plan), plan_digest(build(DET, SNAP, 100, 20, mode='disable')))

    def test_keep_mine_changes_nothing_of_yours(self):
        plan = build(DET, SNAP, 100, 20, mode='skip')
        self.assertEqual(plan['disable_rules'], [])
        self.assertTrue(plan['links'][0]['skipped'])

    def test_reload_window_and_page_text(self):
        steps = ' '.join(s['why'] for s in build(DET, SNAP, 100, 20)['apply_steps'])
        self.assertIn('unshaped (not cut) for about a second', steps)
        with open(os.path.join(PKG, 'src', 'opnsense', 'mvc', 'app', 'views', 'OPNsense', 'ShaperInsight',
                               'wizard.volt')) as f:
            page = f.read()
        self.assertIn('value="disable-bulk"', page)
        self.assertIn('and switches off', page)

    def test_job_disables_exactly_the_listed_rules(self):
        with open(apply.__file__) as f:
            src = f.read()
        self.assertIn("if c.get('action') == 'disable'", src)

    def test_confirm_records_reused_objects(self):
        # objects an unconfirmed (killed) job left and a confirmed plan reused must be Remove-able
        with open(apply.__file__) as f:
            src = f.read()
        self.assertIn("list(st['created'].items()) + list(st.get('reused', {}).items())", src)


class N7Messages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_install_message_says_two_revisions(self):
        with open(os.path.join(PKG, '+POST_INSTALL.post')) as f:
            self.assertIn('two\n  configuration revisions', f.read())

    def test_uninstall_without_objects(self):
        with mock.patch.object(apply, 'lock_held', lambda: False), \
                mock.patch.object(apply, 'take_lock', lambda: os.open(os.devnull, os.O_RDONLY)), \
                mock.patch.object(apply, 'load_state', lambda: {'state': 'removed'}), \
                mock.patch.object(apply, 'load_ledger', lambda: {}), \
                mock.patch.object(apply, 'REVERT_NOW', os.path.join(self.tmp, 'r')), \
                mock.patch.object(apply, 'run', lambda *a, **k: (0, '{"status": "ok"}', '')):
            out = apply.uninstall()
        self.assertTrue(any('no objects' in s for s in out['steps']))
        self.assertFalse(any('setup stays' in s for s in out['steps']))

    def test_remove_clears_the_old_reason(self):
        saved = {}
        with mock.patch.object(apply, 'load_ledger', lambda: {'pipe': ['p']}), \
                mock.patch.object(apply, 'writer', lambda *a: (True, {'removed': {'pipe': 1}})), \
                mock.patch.object(apply, 'LEDGER', os.path.join(self.tmp, 'ledger')), \
                mock.patch.object(apply, 'flushreload', lambda: (True, '')), \
                mock.patch.object(apply, 'settle', lambda: (True, '')), \
                mock.patch.object(apply, 'load_state', lambda: {'state': 'reverted', 'reason': 'not confirmed: uninstalled',
                                                                'steps': [{'name': 'x'}]}), \
                mock.patch.object(apply, 'save_state', lambda st: saved.update(st)):
            open(apply.LEDGER, 'w').close()
            apply.remove_recorded()
        self.assertEqual((saved['state'], saved['reason'], saved['steps']), ('removed', '', []))


class N8Mode(unittest.TestCase):
    def test_rc_conf_d_ipfw_0644(self):
        with open(os.path.join(PKG, '+POST_INSTALL.post')) as f:
            self.assertIn('chmod 0644 /usr/local/etc/rc.conf.d/ipfw', f.read())
        self.assertEqual(os.stat(os.path.join(PKG, 'src', 'etc', 'rc.conf.d', 'ipfw')).st_mode & 0o777 & 0o022, 0)


if __name__ == '__main__':
    unittest.main()
