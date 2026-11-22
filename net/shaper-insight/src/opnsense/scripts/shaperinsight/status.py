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
    Status & proof: is the shaper doing what its configuration says? (read-only)

      status.py [full|summary|rates]

    full     everything the status page shows (default)
    summary  the few fields the dashboard widget needs
    rates    per-pipe byte/packet counters for the live graph (cheap: cached snapshot)
"""
import json
import sys
import time
from lib import snapshot, apply_countdown
from lib.analysis import analyse


def payload_caps(snap):
    """ pipe number -> (configured bit/s, payload cap bit/s). CAKE counts each packet as IP length +
    overhead, so IP-layer bytes (what the counters show) top out at rate x MTU / (MTU + overhead):
    840 Mbit/s with overhead 42 is ~817 Mbit/s of IP packets (TESTING.md speed-test analysis 2.2) """
    from lib.traffic import payload_cap, cake_overheads
    over = cake_overheads(snap['config'])
    out = {}
    for n, p in snap['kernel']['dnctl']['pipes'].items():
        bw = p.get('bandwidth') or 0
        if bw:
            out[str(n)] = (bw, payload_cap(bw, over.get(str(n), 0)))
    return out


def rates(snap):
    """ cumulative counters per pipe: CAKE tins summed, or the ipfw rule counters. "ts" is the
    moment the counters were read; each pipe carries the stamp of its own source """
    stamps = snap['kernel'].get('counters_ts', {})
    out = {'ts': stamps.get('cake', snap['generated']), 'pipes': {}}
    for sched, instances in snap['kernel']['cake_stats'].items():
        tot = {'bytes': 0, 'pkts': 0, 'drops': 0, 'marks': 0, 'avg_delay_us': 0, 'peak_delay_us': 0}
        for inst in instances:
            for tin in inst['tins']:
                tot['bytes'] += tin.get('bytes', 0)
                tot['pkts'] += tin.get('pkts', 0)
                tot['drops'] += tin.get('drops', 0)
                tot['marks'] += tin.get('marks', 0)
                tot['avg_delay_us'] = max(tot['avg_delay_us'], tin.get('avg_delay', 0))
                tot['peak_delay_us'] = max(tot['peak_delay_us'], tin.get('peak_delay', 0))
        tot['ts'] = stamps.get('cake', snap['generated'])
        out['pipes'][str(sched)] = tot
    # pipes without CAKE: sum the ipfw counters of the rules that feed them
    cfg = snap['config']
    target_pipe = {p['uuid']: p.get('number') for p in cfg['pipes']}
    target_pipe.update({q['uuid']: target_pipe.get(q.get('pipe')) for q in cfg['queues']})
    for r in cfg['rules']:
        k = snap['kernel']['ipfw']['rules'].get(r['uuid'])
        num = target_pipe.get(r.get('target'))
        if k is None or num is None or str(num) in out['pipes'] and 'rule_bytes' not in out['pipes'][str(num)]:
            continue
        tot = out['pipes'].setdefault(str(num), {'bytes': 0, 'pkts': 0, 'rule_bytes': True,
                                                 'ts': stamps.get('ipfw', snap['generated'])})
        tot['bytes'] += k['bytes']
        tot['pkts'] += k['pkts']
    for n, (bw, cap) in payload_caps(snap).items():
        if n in out['pipes']:
            out['pipes'][n].update({'bandwidth': bw, 'payload_cap': cap})
    return out


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'full'
    t0 = time.monotonic()
    snap = snapshot()
    if mode == 'rates':
        print(json.dumps(rates(snap)))
        sys.exit(0)
    result = analyse(snap)
    result['generated'] = snap['generated']
    result['cached'] = snap['cached']
    result['versions'] = snap['versions']
    result['config_error'] = snap.get('config_error', '')
    result['errors'] = [e for e in (snap['kernel']['raw'].get('dnctl_error'),
                                    snap['kernel']['raw'].get('ipfw_error')) if e]
    if mode == 'summary':
        result = {
            'summary': result['summary'], 'module': {k: result['module'][k] for k in ('installed', 'loaded', 'usable')},
            'pipes': [{k: p[k] for k in ('number', 'description', 'enabled', 'loaded', 'intended', 'level',
                                         'bandwidth_kernel')} for p in result['pipes']],
            'generated': result['generated'],
            'apply': apply_countdown(),
        }
    else:
        result['rates'] = rates(snap)
        result['apply'] = apply_countdown()
        result['timing'] = snap['kernel']['timing']
    result['elapsed_ms'] = round((time.monotonic() - t0) * 1000, 1)
    print(json.dumps(result))
