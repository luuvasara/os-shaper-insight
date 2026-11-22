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

    hostinfo.py: per address the MAC, the manufacturer and the other addresses behind the same MAC,
    for the host tables (Name, IP, MAC, Manufacturer columns; user feedback 2026-11-22).
    - MACs from what the firewall already has: ARP, NDP and the Kea/Dnsmasq leases (prio.neighbours).
    - Manufacturer from the MAC's first three bytes in core's IEEE list,
      /usr/local/opnsense/contrib/ieee/oui.csv, read the way core's Interfaces: Diagnostics: ARP table
      reads it (scripts/interfaces/lib OUI: column 1 = prefix, column 2 = vendor). No downloads.
    - Several addresses on one MAC (ipvlan containers share their host's MAC) are reported as "shared":
      the manufacturer is then the host machine's, for all of them.
    The prefix table is cached in /var/run/shaperinsight/oui.json, rebuilt when oui.csv changes.
"""

import csv
import json
import os
import sys
from lib import cache_dir
import prio

OUI_FILE = '/usr/local/opnsense/contrib/ieee/oui.csv'


def oui_table():
    """ core's IEEE list as {6-hex prefix: registrant}; parsed once and cached while the file's
    mtime stays the same (core's own ARP page parses it per request) """
    try:
        mtime = os.stat(OUI_FILE).st_mtime
    except OSError:
        return {}
    try:
        cache = os.path.join(cache_dir(), 'oui.json')
    except (OSError, RuntimeError):
        cache = None
    try:
        if cache and os.stat(cache).st_mtime == mtime:
            with open(cache) as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    db = {}
    with open(OUI_FILE, newline='', encoding='utf-8', errors='replace') as f:
        for idx, rec in enumerate(csv.reader(f, delimiter=',')):
            if idx > 0 and len(rec) > 2:
                db[rec[1]] = rec[2]
    if cache:
        try:
            tmp = cache + '.tmp'
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, 'w') as f:
                json.dump(db, f)
            os.utime(tmp, (mtime, mtime))
            os.rename(tmp, cache)
        except OSError:
            pass    # no cache: parsed again next time, the answer is the same
    return db


def vendor(db, mac):
    """ the IEEE registrant, or '' """
    return db.get(mac.replace(':', '').replace('-', '')[:6].upper(), '')


def is_random(mac):
    """ a locally administered MAC (second-lowest bit of the first byte): a phone's private Wi-Fi
    address, a virtual machine or a container; the IEEE list cannot name a maker for it """
    try:
        return bool(int(mac.replace(':', '').replace('-', '')[:2], 16) & 0x02)
    except ValueError:
        return False


def host_info(found=None, db=None):
    """ {address: {mac, vendor, shared: [other addresses on that MAC]}} """
    found = prio.neighbours() if found is None else found
    db = oui_table() if db is None else db
    out = {}
    for mac, addrs in found.items():
        addrs = sorted(str(a).split('/')[0] for a in addrs)
        for a in addrs:
            out[a] = {'mac': mac, 'vendor': vendor(db, mac), 'random': is_random(mac),
                      'shared': [x for x in addrs if x != a]}
    return out


if __name__ == '__main__':
    try:
        print(json.dumps({'status': 'ok', 'hosts': host_info()}))
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'message': 'crashed: %r' % (exc,), 'hosts': {}}))
        sys.exit(0)
