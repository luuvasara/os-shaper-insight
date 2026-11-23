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
    Shaper health checks S1-S8. Each finding has a level (ok, info, warn, bad), a one-line
    text, a "why" and, where it helps, an example of the fix. Read-only: works on one
    snapshot (config + kernel) and the WAN list from lib.detect.

      S1 queue weights the scheduler ignores     S5 direction symmetry
      S2 configuration vs loaded kernel objects  S6 rules shadowed by a catch-all
      S3 enabled rules that never matched        S7 unused pipes and queues
      S4 all internet links follow the same rules S8 FQ-CoDel flows and memory per instance
"""
import re
import time
from collections import defaultdict

CHECKS = [
    ('S1', 'Queue weights the scheduler ignores'),
    ('S2', 'Configuration vs what the kernel has loaded'),
    ('S3', 'Enabled rules that never matched'),
    ('S4', 'All internet links follow the same rules'),
    ('S5', 'Both directions shaped'),
    ('S6', 'Rules that can never match (after a catch-all)'),
    ('S7', 'Unused pipes and queues'),
    ('S8', 'FQ-CoDel flows and memory per instance'),
    ('S9', 'Priority marks loaded'),
    ('S10', 'History sampler running'),
]
# schedulers that do not use queue weights (kernel type names); ipfw(8): only WF2Q+ and QFQ
# "share bandwidth according to their weights"
WEIGHTLESS = {'FQ_CODEL': 'FQ-CoDel', 'FQ_PIE': 'FQ-PIE', 'FIFO': 'FIFO', 'CAKE': 'CAKE'}
CONFIG_TYPE = {'fq_codel': 'FQ_CODEL', 'fq_pie': 'FQ_PIE', 'fifo': 'FIFO', '': 'WF2Q+', 'wf2q+': 'WF2Q+',
               'rr': 'RR', 'qfq': 'QFQ'}
FQ_FLOW_BYTES = 96          # ~sizeof(struct fq_codel_flow) on amd64
FQ_DEFAULT_FLOWS = 1024     # dn_sched_fq_codel.c default when flows is blank
LEVELS = ['bad', 'warn', 'info', 'ok']


def _on(x):
    return x.get('enabled') == '1'


RELOAD_GRACE = 900          # an ipfw reload this recent explains a rule with 0 packets


VIA = re.compile(r'\b(?:(in|out) )?(via|recv|xmit) (\S+)')
QUIET_BYTES = 1000000       # under 1 MB an interface carried only ARP, DHCP and gateway pings
IPV6_PROTOS = ('ip6', 'ipv6', 'ipv6-icmp', 'icmp6')


def cap(text):
    return text[:1].upper() + text[1:]


def fmt_bytes(n):
    for unit, size in (('TB', 1e12), ('GB', 1e9), ('MB', 1e6), ('kB', 1e3)):
        if n >= size:
            return '%.1f %s' % (n / size, unit)
    return '%d bytes' % n


def since_text(cnt):
    """ "the last rule reload (23 Nov 17:02)" or "boot (21 Nov 09:10)" """
    when = time.strftime('%d %b %H:%M', time.localtime(cnt['ts'])) if cnt.get('ts') else ''
    what = 'the last rule reload' if cnt.get('since') == 'reload' else 'boot'
    return '%s (%s)' % (what, when) if when else what


def match_text(body):
    """ "queue 10002 tcp from 192.168.1.11 to any out via vtnet1" -> "tcp from 192.168.1.11 to any" """
    body = re.sub(r'^(queue|pipe)\s+\d+\s+', '', body)
    return VIA.sub('', body).strip()


def ipv6_only(r, body):
    """ the rule can only match IPv6: an IPv6 protocol in the config or as ipfw's protocol word """
    if (r.get('proto') or '').lower() in IPV6_PROTOS:
        return True
    words = body.split()
    return len(words) > 2 and words[2].lower() in IPV6_PROTOS


def parse_netstat(text):
    """ netstat -ibn --libxo json -> {dev: {'in': bytes, 'out': bytes}} (the <Link#n> line per device) """
    import json
    try:
        rows = json.loads(text)['statistics']['interface']
    except (ValueError, KeyError, TypeError):
        return {}
    out = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and str(row.get('network', '')).startswith('<Link#'):
            try:
                out[row['name']] = {'in': int(row.get('received-bytes', 0)), 'out': int(row.get('sent-bytes', 0)),
                                    'in_pkts': int(row.get('received-packets', 0)),
                                    'out_pkts': int(row.get('sent-packets', 0))}
            except (KeyError, TypeError, ValueError):
                continue
    return out


def own_bytes(counters, parents):
    """ a VLAN parent's counters include its VLANs' frames plus their 4-byte tag (lab, 2026-11-23:
    3,000 frames on vlan0.102 added 4,326,000 bytes there and 4,337,996 on vtnet1); take them off,
    so a rule on the parent is judged by the parent's own traffic
    :param parents: {vlan device: parent device} """
    own = {dev: dict(c) for dev, c in counters.items()}
    for child, parent in parents.items():
        c, p = counters.get(child), own.get(parent)
        if c and p:
            for d in ('in', 'out'):
                p[d] = max(0, p[d] - c[d] - 4 * c.get(d + '_pkts', 0))
    return own


def interface_view(det, netstat_now, netstat_base=None, reloaded=None, boot=None):
    """ what S3 needs to know about interfaces, from data health.py already has
    :param det: lib.detect result (interfaces, routes)
    :param netstat_now: netstat -ibn --libxo json now; netstat_base: the same at the last rule reload
        (rc.ipfw writes it into the reload stamp), so the bytes compare with the rule counters
    :return: {ifaces: {key: {descr, device, ipv6_off, gateway}}, bytes: {dev: {in, out, since, ts}},
              default: [devs]}
    """
    parents = {i['device']: i['vlan_parent'] for i in det.get('interfaces') or []
               if i.get('vlan_parent') and i.get('device')}
    now_c = parse_netstat(netstat_now or '')
    base_c = parse_netstat(netstat_base or '') if netstat_base else {}
    # raw counters only grow; one that went down (interface re-created) or is missing from the
    # reload's copy is counted from boot. VLAN frames come off the parent after that step
    raw, since = {}, {}
    for dev, c in now_c.items():
        b = base_c.get(dev)
        if b and reloaded and all(c[k] >= b[k] for k in c):
            raw[dev], since[dev] = {k: c[k] - b[k] for k in c}, ('reload', reloaded)
        else:
            raw[dev], since[dev] = dict(c), ('boot', boot)
    changed = True
    while changed:      # parent and VLANs must cover the same span before one is taken off the other
        changed = False
        for dev, parent in parents.items():
            if dev in since and parent in since and since[dev] != since[parent]:
                for d in (dev, parent):
                    raw[d], since[d] = dict(now_c[d]), ('boot', boot)
                changed = True
    out = {dev: {'in': c['in'], 'out': c['out'], 'since': since[dev][0], 'ts': since[dev][1]}
           for dev, c in own_bytes(raw, parents).items()}
    return {'ifaces': {i['key']: {'descr': i.get('descr'), 'device': i.get('device'), 'ipv6_off': i.get('ipv6_off'),
                                  'gateway': i.get('gateway')} for i in det.get('interfaces') or []},
            'bytes': out, 'default': (det.get('routes') or {}).get('inet', [])}


def _catch_all(r):
    """ the rule matches every IP packet on its interface and direction """
    return all([
        (r.get('proto') or 'ip') == 'ip',
        (r.get('source') or 'any') == 'any',
        (r.get('destination') or 'any') == 'any',
        r.get('source_not') != '1',
        r.get('destination_not') != '1',
        (r.get('src_port') or 'any') == 'any',
        (r.get('dst_port') or 'any') == 'any',
        not r.get('iplen'),
        not r.get('dscp'),
        not r.get('interface2'),
    ])


def run_checks(snap, wans, prio_last=None, prio_wanted=False, reloaded=None, now=None, sampler=None, ifview=None):
    """ :param wans: config keys of the internet-facing interfaces (lib.detect)
    :param reloaded: time of the last ipfw rule reload (rc.ipfw stamps it), which zeroes every
        rule counter; within RELOAD_GRACE a rule with 0 packets is "wait for traffic", not a warning
    :param prio_last: the last prio.py load/refresh result (or None), for S9
    :param prio_wanted: an enabled priority entry exists
    :param ifview: interface_view(): bytes per interface, IPv6 state and policy routing, for S3
    :return: list of {id, title, level, items: [{level, text, why, example}]}
    """
    cfg, kern = snap['config'], snap['kernel']
    pipes, queues, rules = cfg['pipes'], cfg['queues'], cfg['rules']
    kpipes = {int(k): v for k, v in kern['dnctl']['pipes'].items()}
    kqueues = {int(k): v for k, v in kern['dnctl']['queues'].items()}
    krules = kern['ipfw']['rules']
    pipe_by = {p['uuid']: p for p in pipes}
    queue_by = {q['uuid']: q for q in queues}
    erules = [r for r in rules if _on(r)]
    out = defaultdict(list)

    def add(cid, level, text, why='', example=''):
        out[cid].append({'level': level, 'text': text, 'why': why, 'example': example})

    def pname(p):
        return 'pipe %s%s' % (p.get('number', '?'), ' "%s"' % p['description'] if p.get('description') else '')

    def qname(q):
        return 'queue %s%s' % (q.get('number', '?'), ' "%s"' % q['description'] if q.get('description') else '')

    def tname(uuid):
        if uuid in queue_by:
            return qname(queue_by[uuid])
        if uuid in pipe_by:
            return pname(pipe_by[uuid])
        return 'a missing pipe or queue'

    def rname(r):
        """ what Firewall: Shaper: Rules shows: description, Sequence, interface name, direction """
        key = r.get('interface', '?')
        where = '%s %s' % ((((ifview or {}).get('ifaces') or {}).get(key) or {}).get('descr') or key,
                           r.get('direction') or 'both')
        if r.get('description'):
            return 'rule "%s" (sequence %s, %s)' % (r['description'], r.get('sequence', '?'), where)
        return 'the rule with sequence %s (%s)' % (r.get('sequence', '?'), where)

    def running(p):
        """ scheduler the kernel runs for a pipe, else the configured one """
        try:
            k = kpipes.get(int(p.get('number', 0)))
        except ValueError:
            k = None
        if k and k.get('type') not in (None, '?'):
            return k['type']
        return CONFIG_TYPE.get((p.get('scheduler') or '').lower(), (p.get('scheduler') or '').upper())

    # S1
    for p in pipes:
        qs = [q for q in queues if q.get('pipe') == p['uuid'] and _on(q)]
        weights = {q.get('number'): q.get('weight') or '100' for q in qs}
        odd = [q for q in qs if (q.get('weight') or '100') != '100']
        if not (odd or len(set(weights.values())) > 1):
            continue
        t = running(p)
        if t in WEIGHTLESS:
            for q in odd or qs:
                add('S1', 'warn', '%s has weight %s on %s, which runs %s: the weight has no effect.'
                    % (qname(q), q.get('weight') or '100', pname(p), WEIGHTLESS[t]),
                    '%s shares the link between flows (and with CAKE between hosts) by itself; only WF2Q+ '
                    'and QFQ share bandwidth by queue weight (ipfw(8)).' % WEIGHTLESS[t],
                    'Use a separate, smaller pipe for traffic that must get less, or move the queues to a '
                    'WF2Q+ pipe.')
        else:
            add('S1', 'ok', '%s (%s) uses weights %s, and this scheduler honours them.'
                % (pname(p), t, ', '.join('%s=%s' % kv for kv in sorted(weights.items()))))

    # S2
    for kind, objs, kset, name in (('pipe', pipes, kpipes, pname), ('queue', queues, kqueues, qname)):
        nums = {}
        for o in objs:
            try:
                nums[int(o.get('number'))] = o
            except (TypeError, ValueError):
                continue
        for n, o in sorted(nums.items()):
            if not _on(o) and n in kset:
                add('S2', 'warn', '%s is disabled but still loaded in the kernel.' % name(o),
                    'Apply writes and loads the enabled and disabled objects alike and never removes old '
                    'ones, so what the page shows is not what the firewall runs.',
                    'Delete it, then Apply and reload the shaper (Firewall: Shaper: Status, "Reset").')
            elif _on(o) and n not in kset:
                add('S2', 'bad', '%s is enabled but not loaded in the kernel.' % name(o),
                    'Rules that send traffic to it shape nothing. A failed Apply leaves this state.',
                    'Apply the shaper again and check the system log for dnctl errors.')
        for n in sorted(set(kset) - set(nums)):
            add('S2', 'warn', 'The kernel has %s %d, which is not in the shaper configuration.' % (kind, n),
                'Something deleted it from the configuration without reloading the shaper.',
                'Reload the shaper so the kernel matches the configuration.')
    if not out['S2']:
        add('S2', 'ok', 'Every enabled pipe and queue is loaded, and nothing else is.')

    # S3: a rule with 0 packets is told apart by what its interface carried (ifview): no traffic,
    # IPv6 rule on an IPv6-off interface, upload shaped by a sibling rule (ipfw sees a packet on the
    # interface the routing table chose; pf's reply-to moves it afterwards: lab, 2026-11-23), or
    # real traffic that no rule into that pipe caught
    iv = ifview or {}
    ifaces, ifbytes = iv.get('ifaces') or {}, iv.get('bytes') or {}

    def pipe_of(r):
        t = r.get('target')
        return queue_by[t].get('pipe') if t in queue_by else t

    def ifname(key, dev):
        d = (ifaces.get(key) or {}).get('descr') or key
        return '%s (%s)' % (d, dev) if dev and dev != d else d

    def devof(r2):
        m2 = VIA.search((krules.get(r2['uuid']) or {}).get('body', ''))
        return m2.group(3) if m2 else (ifaces.get(r2.get('interface')) or {}).get('device')

    for r in erules:
        k = krules.get(r['uuid'])
        loaded = 'Loaded as ipfw rule %s: %s' % (k['num'], k['body']) if k else ''
        if k is None:
            add('S3', 'bad', '%s is enabled but has no loaded rule.' % cap(rname(r)),
                'Its interface may be disabled, its target may be disabled, or Apply has not run.')
            continue
        if k['pkts'] > 0:
            add('S3', 'ok', '%s: %s packets.' % (cap(rname(r)), format(k['pkts'], ',')))
            continue
        m = VIA.search(k['body'])
        dev = m.group(3) if m else (ifaces.get(r.get('interface')) or {}).get('device')
        kdir = (m.group(1) if m and m.group(1) else '') or r.get('direction') or ''
        where = ifname(r.get('interface'), dev)
        info = ifaces.get(r.get('interface')) or {}
        if ipv6_only(r, k['body']) and info.get('ipv6_off'):
            add('S3', 'info', 'Not applicable: %s matches only IPv6, and IPv6 is off on %s.' % (rname(r), where),
                'The interface has no IPv6 address (FreeBSD marks it IFDISABLED), so no IPv6 packet can pass it; '
                '0 packets is expected, not a fault.',
                'Nothing needs fixing. If you switch IPv6 on for this link later, this rule starts to match. '
                + loaded)
            continue
        if reloaded and 0 <= (now or time.time()) - reloaded < RELOAD_GRACE:
            add('S3', 'info', '%s: counters reset at %s (the firewall rules were reloaded); wait for traffic, '
                'then check again.' % (cap(rname(r)), time.strftime('%H:%M:%S', time.localtime(reloaded))),
                'Every rule reload (an install, a shaper or firewall Apply, a reboot) sets the packet counters '
                'to 0, so 0 packets right after it says nothing yet.', loaded)
            continue
        cnt = ifbytes.get(dev) if dev else None
        if cnt is None:
            add('S3', 'warn', '%s has matched 0 packets since the last reload.' % cap(rname(r)),
                'A rule that never matches shapes nothing: either no traffic goes that way, or its match '
                'does not fit the packets as the firewall sees them (direction, addresses after NAT). The '
                'interface counters for %s could not be read, so this check cannot tell which.' % where, loaded)
            continue
        verb = {'out': 'sent', 'in': 'received'}.get(kdir, 'carried')
        moved = cnt['out'] if kdir == 'out' else cnt['in'] if kdir == 'in' else cnt['out'] + cnt['in']
        since = since_text(cnt)
        if moved < QUIET_BYTES:
            add('S3', 'info', 'No traffic this way since %s: %s %s %s, so %s had nothing to shape.'
                % (since, where, verb, fmt_bytes(moved), rname(r)),
                'An interface that %s (almost) nothing gives a rule nothing to match; a few kB are ARP, '
                'DHCP or gateway monitoring, which the firewall sends itself. Example: a backup internet line '
                'that only carries traffic when the main line fails.' % verb,
                'Nothing needs fixing while the link is idle. ' + loaded)
            continue
        if not _catch_all(r):
            # interface bytes cannot say whether packets of this narrower kind passed
            add('S3', 'warn', '%s matched 0 packets since %s, while %s %s %s.' % (
                cap(rname(r)), 'the last reload' if cnt.get('since') == 'reload' else since, where, verb,
                fmt_bytes(moved)),
                'The rule matches only part of the traffic (%s), so the interface bytes cannot tell whether '
                'such packets passed. Either none did, or its match does not fit the packets as the firewall '
                'sees them (direction, addresses after NAT).' % match_text(k['body']),
                loaded)
            continue

        sib = []
        if kdir == 'out':
            # another interface's catch-all upload rule into the same pipe that did match
            sib = [r2 for r2 in erules if r2 is not r and pipe_of(r2) == pipe_of(r) and _catch_all(r2)
                   and (r2.get('direction') or '') == 'out' and devof(r2) != dev
                   and (krules.get(r2['uuid']) or {}).get('pkts', 0) > 0]
            sib.sort(key=lambda r2: (r2.get('target') != r.get('target'), -krules[r2['uuid']]['pkts']))
        if sib:
            s2 = sib[0]
            k2 = krules[s2['uuid']]
            dev2 = devof(s2)
            default = dev2 in (iv.get('default') or [])
            how = ifname(s2.get('interface'), dev2) + (', which holds the default route' if default else '')
            if info.get('gateway'):
                cause = ('replies to connections that came in on %s go back out through it (reply-to, which '
                         'OPNsense adds to the rules of an interface with a gateway). Example: a server or port '
                         'forward reached through the address of %s.' % (where, where))
            else:
                cause = ('%s has no gateway, so the likely cause is a firewall rule that moves outgoing '
                         'traffic there (route-to or reply-to).' % where)
            same = s2.get('target') == r.get('target')
            add('S3', 'info', '%s matched 0 packets, but it is redundant: traffic leaving %s, %s %s since %s, is '
                'shaped by %s instead, which feeds the same %s (%s packets). Nothing is unshaped.'
                % (cap(rname(r)), where, fmt_bytes(moved), verb, since, rname(s2),
                   tname(r.get('target')) if same else tname(pipe_of(r)), format(k2['pkts'], ',')),
                'The shaper sees a packet on the interface the routing table chose, %s. Only after that does '
                'the firewall move it to %s: %s' % (how, where, cause),
                'Nothing needs fixing. Keep the rule (it costs nothing and starts to match if the default '
                'route moves to this link) or delete it to tidy up. ' + loaded)
            continue
        add('S3', 'warn', '%s %s %s since %s, but %s matched 0 packets and no other upload rule into %s '
            'matched either: this pipe shaped none of it, so unless another pipe caught it, it left unshaped.'
            % (where, verb, fmt_bytes(moved), since, rname(r), tname(pipe_of(r)))
            if kdir == 'out' else
            '%s %s %s since %s, but %s matched 0 packets: that traffic was not shaped by this rule.'
            % (where, verb, fmt_bytes(moved), since, rname(r)),
            'The traffic is there but this rule does not catch it: its match does not fit the packets as the '
            'shaper sees them (direction, addresses after NAT)%s.' % (
                '; or the firewall moves them here (reply-to, policy routing) after the shaper saw them on the '
                'interface the routing table chose, which has no rule into this pipe' if kdir == 'out' else '') + (
                ' The interface bytes count from boot and the rule counters from the last reload, so part of '
                'this traffic may be older than the rule counters.' if cnt.get('since') == 'boot' and reloaded else ''),
            'Example: for replies that reply-to sends out here, add an upload rule into this pipe on the '
            'interface that holds the route (often the default-route WAN). ' + loaded if kdir == 'out' else
            'Compare the match with the traffic (Firewall: Diagnostics: States). ' + loaded)

    # S4
    keyf = ('direction', 'proto', 'source', 'source_not', 'src_port', 'destination', 'destination_not',
            'dst_port', 'iplen', 'dscp', 'interface2', 'target')
    patterns = defaultdict(dict)
    for r in erules:
        if r.get('interface') in wans:
            patterns[tuple(r.get(k, '') for k in keyf)][r['interface']] = r
    if len(wans) < 2:
        add('S4', 'ok', 'Only one internet link found (%s), nothing to compare.' % (', '.join(wans) or 'none'))
    for key, have in patterns.items():
        if len(wans) < 2:
            break
        missing = [w for w in wans if w not in have]
        k = dict(zip(keyf, key))
        desc = '%s %s from %s to %s to %s' % (k['direction'] or 'both', k['proto'] or 'ip', k['source'] or 'any',
                                              k['destination'] or 'any', tname(k['target']))
        if missing:
            ex = next(iter(have.values()))
            add('S4', 'warn', '%s is on %s but not on %s.' % (desc, ', '.join(sorted(have)), ', '.join(missing)),
                'Traffic that leaves by %s is shaped differently from the same traffic on %s.'
                % (missing[0], ex['interface']),
                'Copy rule %s to %s, or use one rule per physical link.' % (ex.get('sequence'), ', '.join(missing)))
        else:
            add('S4', 'ok', '%s is on all %d internet links.' % (desc, len(wans)))

    # S5: mirror = opposite direction, same interface and protocol, addresses and ports swapped
    def mirrored(a, b):
        return all([
            b['interface'] == a['interface'],
            b.get('direction') != a.get('direction'),
            b.get('proto') == a.get('proto'),
            (b.get('source') or 'any') == (a.get('destination') or 'any'),
            (b.get('destination') or 'any') == (a.get('source') or 'any'),
            (b.get('src_port') or 'any') == (a.get('dst_port') or 'any'),
            (b.get('dst_port') or 'any') == (a.get('src_port') or 'any'),
        ])
    by_target = defaultdict(list)
    for r in erules:
        by_target[r['target']].append(r)
    for tgt, rs in sorted(by_target.items(), key=lambda kv: tname(kv[0])):
        dirs = {r.get('direction') or 'both' for r in rs}
        unmatched = [r for r in rs if r.get('direction') and not any(mirrored(r, r2) for r2 in erules)]
        if 'both' in dirs or len(dirs) == 2:
            add('S5', 'ok', '%s is used for both directions.' % tname(tgt))
        elif unmatched:
            d = rs[0]['direction']
            add('S5', 'info', '%s shapes only "%s" traffic, and no "%s" rule matches the replies.'
                % (tname(tgt), d, 'in' if d == 'out' else 'out'),
                'Shaping is per direction. Usually there is one pipe per direction on purpose (download and '
                'upload); this note matters when a rule was meant for both.',
                'If both directions are meant, add a rule for the other direction with source and '
                'destination swapped.')
        else:
            add('S5', 'ok', '%s has a mirror rule for the other direction.' % tname(tgt))

    # S6: ipfw stops at the first matching pipe/queue rule; rules are emitted in sequence order
    catch_all = _catch_all

    def seq(r):
        try:
            return int(r.get('sequence') or 0)
        except ValueError:
            return 0
    for ca in [r for r in erules if catch_all(r)]:
        for r in erules:
            if any([r is ca, r['interface'] != ca['interface'], r.get('direction') != ca.get('direction'),
                    seq(r) < seq(ca)]):
                continue
            tie = seq(r) == seq(ca)
            add('S6', 'warn', '%s comes %s the catch-all %s.' % (rname(r), 'at the same position as' if tie
                                                                 else 'after', rname(ca)),
                'The firewall stops at the first matching shaper rule, and a catch-all matches every '
                'packet, so this rule %s.' % ('may never match' if tie else 'can never match'),
                'Give it a sequence number lower than %s.' % ca.get('sequence'))
    if not out['S6']:
        add('S6', 'ok', 'No rule sits behind a catch-all rule.')

    # S7
    used_queues = {r['target'] for r in erules if r['target'] in queue_by}
    used_pipes = {r['target'] for r in erules if r['target'] in pipe_by}
    used_pipes |= {queue_by[u]['pipe'] for u in used_queues if _on(queue_by[u])}
    for p in pipes:
        if not _on(p):
            add('S7', 'info', '%s is disabled.' % pname(p),
                'A disabled pipe shapes nothing; check S2 to see whether the kernel still holds it.',
                'Delete it, or enable it together with the rule it was meant for.')
        elif p['uuid'] not in used_pipes:
            add('S7', 'warn', '%s is enabled, but no enabled rule sends traffic to it.' % pname(p),
                'Traffic reaches a pipe only through a rule (directly or through a queue).',
                'Delete it, or add the rule it was meant for.')
    for q in queues:
        if not _on(q):
            add('S7', 'info', '%s is disabled.' % qname(q))
        elif q['uuid'] not in used_queues:
            add('S7', 'warn', '%s is enabled, but no enabled rule sends traffic to it.' % qname(q),
                'A queue receives packets only from rules.', 'Delete it, or add a rule.')
        elif q.get('pipe') in pipe_by and not _on(pipe_by[q['pipe']]):
            add('S7', 'bad', '%s is used by rules, but its %s is disabled.' % (qname(q), pname(pipe_by[q['pipe']])),
                'The rules point at a pipe that is not configured.')
    if not out['S7']:
        add('S7', 'ok', 'Every pipe and queue is used by an enabled rule.')

    # S8
    for p in pipes:
        t = running(p)
        if not _on(p) or t not in ('FQ_CODEL', 'FQ_PIE'):
            continue
        flows = int(p.get('fqcodel_flows') or FQ_DEFAULT_FLOWS)
        kib = flows * FQ_FLOW_BYTES / 1024
        mask = p.get('mask') or 'none'
        inst = ('one instance: all traffic shares it' if mask == 'none'
                else 'one instance per %s address' % mask.replace('-', ' '))
        add('S8', 'info', '%s: %s with %d flows is about %.0f KiB per instance, %s.'
            % (pname(p), WEIGHTLESS[t], flows, kib, inst),
            'The kernel allocates this block without waiting whenever an idle instance is re-created; '
            'under memory pressure that fails and the packet is dropped ("cannot allocate memory" in the '
            'system log).', 'flows 1024 halves it.')

    # S9: the priority marks (prio.py) loaded without errors
    if prio_wanted:
        if not prio_last:
            add('S9', 'warn', 'The priority marks have not been loaded since the last restart.',
                'Priorities only work while their ipfw rules and tables are loaded.',
                'Firewall: Shaper: Shaper Insight: priorities, Apply.')
        elif prio_last.get('status') != 'ok':
            add('S9', 'bad', 'The last %s of the priority marks failed: %s' % (
                prio_last.get('command', 'load'), '; '.join(prio_last.get('errors', [])) or 'no reason given'),
                'A failed load keeps the previous marks; a failed refresh leaves devices with a new address '
                'without their priority.', 'Firewall: Shaper: Shaper Insight: priorities shows the details.')
        else:
            add('S9', 'ok', 'Loaded: %d rules (%s).' % (prio_last.get('rules', 0), prio_last.get('command', 'load'))
                if 'rules' in prio_last else 'Last refresh OK.')
    else:
        add('S9', 'info', 'Not applicable: no priority is set.')

    # S10: the history sampler (Verification 4, N10: a config save could stop it silently)
    if sampler is None:
        add('S10', 'info', 'Not applicable: the sampler state was not read.')
    elif sampler.get('enabled') is False:
        add('S10', 'info', 'Switched off in the settings (Firewall: Shaper: Shaper Insight: sharing).')
    elif sampler.get('state') == 'running':
        wd = sampler.get('watchdog') or {}
        extra = ' The watchdog restarted it %d time(s).' % wd['count'] if wd.get('count') else ''
        add('S10', 'ok', 'Recording every 10 s.' + extra)
        over = (sampler.get('cost') or {}).get('hosts_over') or {}
        if over:
            add('S10', 'info', 'Every host is stored except on pipe %s: %s hosts is over the ceiling of 2,000, so that '
                'pipe keeps its busiest 16 hosts and sums the rest.' % (', '.join(sorted(over)), ', '.join(
                    '{:,}'.format(over[k]) for k in sorted(over))),
                'The ring is in RAM (/var/run); the ceiling keeps it bounded.',
                'Firewall: Shaper: Shaper Insight: sharing, "Recording settings".')
        sug = sampler.get('assess') or {}
        if (sampler.get('cost') or {}).get('hosts_mode') == 'every' and sug.get('suggest') == 'light':
            add('S10', 'info', 'Full insight looks heavy for this firewall (%s); Light is suggested.' %
                ', '.join(sug.get('decided') or []),
                'Full stores every host for 6 h and reads them every 10 s; the suggestion compares this box with '
                'measured limits and changes nothing by itself.',
                'Firewall: Shaper: Shaper Insight: sharing, "Recording settings" shows the table and applies Light.')
    elif sampler.get('state') == 'restarting':
        add('S10', 'warn', 'The sampler process ended; daemon(8) starts it again within 30 s.',
            'While it is down, the pipe history has a gap.', 'If this stays, see the system log ("shaperinsight").')
    else:
        add('S10', 'warn', 'Switched on, but not running: the pipe history and the sharing page are not updated.',
            'The watchdog (cron, every 5 minutes) starts it again; until then nothing is recorded.',
            'Firewall: Shaper: Shaper Insight: sharing, or "configctl shaperinsight sampler.start".')

    # with no shaper at all, eight green "OK" lines would claim a health nobody checked (review M7)
    no_shaper = not any(_on(p) for p in pipes) and not erules
    result = []
    for cid, title in CHECKS:
        items = sorted(out.get(cid, []), key=lambda i: LEVELS.index(i['level']))
        if no_shaper and cid not in ('S9', 'S10'):
            items = [{'level': 'info', 'text': 'Not applicable: no shaper is configured (no enabled pipe or rule).',
                      'why': '', 'example': ''}]
        elif not items:
            items = [{'level': 'ok', 'text': 'Nothing to report.', 'why': '', 'example': ''}]
        level = min((i['level'] for i in items), key=LEVELS.index)
        result.append({'id': cid, 'title': title, 'level': level, 'items': items})
    return result
