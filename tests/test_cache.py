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

"""The cache directory must refuse symlinks and directories others can write to."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'net', 'shaper-insight', 'src', 'opnsense',
                                'scripts', 'shaperinsight'))
import lib  # noqa: E402


class CacheDirTest(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()

    def test_creates_private_dir(self):
        path = os.path.join(self.base, 'c')
        lib.cache_dir(path)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o700)

    def test_refuses_symlink(self):
        target = os.path.join(self.base, 'elsewhere')
        os.mkdir(target, 0o700)
        link = os.path.join(self.base, 'c')
        os.symlink(target, link)
        with self.assertRaises(lib.CacheDirError):
            lib.cache_dir(link)

    def test_refuses_open_mode(self):
        path = os.path.join(self.base, 'c')
        os.mkdir(path)
        os.chmod(path, 0o777)
        with self.assertRaises(lib.CacheDirError):
            lib.cache_dir(path)

    def test_write_is_atomic_and_read_refuses_symlink(self):
        path = os.path.join(self.base, 'c')
        lib.cache_dir(path)
        f = os.path.join(path, 'x.json')
        lib._write_json(f, {'a': 1})
        self.assertEqual(lib._read_json(f), {'a': 1})
        self.assertEqual([n for n in os.listdir(path) if n.startswith('.tmp.')], [])
        evil = os.path.join(path, 'y.json')
        os.symlink(f, evil)
        with self.assertRaises(OSError):
            lib._read_json(evil)


if __name__ == '__main__':
    unittest.main()
