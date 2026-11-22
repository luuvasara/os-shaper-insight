#!/usr/local/bin/python3

"""
    Copyright (c) 2026 os-shaper-insight contributors
    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice,
     this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright
     notice, this list of conditions and the following disclaimer in the
     documentation and/or other materials provided with the distribution.

    THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
    INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
    AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
    AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
    OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
    SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
    INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
    CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
    ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
    POSSIBILITY OF SUCH DAMAGE.
    Link detection (lib/detect.py) and the wizard's plan preview (lib/plan.py) as JSON.
    Dry run: nothing is written.
      detect.py [download_mbit upload_mbit [skip|disable|disable-bulk]]
    Each link's own speeds and line type come from the saved choices (model links); the two
    numbers are only the fallback for a link without saved speeds.
"""
import json
import sys
from lib import snapshot
from lib.plan import build, priorities_set, link_choices
from health import links


def mbit(arg):
    """ a link rate from the page: whole Mbit/s, 0 to 100000; anything else counts as not measured """
    return int(arg) if arg.isdigit() and int(arg) <= 100000 else 0


if __name__ == '__main__':
    args = (sys.argv[1:] + ['0', '0', 'skip'])[:3]
    mode = args[2] if args[2] in ('disable', 'disable-bulk') else 'skip'
    det = links()
    # fresh, not the status pages' cached copy: the plan must match the configuration as it is
    # now, or it would offer to add what was just added (not polled, so the cost is fine)
    snap = snapshot(0)
    plan = build(det, snap, mbit(args[0]), mbit(args[1]), priorities_set(), link_choices()['rates'], mode)
    print(json.dumps({'detect': det, 'plan': plan, 'generated': snap['generated']}))
