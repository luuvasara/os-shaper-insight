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

"""Security review 2026-11-22 (TESTING.md): one regression test per finding S1-S14, I1."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, '..', 'net', 'shaper-insight')
SCRIPTS = os.path.join(PKG, 'src', 'opnsense', 'scripts', 'shaperinsight')
MVC = os.path.join(PKG, 'src', 'opnsense', 'mvc', 'app')
sys.path.insert(0, SCRIPTS)
import apply  # noqa: E402
import bbtest  # noqa: E402
import prio  # noqa: E402
from lib.plan import build  # noqa: E402
from lib.redact import Redactor  # noqa: E402


def read(*parts):
    with open(os.path.join(*parts)) as f:
        return f.read()


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.patches = []

    def patch(self, *a, **k):
        p = mock.patch.object(*a, **k)
        self.patches.append(p)
        return p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp)


class S1FailClosed(TmpCase):
    def hook(self, apply_body, state=None, force=False):
        """ run +PRE_DEINSTALL with a stand-in apply.py and state file """
        fake = os.path.join(self.tmp, 'apply.py')
        with open(fake, 'w') as f:
            f.write('#!/bin/sh\n' + apply_body + '\n')
        os.chmod(fake, 0o755)
        st = os.path.join(self.tmp, 'state.json')
        if state is not None:
            with open(st, 'w') as f:
                f.write(state)
        script = read(PKG, '+PRE_DEINSTALL.pre').replace('/usr/local/opnsense/scripts/shaperinsight/apply.py', fake) \
            .replace('/conf/shaperinsight-apply.json', st)
        env = dict(os.environ, PATH='/usr/bin:/bin')
        env.pop('PKG_UPGRADE', None)
        if force:
            env['SHAPERINSIGHT_FORCE_REMOVE'] = '1'
        return subprocess.run(['sh', '-c', script], env=env, capture_output=True, text=True).returncode

    def test_crash_while_pending_stops_pkg(self):
        crash = 'echo "Traceback: CacheDirError"; exit 1'
        self.assertEqual(self.hook(crash, '{"state": "waiting"}'), 1)
        self.assertEqual(self.hook(crash, 'garbage'), 1)                  # unreadable state = assume pending
        self.assertEqual(self.hook(crash, '{"state": "waiting"}', force=True), 0)

    def test_crash_without_pending_apply_goes_on(self):
        crash = 'echo "Traceback"; exit 1'
        self.assertEqual(self.hook(crash, '{"state": "confirmed"}'), 0)
        self.assertEqual(self.hook(crash), 0)                              # never applied

    def test_reported_failure_stops(self):
        self.assertEqual(self.hook('echo \'{"status": "failed", "steps": []}\'; exit 1', '{"state": "confirmed"}'), 1)
        self.assertEqual(self.hook('echo \'{"status": "ok", "steps": []}\'', '{"state": "waiting"}'), 0)

    @unittest.skipIf(os.geteuid() == 0 or os.path.isdir('/var/run/shaperinsight'), 'needs a box without the cache dir')
    def test_apply_py_exit_status(self):
        # here cache_dir() cannot create /var/run/shaperinsight: the crash the review describes
        r = subprocess.run([sys.executable, '-B', os.path.join(SCRIPTS, 'apply.py'), 'uninstall'],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn('"status": "failed"', r.stdout)
        self.assertIn('crashed', r.stdout)


class S2AliasCost(TmpCase):
    def test_cap_refuses_large_targets(self):
        members = {'interactive': {'bogons': ['10.%d.0.0/16' % i for i in range(prio.MAX_PER_TARGET + 1)],
                                   'pc': ['192.168.1.20/32']}, 'background': {}}
        errors = prio.enforce_cap(members)
        self.assertEqual(members['interactive']['bogons'], [])
        self.assertEqual(members['interactive']['pc'], ['192.168.1.20/32'])
        self.assertIn('refused', errors[0])

    def test_one_ipfw_process_for_all_edits(self):
        calls = []

        def ipfw(*args):
            calls.append(args)
            if args[0] == 'table' and args[2] == 'list':
                return 0, '', ''
            return 0, '', ''
        self.patch(prio, 'ipfw', ipfw)
        errors = []
        prio.sync_tables({'interactive': {'a': ['10.0.%d.%d' % (i // 250, i % 250) for i in range(1000)]},
                          'background': {}}, errors)
        edits = [c for c in calls if c[0] == '-q']
        self.assertEqual(len(edits), 1)                     # one batch, not 1000 processes
        self.assertTrue(errors)                             # the fake table stays empty: reported

    def test_refresh_never_blocks(self):
        import fcntl
        self.patch(prio, 'LOCK', os.path.join(self.tmp, 'lock'))
        self.patch(prio, 'REMOVING', os.path.join(self.tmp, 'removing'))
        fd = os.open(prio.LOCK, os.O_RDWR | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            t0 = time.monotonic()
            out = prio.main('refresh')
            self.assertLess(time.monotonic() - t0, 1)
            self.assertEqual(out.get('skipped'), 'busy')
        finally:
            os.close(fd)

    def test_controller_restricts_alias_types(self):
        src = read(MVC, 'controllers', 'OPNsense', 'ShaperInsight', 'Api', 'PrioritiesController.php')
        self.assertIn("['host', 'network']", src)
        self.assertIn('targetProblem()', src.split('function addItemAction')[1].split('}')[0])


class S3RevertMarker(TmpCase):
    def setUp(self):
        super().setUp()
        self.marker = os.path.join(self.tmp, 'revert-now')
        self.patch(apply, 'REVERT_NOW', self.marker)

    def write(self, text, age=0):
        with open(self.marker, 'w') as f:
            f.write(text)
        if age:
            t = time.time() - age
            os.utime(self.marker, (t, t))

    def test_only_this_jobs_marker_counts(self):
        start = time.time()
        self.write('other-token')
        self.assertFalse(apply.revert_marker_for('my-token', start))
        self.assertFalse(os.path.exists(self.marker))                       # stale one removed
        self.write('my-token', age=3600)
        self.assertFalse(apply.revert_marker_for('my-token', start))         # older than the job
        self.write('my-token')
        self.assertTrue(apply.revert_marker_for('my-token', start))

    def test_uninstall_clears_marker_when_giving_up(self):
        self.patch(apply, 'lock_held', lambda: True)
        self.patch(apply, 'take_lock', lambda: None)
        self.patch(apply, 'run', lambda *a, **k: (0, '', ''))
        self.patch(apply, 'load_state', lambda: {'token': 't'})
        self.patch(apply.time, 'sleep', lambda s: None)
        out = apply.uninstall()
        self.assertEqual(out['status'], 'failed')
        self.assertFalse(os.path.exists(self.marker))


class S4PurgeRace(TmpCase):
    def setUp(self):
        super().setUp()
        for name in ('LOCK', 'REMOVING', 'RESULT'):
            self.patch(prio, name, os.path.join(self.tmp, name.lower()))
        self.patch(prio, 'ipfw', lambda *a: (1, '', 'no'))

    def test_purge_flags_and_keeps_lock_file(self):
        prio.purge()
        self.assertTrue(os.path.exists(prio.REMOVING))
        self.assertTrue(os.path.exists(prio.LOCK))
        for cmd in ('load', 'refresh'):
            self.assertEqual(prio.main(cmd)['skipped'], 'the plugin is being removed')

    def test_cron_hook_and_scripts_honour_the_flag(self):
        self.assertIn('shaperinsight-prio.removing', read(PKG, 'src', 'etc', 'inc', 'plugins.inc.d', 'shaperinsight.inc'))
        self.assertIn('rm -f /var/run/shaperinsight-prio.removing', read(PKG, '+POST_INSTALL.post'))
        self.assertIn('cron restart', read(PKG, '+PRE_DEINSTALL.pre'))


class S5StatusIsRead(TmpCase):
    def test_status_starts_nothing(self):
        spawned = []
        self.patch(apply, 'cache_dir', lambda: None)
        self.patch(apply, 'run', lambda *a, **k: spawned.append(a) or (0, '', ''))
        self.patch(apply, 'lock_held', lambda: False)
        self.patch(apply, 'load_state', lambda: {'state': 'waiting', 'updated': 0, 'token': 'x'})
        st = apply.main(['apply.py', 'status'])
        self.assertEqual(spawned, [])
        self.assertTrue(st['recovery_pending'])
        ctl = read(MVC, 'controllers', 'OPNsense', 'ShaperInsight', 'Api', 'ApplyController.php')
        self.assertIn('isPost()', ctl.split('function recoverAction')[1].split('function')[0])


class S6Shares(unittest.TestCase):
    def test_shares_not_in_read_only_role(self):
        status = read(MVC, 'controllers', 'OPNsense', 'ShaperInsight', 'Api', 'StatusController.php')
        self.assertNotIn('sharesAction', status)
        acl = read(MVC, 'models', 'OPNsense', 'ShaperInsight', 'ACL', 'ACL.xml')
        ro = acl.split('page-firewall-shaperinsight-status>')[1]
        self.assertNotIn('devices', ro)
        self.assertIn('sharesAction', read(MVC, 'controllers', 'OPNsense', 'ShaperInsight', 'Api', 'DevicesController.php'))


class S7RefreshLoadedOnly(TmpCase):
    def test_refresh_uses_last_load(self):
        self.patch(prio, 'last_entries', lambda: [{'class': 'interactive', 'target': '10.0.0.1'}])
        seen = {}
        self.patch(prio, 'sync_tables', lambda m, e: seen.update(m) or 0)
        prio.refresh(None)
        self.assertEqual(seen['interactive'], {'10.0.0.1': ['10.0.0.1/32']})
        self.patch(prio, 'last_entries', lambda: None)
        self.assertEqual(prio.refresh(None)['skipped'], 'nothing loaded')


class S8DegradedDetection(unittest.TestCase):
    def test_no_removal_on_degraded_detection(self):
        snap = {'config': {'pipes': [], 'queues': [], 'rules': [{'uuid': 'old', 'origin': 'ShaperInsight',
                'description': 'Insight download Link B', 'interface': 'opt1', 'direction': 'in', 'sequence': '900'}]},
                'oscake': {}, 'kernel': {'dnctl': {'pipes': {}, 'queues': {}}, 'sysctl': {}}}
        det = {'links': [{'name': 'Link A', 'root': 'igb0', 'members': ['wan'], 'why': 'x'}], 'interfaces': [],
               'error': 'runtime gateway list not available'}
        plan = build(det, snap, 100, 20)
        self.assertFalse(plan['ready'])
        self.assertTrue(any(p['level'] == 'bad' and 'Detection is incomplete' in p['text'] for p in plan['preflight']))
        det2 = dict(det, error='')
        self.assertTrue(build(det2, snap, 100, 20)['ready'])
        self.assertIn('detect_error', read(MVC, 'views', 'OPNsense', 'ShaperInsight', 'index.volt'))


class S9LockFirst(unittest.TestCase):
    def test_write_locks_before_building_the_model(self):
        src = read(SCRIPTS, 'writer.php')
        write = src.split("$cmd == 'write'")[1].split('} elseif')[0]
        self.assertLess(write.index('Config::getInstance()->lock()'), write.index('new TrafficShaper()'))
        self.assertLess(write.index('Config::getInstance()->lock()'), write.index('cake_model()'))


class S10PidFile(TmpCase):
    def test_unlocked_pidfile_is_stale(self):
        pidfile = os.path.join(self.tmp, 'job.pid')
        with open(pidfile, 'w') as f:
            f.write(str(os.getpid()))          # a live pid, but nobody holds the file
        self.patch(bbtest, 'PIDFILE', pidfile)
        killed = []
        self.patch(bbtest.os, 'killpg', lambda *a: killed.append(a))
        self.assertIsNone(bbtest.live_pid())
        self.assertFalse(os.path.exists(pidfile))
        with open(pidfile, 'w') as f:
            f.write(str(os.getpid()))
        import fcntl
        fd = os.open(pidfile, os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_EX)          # "daemon" holds it, but the pid is not bbtest.py run
        try:
            self.assertIsNone(bbtest.live_pid())
        finally:
            os.close(fd)
        self.assertEqual(killed, [])


class S11Decision(TmpCase):
    def test_start_refuses_pending_decision(self):
        self.patch(apply, 'cache_dir', lambda: None)
        self.patch(apply, 'load_state', lambda: {'state': 'needs-attention'})
        self.patch(apply, 'take_lock', lambda: self.fail('must not lock'))
        out = apply.main(['apply.py', 'start', '0', '0', 'skip', 'a' * 64])
        self.assertIn('needs your decision', out['message'])

    def test_resolve_reads_state_under_the_lock(self):
        order = []
        self.patch(apply, 'cache_dir', lambda: None)
        self.patch(apply, 'take_lock', lambda: order.append('lock') or os.open(os.devnull, os.O_RDONLY))
        self.patch(apply, 'load_state', lambda: order.append('read') or {'state': 'confirmed'})
        out = apply.main(['apply.py', 'resolve', 'keep'])
        self.assertEqual(order, ['lock', 'read'])
        self.assertEqual(out['status'], 'failed')


class S12Redactor(unittest.TestCase):
    def test_glued_ipv6_and_all_panels(self):
        r = Redactor(set())
        out = r.text('x2a01:4f8::1 y203.0.113.5 12:34:56')
        self.assertNotIn('2a01', out)
        self.assertNotIn('203.0.113', out)
        self.assertIn('12:34:56', out)
        src = read(SCRIPTS, 'compiled.py')
        self.assertIn('descriptions=True', src)
        self.assertNotIn("descriptions=p.pop('descr', False)", src)


class S13CheckedResults(TmpCase):
    def test_template_failure_fails_reconfigure(self):
        self.patch(apply, 'configd', lambda *a: (0, 'ERR') if a[0] == 'template' else (0, 'OK'))
        ok, msg = apply.reconfigure()
        self.assertFalse(ok)
        self.assertIn('template', msg)

    def test_restore_point_atomic_and_boot_logged(self):
        src = read(SCRIPTS, 'writer.php').split("$cmd == 'snapshot'")[1].split('} elseif')[0]
        self.assertIn('rename(', src)
        self.assertIn('umask(0077)', src)
        self.assertIn('logger', read(PKG, 'src', 'etc', 'rc.syshook.d', 'start', '50-shaperinsight'))
        self.assertIn("'Record what was written'", read(SCRIPTS, 'apply.py'))
        self.assertRegex(read(SCRIPTS, 'apply.py'), r"'prio.py'\), 'purge'\], timeout=\d+")


class S14KeepUnlisted(unittest.TestCase):
    def test_only_listed_choices_replaced(self):
        src = read(MVC, 'controllers', 'OPNsense', 'ShaperInsight', 'Api', 'LinksController.php')
        self.assertRegex(src, r"in_array\(\(string\)\$c->interface, \$shown, true\)")
        self.assertIn('shown: shown', read(MVC, 'views', 'OPNsense', 'ShaperInsight', 'wizard.volt'))


class I1DataAttr(unittest.TestCase):
    def test_text_attributes_not_coerced(self):
        src = read(MVC, 'views', 'OPNsense', 'ShaperInsight', 'priorities.volt')
        self.assertNotIn(".data('descr')", src)
        self.assertIn(".attr('data-descr')", src)


if __name__ == '__main__':
    unittest.main()
