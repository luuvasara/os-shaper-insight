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

"""
File modes check: the package installs files that root runs (configd actions, cron, the ipfw
rc script), so no file or directory in it may be writable by group or others. A package built
from a tree checked out or unpacked under umask 0002 gets 664/775 files (1.0.h to 1.0.k did).

    tools/check_modes.py --tree net/shaper-insight     the source tree a package is built from
    tools/check_modes.py PKG [PKG ...]                 built packages (reads `tar -tvf PKG`)

Exit 0 when nothing is group/other-writable, 1 when something is (each path printed), 2 when a
package cannot be listed. Fix a tree with `chmod -R go-w net/shaper-insight`, or build with
`umask 022` (`git -c tar.umask=022 archive ...`).
"""
import os
import subprocess
import sys


def tree_writable(root):
    """ paths under root (root included) whose mode has group or other write; skips __pycache__
    and *.pyc, which running the tests creates and .gitignore keeps out of every export """
    bad = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x != '__pycache__']
        for p in [d] + [os.path.join(d, f) for f in files if not f.endswith('.pyc')]:
            st = os.lstat(p)
            if not os.path.islink(p) and st.st_mode & 0o022:
                bad.append('%o %s' % (st.st_mode & 0o7777, p))
    return bad


def listing_writable(listing):
    """ lines of a `tar -tvf` listing whose mode string has w in the group or other place
    (drwxrwxr-x: index 5 is group write, 8 other write); symlinks (l...) are always rwx """
    bad = []
    for line in listing.splitlines():
        mode = line.split(None, 1)[0] if line.strip() else ''
        if len(mode) >= 10 and mode[0] != 'l' and (mode[5] == 'w' or mode[8] == 'w'):
            bad.append(line)
    return bad


def package_writable(pkg):
    proc = subprocess.run(['tar', '-tvf', pkg], capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError('tar -tvf %s failed (rc %d): %s' % (pkg, proc.returncode, proc.stderr.strip()))
    return listing_writable(proc.stdout)


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__.strip())
        return 2
    bad, rc = [], 0
    if argv[0] == '--tree':
        for root in argv[1:] or ['net/shaper-insight']:
            if not os.path.isdir(root):
                print('not a directory: %s' % root, file=sys.stderr)
                return 2
            bad += tree_writable(root)
    else:
        for pkg in argv:
            try:
                bad += package_writable(pkg)
            except (RuntimeError, OSError) as exc:
                print(exc, file=sys.stderr)
                rc = 2
    for line in bad:
        print('group/other-writable: %s' % line)
    if rc:
        return rc
    print('modes OK' if not bad else '%d group/other-writable entries' % len(bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
