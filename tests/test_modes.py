#!/usr/bin/env python3

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

""" File modes (2026.11.23a): packages up to 1.0.k shipped root-owned 664/775 files because they were
built from a tree made under umask 0002. A package is built from net/shaper-insight as it is on
disk, so this fails on a group/other-writable file there (fix: chmod -R go-w net/shaper-insight,
or check out under umask 022). SA_PKG=path/to/package.pkg also checks a built package. """
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import check_modes  # noqa: E402

PKG_TREE = os.path.join(ROOT, 'net', 'shaper-insight')


class Modes(unittest.TestCase):
    def test_source_tree_has_no_group_or_other_write(self):
        bad = check_modes.tree_writable(PKG_TREE)
        self.assertEqual(bad, [], 'group/other-writable files would be packaged as such; '
                                  'run: chmod -R go-w net/shaper-insight')

    def test_checker_flags_a_664_file(self):
        # negative control: the tree check must fail on what 1.0.h shipped
        with tempfile.TemporaryDirectory() as d:
            os.chmod(d, 0o755)
            p = os.path.join(d, 'health.py')
            with open(p, 'w') as f:
                f.write('#\n')
            os.chmod(p, 0o644)
            self.assertEqual(check_modes.tree_writable(d), [])
            os.chmod(p, 0o664)
            self.assertEqual(len(check_modes.tree_writable(d)), 1)
            os.chmod(p, 0o646)
            self.assertEqual(len(check_modes.tree_writable(d)), 1)

    def test_listing_parser(self):
        listing = ('-rw-r--r--  0 root   wheel    1179 Nov 23 18:00 +COMPACT_MANIFEST\n'
                   '-rwxr-xr-x  0 root   wheel    2048 Nov 23 18:00 /usr/local/opnsense/scripts/x.py\n'
                   'lrwxrwxrwx  0 root   wheel       0 Nov 23 18:00 /usr/local/link -> x\n'
                   'drwxr-xr-x  0 root   wheel       0 Nov 23 18:00 /usr/local/opnsense\n')
        self.assertEqual(check_modes.listing_writable(listing), [])
        bad = listing + ('-rw-rw-r--  0 root   wheel    1024 Nov 22 19:00 /usr/local/etc/rc.conf.d/ipfw\n'
                         'drwxrwxr-x  0 root   wheel       0 Nov 22 19:00 /usr/local/opnsense/scripts\n'
                         '-rw-r--rw-  0 root   wheel       1 Nov 22 19:00 /usr/local/o\n')
        self.assertEqual(len(check_modes.listing_writable(bad)), 3)

    def test_tar_listing_end_to_end(self):
        # a real archive with one 664 member: the package path of the checker must report it
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, 'src')
            os.mkdir(src, 0o755)
            os.chmod(src, 0o755)
            for name, mode in (('ok', 0o644), ('bad', 0o664)):
                p = os.path.join(src, name)
                with open(p, 'w') as f:
                    f.write(name)
                os.chmod(p, mode)
            tarball = os.path.join(d, 't.tar')
            subprocess.run(['tar', '-cf', tarball, '-C', d, 'src'], check=True)
            bad = check_modes.package_writable(tarball)
            self.assertEqual(len(bad), 1)
            self.assertIn('src/bad', bad[0])

    @unittest.skipUnless(os.environ.get('SA_PKG'), 'SA_PKG not set (path of a built package)')
    def test_built_package(self):
        self.assertEqual(check_modes.package_writable(os.environ['SA_PKG']), [])


if __name__ == '__main__':
    unittest.main()
