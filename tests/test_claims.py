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

"""Every claim id the UI links exists in claims.json (the registry check needs claims.yaml:
tools/claims/build.py --check <claims.yaml>)."""
import importlib.util
import os
import unittest

# loaded under its own name: tools/glossary/build.py is also "build"
_spec = importlib.util.spec_from_file_location(
    'claims_build', os.path.join(os.path.dirname(__file__), '..', 'tools', 'claims', 'build.py'))
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)


class ClaimIdsTest(unittest.TestCase):
    def test_every_used_id_has_an_entry(self):
        self.assertEqual(build.main(['build.py', '--check']), 0)

    def test_extraction_sees_lists_and_ternaries(self):
        used = build.used_ids()
        for cid in ('ack-filter', 'bottleneck', 'tins-capped', 'module-match', 'apply-no-flush'):
            self.assertIn(cid, used)


if __name__ == '__main__':
    unittest.main()
