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
    --------------------------------------------------------------------------------------
    Shaper health checks S1-S8 as JSON (lib/health.py), with the internet links from
    lib/detect.py for S4. Read-only.
"""
import json
import time
from lib import snapshot, run, CONFIG
from lib.detect import detect
from lib.health import run_checks


def links():
    """ internet-link detection: config.xml + one ifconfig -a + one netstat -rn + core's runtime
    gateway list (dynamic DHCP/PPPoE/DHCPv6 gateways are not in config.xml), with the user's
    saved ticks """
    import os
    import xml.etree.ElementTree as ET
    from lib.plan import link_choices
    try:
        root = ET.parse(CONFIG).getroot()
    except (OSError, ET.ParseError) as exc:
        return {'interfaces': [], 'links': [], 'routes': {}, 'error': str(exc)}
    rc1, ifc, err1 = run(['/sbin/ifconfig', '-a'])
    rc2, routes, err2 = run(['/usr/bin/netstat', '-rn'])
    gateways_php = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gateways.php')
    rc3, gwout, err3 = run(['/usr/local/bin/php', gateways_php])
    try:
        gateways = json.loads(gwout) if rc3 == 0 else None
    except ValueError:
        gateways, rc3, err3 = None, 1, 'gateway list unreadable'
    if not isinstance(gateways, list):
        gateways = None     # fall back to the gateways config.xml stores; the error says so
        err3 = 'runtime gateway list not available (%s); dynamic gateways are not shown' % (err3.strip() or 'no output')
        rc3 = rc3 or 1
    out = detect(root, ifc, routes, gateways, link_choices(CONFIG)['select'])
    out['error'] = '; '.join(e.strip() for rc, e in ((rc1, err1), (rc2, err2), (rc3, err3)) if rc != 0 and e.strip())
    return out


if __name__ == '__main__':
    t0 = time.monotonic()
    snap = snapshot()
    det = links()
    wans = [i['key'] for i in det['interfaces'] if i['selected']]
    try:
        with open('/var/run/shaperinsight-prio.json') as f:
            prio_last = json.load(f)
    except (OSError, ValueError):
        prio_last = None
    from lib.plan import priorities_set
    try:
        import os
        reloaded = os.stat('/var/run/shaperinsight-ipfw-reload').st_mtime
    except OSError:
        reloaded = None
    try:
        import sampler
        samp = {'enabled': sampler.enabled(), 'state': sampler.run_state(), 'watchdog': sampler.watchdog_info(),
                'cost': sampler.status().get('cost', {})}
        try:
            samp['assess'] = sampler.assess()
        except Exception:           # the suggestion is a convenience; the check stands without it
            samp['assess'] = None
    except Exception:
        samp = None
    # S3: bytes per interface now and at the last rule reload (rc.ipfw stores netstat's JSON in
    # the reload stamp); one netstat run, ~1 ms. CLOCK_MONOTONIC counts from boot on FreeBSD
    from lib.health import interface_view
    rc, now_stats, _err = run(['/usr/bin/netstat', '-ibn', '--libxo', 'json'])
    try:
        with open('/var/run/shaperinsight-ipfw-reload') as f:
            base_stats = f.read(4 << 20)
    except OSError:
        base_stats = ''
    ifview = interface_view(det, now_stats if rc == 0 else '', base_stats, reloaded,
                            time.time() - time.monotonic())
    checks = run_checks(snap, wans, prio_last, priorities_set(), reloaded, sampler=samp, ifview=ifview)
    no_shaper = all(i['text'].startswith('Not applicable: no shaper') for c in checks if c['id'] not in ('S9', 'S10') for i in c['items'])
    print(json.dumps({'checks': checks, 'no_shaper': no_shaper, 'wans': wans, 'detect_error': det['error'],
                      'generated': snap['generated'], 'elapsed_ms': round((time.monotonic() - t0) * 1000, 1)}))
