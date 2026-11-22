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


"""apply.py: the run path reaches job(), and a failing job never leaves "running" behind."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'net', 'shaper-insight', 'src', 'opnsense',
                                'scripts', 'shaperinsight'))
import apply  # noqa: E402


class RunPathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.patches = [mock.patch.object(apply, 'LOCK', os.path.join(self.tmp, 'lock')),
                        mock.patch.object(apply, 'STATE', os.path.join(self.tmp, 'state.json')),
                        mock.patch.object(apply, 'PROGRESS', os.path.join(self.tmp, 'progress')),
                        mock.patch.object(apply, 'cache_dir', lambda: None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_main_does_not_shadow_job(self):
        # a local named "job" anywhere in main() made the run path raise UnboundLocalError
        self.assertNotIn('job', apply.main.__code__.co_varnames)

    def test_run_calls_job(self):
        called = []
        with mock.patch.object(apply, 'job', lambda *a: called.append(a)):
            apply.main(['apply.py', 'run', '80', '20', 'skip', 'a' * 32, 'b' * 64])
        self.assertEqual(called, [(80, 20, 'skip', 'a' * 32, 'b' * 64)])

    def test_failing_job_is_recorded_and_recovered(self):
        def boom(*a):
            apply.save_state({'state': 'running', 'steps': []})
            raise RuntimeError('analysis failed')
        recovered = []
        with mock.patch.object(apply, 'job', boom), mock.patch.object(apply, 'recover', recovered.append):
            apply.main(['apply.py', 'run', '80', '20', 'skip', 'a' * 32, 'b' * 64])
        st = apply.load_state()
        self.assertIn('analysis failed', st['steps'][-1]['detail'])
        self.assertEqual(len(recovered), 1)

    def test_confirm_by_job_id(self):
        # the page confirms with the job id (a hash of the token); the token stays on the box
        apply.save_state({'state': 'waiting', 'token': 'c' * 32, 'steps': []})
        cdir = self.tmp
        with mock.patch.object(apply, 'CACHE_DIR', cdir), mock.patch.object(apply, 'lock_held', lambda: True), \
                mock.patch.object(apply.time, 'sleep', lambda s: None):
            r = apply.main(['apply.py', 'confirm', '0' * 16])
            self.assertIn('wrong', r['message'])
            apply.main(['apply.py', 'confirm', apply.job_id('c' * 32)])
        self.assertTrue(os.path.exists(os.path.join(cdir, 'confirm.' + 'c' * 32)))

    def test_uninstall_keeps_the_confirmed_setup(self):
        # review N1-N3: uninstall (also "make upgrade") never runs Remove and keeps the ledger
        ledger = os.path.join(self.tmp, 'ledger.json')
        with open(ledger, 'w') as f:
            f.write('{"pipe": ["u1"]}')
        apply.save_state({'state': 'confirmed', 'steps': []})
        ran = []
        with mock.patch.object(apply, 'LEDGER', ledger), mock.patch.object(apply, 'CACHE_DIR', self.tmp), \
                mock.patch.object(apply, 'remove_recorded', lambda: ran.append('remove')), \
                mock.patch.object(apply, 'run', lambda argv, **kw: ran.append(os.path.basename(argv[0]) + ' ' + argv[1])
                                  or (0, '{"status": "ok"}', '')):
            out = apply.uninstall()
        self.assertEqual(out['status'], 'ok')
        self.assertNotIn('remove', ran)
        self.assertIn('prio.py purge', ran)
        self.assertTrue(os.path.exists(ledger))
        self.assertEqual(apply.load_state()['state'], 'confirmed')


if __name__ == '__main__':
    unittest.main()
