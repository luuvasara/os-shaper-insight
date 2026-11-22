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
    The wizard's plan (building it writes nothing; apply.py writes exactly this plan, checked by
    its digest): for each link group from lib.detect,
    the pipes, queues, rules and os-cake entries Shaper Insight would create,
    the conflicts with hand-made shaper objects and the preflight warnings.
    Nothing here writes anything.
"""

ORIGIN = 'ShaperInsight'
# objects made by the plugin under its earlier name (os-shaper-assistant): still ours, matched by
# the same names, and renamed in place by the next apply (no config change at install)
LEGACY_ORIGIN = 'ShaperAssistant'
OURS = (ORIGIN, LEGACY_ORIGIN)


def norm(desc):
    """ an object name in today's form: "Assistant download Link A" -> "Insight download Link A" """
    desc = desc or ''
    tag = ''
    for t in ('[Assistant] ', '[Insight] '):
        if desc.startswith(t):
            tag, desc = '[Insight] ', desc[len(t):]
    if desc.startswith('Assistant '):
        desc = 'Insight ' + desc[len('Assistant '):]
    return tag + desc


# shape at 95 % of the measured rate, so the queue stays on this box
HEADROOM = 0.95
# without CAKE there is no overhead compensation: a larger margin
# (claim headroom-percent: ~95 % with the line type known, ~92 % without)
HEADROOM_NO_OVERHEAD = 0.92
# ICMPv6 control lane: 1 % of the link, at least 1 Mbit/s (OPNsense docs, "Control plane" how-to);
# contested: a hand-made setup seen in testing uses 10 Mbit/s
CONTROL_SHARE = 0.01
CONTROL_MIN_KBIT = 1000
# ...but never more than 5 % of the shaped rate: on a slow line 1 Mbit/s would be the whole budget
# (review H3)
CONTROL_CAP = 0.05
# below this the lane is dropped: neighbour discovery and router adverts are a few kbit/s, and a
# smaller pipe would only add its own delay
CONTROL_FLOOR_KBIT = 64
ACK_FILTER_RATIO = 15       # ACK filter only on strongly asymmetric links; threshold contested (10/15/30:1)
FIRST_PIPE = 10000
# CAKE's "host" is an address (claim host-isolation-per-address, measured: 66.5 % vs 50 %)
PER_ADDRESS = ('usually one per device; a device using IPv4 and IPv6 at the same time, or several IPv6 privacy '
               'addresses, counts once per address')
# the line types os-cake knows (its "link" field), asked per link (claim overhead-preset); "auto"
# works the framing out from the interface: Ethernet 38, +4 per VLAN tag, +8 for PPPoE
# key: (label, overhead bytes or None = worked out, mpu or '' = the preset's own, VLAN tag adds 4)
LINE_TYPES = {
    'auto': ('Ethernet or fibre (worked out from the interface)', None, '84', True),
    'docsis': ('Cable (DOCSIS)', 18, '64', False),
    'pppoe-ptm': ('VDSL2 with PPPoE (PTM)', 30, '', True),
    'bridged-ptm': ('VDSL2 without PPPoE (PTM)', 22, '', True),
    'pppoe-vcmux': ('ADSL, PPPoE VC-mux (ATM)', 32, '', False),
    'pppoe-llcsnap': ('ADSL, PPPoE LLC/SNAP (ATM)', 40, '', False),
    'pppoa-vcmux': ('ADSL, PPPoA VC-mux (ATM)', 10, '', False),
    'pppoa-llc': ('ADSL, PPPoA LLC (ATM)', 14, '', False),
    'bridged-vcmux': ('ADSL, bridged VC-mux (ATM)', 24, '', False),
    'bridged-llcsnap': ('ADSL, bridged LLC/SNAP (ATM)', 32, '', False),
    'ipoa-vcmux': ('ADSL, IPoA VC-mux (ATM)', 8, '', False),
    'ipoa-llcsnap': ('ADSL, IPoA LLC/SNAP (ATM)', 16, '', False),
    'conservative': ('ADSL, not sure which (ATM, 48 bytes)', 48, '', False),
}


def is_vlan_name(dev):
    """ VLAN devices by name: vlan0.101 (current), igb0.7, and the older vtnet1_vlan101 """
    return dev.startswith('vlan') or '.' in dev or '_vlan' in dev


def overhead_for(line, vlan, pppoe):
    """ :return: (overhead, mpu, link keyword, why) of one link """
    label, fixed, mpu, vlan_adds = LINE_TYPES.get(line, LINE_TYPES['auto'])
    tag = 4 if vlan and vlan_adds else 0
    if fixed is None:
        overhead = 38 + tag + (8 if pppoe else 0)
        why = ('Overhead %d bytes: Ethernet framing 38%s%s, so CAKE counts what the line really carries. This is '
               'the suggestion from the interface; for DSL or cable pick the line type in step 2.'
               % (overhead, ' + 4 for the VLAN tag' if tag else '', ' + 8 for PPPoE' if pppoe else ''))
        return overhead, mpu, 'ethernet', why
    overhead = fixed + tag
    return overhead, mpu, line, ('Overhead %d bytes: line type "%s", which you picked%s, so CAKE counts what the '
                                 'line really carries.' % (overhead, label, ' (+ 4 for the VLAN tag)' if tag else ''))


def split_rate(mbit, headroom=HEADROOM):
    """ the shaped budget of one direction, in kbit/s: total = rate x headroom, the control lane
    comes OUT of it, so control + bulk never exceeds the total (review H3)
    :return: (total, control, bulk); control 0 = no control lane """
    if not mbit:
        return 0, 0, 0
    total = int(round(mbit * 1000 * headroom))
    control = min(max(CONTROL_MIN_KBIT, int(round(mbit * 1000 * CONTROL_SHARE))), int(total * CONTROL_CAP))
    if control < CONTROL_FLOOR_KBIT:
        control = 0
    return total, control, total - control


def rate_model(kbit):
    """ :return: (bandwidth, bandwidthMetric, label) for the core pipe model """
    if kbit % 1000 == 0:
        return str(kbit // 1000), 'Mbit', '%d Mbit/s' % (kbit // 1000)
    return str(kbit), 'Kbit', ('%.2f Mbit/s' % (kbit / 1000)).replace('.00 ', ' ')


def _num(x, default=0):
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


MODES = ('skip', 'disable', 'disable-bulk')


def shapes_everything(r):
    """ a hand-made rule that sends ALL traffic of its direction to its pipe (Shaper Insight's bulk
    rule would compete with it); narrower rules (ICMPv6 control lanes, ports, DSCP, addresses)
    have another purpose and can stay on with "disable-bulk" (verification 3, N6) """
    def blank(k):
        return (r.get(k) or '').strip().lower() in ('', 'any', '0')
    return all([
        (r.get('proto') or 'ip').lower() in ('ip', 'any', 'all'),
        blank('source'), blank('destination'), blank('src_port'), blank('dst_port'), blank('dscp'), blank('iplen'),
        (r.get('source_not') or '0') == '0',
        (r.get('destination_not') or '0') == '0',
    ])


def build(det, snap, down_mbit=0, up_mbit=0, priorities=False, rates=None, mode='skip'):
    """ :param det: lib.detect.detect() result
    :param snap: lib.snapshot() result
    :param down_mbit/up_mbit: the rate for a link without its own (link_choices()); 0 = not measured
    :param priorities: the user picked device/app priorities (later slice); without them every
        device and flow is treated alike ("fair by default") and DSCP marks are ignored
    :param rates: {physical port: {'down', 'up', 'line'}}, each link's own speeds and line type
        (a failover LTE line is not the fibre, topology test F2)
    :param mode: 'skip' = a link with your own shaper rules is left out ("Keep mine"), 'disable'
        = all of them are switched off, 'disable-bulk' = only those that shape all traffic
    """
    mode = mode if mode in MODES else 'skip'
    rates = rates or {}
    cfg, kern = snap['config'], snap['kernel']
    used_pipe_numbers = {_num(p.get('number')) for p in cfg['pipes']} | {int(k) for k in kern['dnctl']['pipes']}
    used_queue_numbers = {_num(q.get('number')) for q in cfg['queues']} | {int(k) for k in kern['dnctl']['queues']}
    cake_available = bool(snap.get('oscake') is not None)
    target_dir = {}
    for r in cfg['rules']:
        target_dir.setdefault(r.get('interface'), []).append(r)

    def next_free(used):
        n = FIRST_PIPE
        while n in used:
            n += 1
        used.add(n)
        return n

    # Shaper Insight's own objects from an earlier apply: matched by origin + name (rules also by
    # interface and direction), they are CHANGED in place, never added a second time
    own_pipes = {norm(p.get('description')): p for p in cfg['pipes'] if p.get('origin') in OURS}
    own_queues = {norm(q.get('description')): q for q in cfg['queues'] if q.get('origin') in OURS}
    own_rules = {(norm(r.get('description')), r.get('interface'), r.get('direction')): r
                 for r in cfg['rules'] if r.get('origin') in OURS}
    own_pipe_uuids = {p['uuid'] for p in own_pipes.values()}
    own_cake = {norm(c.get('description')): c for c in cfg.get('cake', [])
                if norm(c.get('description')).startswith('[Insight] ') and c.get('pipe') in own_pipe_uuids}
    max_seq = max([_num(r.get('sequence')) for r in cfg['rules'] if r.get('origin') not in OURS] + [0])

    def reuse(objs_index, key, number_used):
        """ :return: (existing or None, number) """
        ex = objs_index.get(key)
        if ex is not None and _num(ex.get('number')):
            return ex, _num(ex.get('number'))
        return None, next_free(number_used)

    seq = max_seq + 100      # after every hand-made rule, so the user's rules keep winning
    links, conflicts, rate_notes = [], [], []
    paths = {i['key']: i['path'] for i in det.get('interfaces', [])}
    vlan_rows = {i['key'] for i in det.get('interfaces', []) if i.get('vlan')}
    kept_uuids = set()
    for link in det['links']:
        objs = []
        steps = [d for m in link['members'] for d in paths.get(m, [])[:-1]]
        # config.xml's VLAN list (detect) first, the device name as the fallback (F8)
        vlan = any(m in vlan_rows for m in link['members']) or any(is_vlan_name(x) for x in steps)
        pppoe = any(x.startswith('pppoe') for x in steps)
        own = rates.get(link['root'], {})
        line = own.get('line') if own.get('line') in LINE_TYPES else 'auto'
        overhead, mpu, link_kw, overhead_why = overhead_for(line, vlan, pppoe)
        suggested = overhead_for('auto', vlan, pppoe)[0]
        rates_ = {'down': own.get('down') or down_mbit, 'up': own.get('up') or up_mbit}
        ratio = (rates_['down'] / rates_['up']) if rates_['down'] and rates_['up'] else 0
        existing_all = {}
        for d, arrow, direction in (('down', 'download', 'in'), ('up', 'upload', 'out')):
            measured = rates_[d]
            headroom = HEADROOM if cake_available else HEADROOM_NO_OVERHEAD
            total, control, bulk = split_rate(measured, headroom)
            if not measured:
                control = CONTROL_MIN_KBIT
            if measured and not control:
                rate_notes.append({'level': 'warn', 'text': '%s %s: no IPv6 housekeeping lane at %s.' % (
                    link['name'], arrow, rate_model(int(measured * 1000))[2]),
                    'why': 'On a line this slow even the smallest useful lane (%d kbit/s, at most %d %% of the '
                           'budget) does not fit; neighbour discovery shares the main lane instead.'
                           % (CONTROL_FLOOR_KBIT, int(CONTROL_CAP * 100)), 'example': '', 'claims': ['icmpv6-control']})
            c_bw, c_metric, c_label = rate_model(control)
            b_bw, b_metric, b_label = rate_model(bulk) if bulk else ('0', 'Mbit', 'to be measured')
            cname = 'Insight %s control %s' % (arrow, link['name'])
            bname = 'Insight %s %s' % (arrow, link['name'])
            ex_cp, cpipe = reuse(own_pipes, cname, used_pipe_numbers)
            ex_bp, bpipe = reuse(own_pipes, bname, used_pipe_numbers)
            ex_cq, cq = reuse(own_queues, cname, used_queue_numbers)
            ex_bq, bq = reuse(own_queues, bname, used_queue_numbers)
            existing_all.update({'pipe:%d' % cpipe: ex_cp, 'pipe:%d' % bpipe: ex_bp, 'queue:%d' % cq: ex_cq,
                                 'queue:%d' % bq: ex_bq})
            lane = [
                {'kind': 'pipe', 'number': cpipe, 'description': cname, 'ref': 'pipe:%d' % cpipe,
                 'model': {'number': str(cpipe), 'enabled': '1', 'bandwidth': c_bw, 'bandwidthMetric': c_metric,
                           'scheduler': '', 'mask': 'none', 'description': cname, 'origin': ORIGIN},
                 'fields': {'bandwidth': c_label, 'scheduler': 'WF2Q+ (default)', 'mask': 'none'},
                 'why': 'A small lane for IPv6 housekeeping (neighbour discovery, router adverts), so a full link '
                        'never starves them. %s = the larger of 1 Mbit/s and 1 %% of the link (OPNsense docs, '
                        'control-plane how-to), at most %d %% of the shaped rate, and taken out of it, so both '
                        'lanes together stay under the line rate. WF2Q+, not FQ-CoDel: a lane this small has '
                        'no competing flows to keep apart.' % (c_label, int(CONTROL_CAP * 100)),
                 'claims': ['icmpv6-control'], 'contested': True}]
            if measured:
                minus = (', minus the %s control lane' % c_label) if control else ''
                no_cake = '' if cake_available else (' (92 %, not 95 %: without CAKE nothing counts the '
                                                     'per-packet overhead)')
                b_why = ('%s measured x %d %% = %s%s: shaping a little below the line rate keeps the queue on '
                         'this firewall, where it is managed%s.'
                         % (rate_model(int(measured * 1000))[2], round(headroom * 100), rate_model(total)[2],
                            minus, no_cake))
            else:
                b_why = 'Run the speed measurement first; the rate is %d %% of what it measures.' % round(headroom * 100)
            objs += [
                {'kind': 'pipe', 'number': bpipe, 'description': bname, 'ref': 'pipe:%d' % bpipe,
                 'model': {'number': str(bpipe), 'enabled': '1', 'bandwidth': b_bw, 'bandwidthMetric': b_metric,
                           'scheduler': 'fq_codel', 'mask': 'none', 'codel_ecn_enable': '1', 'description': bname,
                           'origin': ORIGIN},
                 'fields': {'bandwidth': b_label,
                            'scheduler': 'fq_codel (CAKE via os-cake)' if cake_available else 'fq_codel',
                            'mask': 'none', 'codel_ecn_enable': '1'},
                 'why': b_why,
                 'claims': ['headroom-percent', 'bottleneck']},
                {'kind': 'queue', 'number': bq, 'description': bname, 'fields': {'pipe': bpipe, 'weight': 100},
                 'ref': 'queue:%d' % bq, 'pipe_ref': 'pipe:%d' % bpipe,
                 'model': {'number': str(bq), 'enabled': '1', 'weight': '100', 'mask': 'none', 'description': bname,
                           'origin': ORIGIN},
                 'why': ('os-cake: rules that target a pipe directly bypass CAKE; they must target a queue.'
                         if cake_available else 'Rules point at a queue, not at the pipe itself.'),
                 'claims': ['queue-not-pipe'] if cake_available else []},
            ]
            lane.append({'kind': 'queue', 'number': cq, 'description': cname, 'fields': {'pipe': cpipe, 'weight': 100},
                         'ref': 'queue:%d' % cq, 'pipe_ref': 'pipe:%d' % cpipe,
                         'model': {'number': str(cq), 'enabled': '1', 'weight': '100', 'mask': 'none',
                                   'description': cname, 'origin': ORIGIN},
                         'why': 'Rules point at a queue, not at the pipe itself.', 'claims': ['queue-not-pipe']})
            if control:
                objs = objs[:-2] + lane[:1] + objs[-2:-1] + lane[1:] + objs[-1:]
            for member in link['members'] if control else []:
                objs.append({'kind': 'rule', 'sequence': seq, 'description': cname, 'target_ref': 'queue:%d' % cq,
                             'model': {'enabled': '1', 'sequence': str(seq), 'interface': member, 'direction': direction,
                                       'proto': 'ipv6-icmp', 'source': 'any', 'destination': 'any',
                                       'description': cname, 'origin': ORIGIN},
                             'fields': {'interface': member, 'direction': direction, 'proto': 'ipv6-icmp',
                                        'target': 'queue %d' % cq},
                             'why': 'Before the bulk rule, because the first matching rule wins.', 'claims': ['first-match']})
                seq += 1
            for member in link['members']:
                objs.append({'kind': 'rule', 'sequence': seq, 'description': bname, 'target_ref': 'queue:%d' % bq,
                             'model': {'enabled': '1', 'sequence': str(seq), 'interface': member, 'direction': direction,
                                       'proto': 'ip', 'source': 'any', 'destination': 'any',
                                       'description': bname, 'origin': ORIGIN},
                             'fields': {'interface': member, 'direction': direction, 'proto': 'ip',
                                        'target': 'queue %d' % bq},
                             'claims': ['one-limit-per-cable'],
                             'why': 'Every WAN on this cable feeds the same pipe, so together they never exceed '
                                    'the one shared limit.'})
                seq += 1
            if cake_available:
                ack = d == 'up' and ratio >= ACK_FILTER_RATIO
                if d == 'down':
                    ack_why = 'ACK filter off: it only helps the upload direction.'
                elif ratio:
                    ack_why = ('ACK filter %s: the link is %.0f:1 download to upload, %s the %d:1 where dropping '
                               'redundant ACKs starts to pay off (threshold contested: 10, 15 or 30:1).'
                               % ('on' if ack else 'off', ratio, 'at or above' if ack else 'below', ACK_FILTER_RATIO))
                else:
                    ack_why = 'ACK filter off until both speeds are known (it pays off on strongly asymmetric links).'
                if priorities:
                    ds, ds_why = 'diffserv4', ('diffserv4: four priority classes, because you set priorities; '
                                               'CAKE then follows the DSCP marks your priority rules set.')
                else:
                    ds, ds_why = 'besteffort', ('besteffort: one class, every address and flow treated alike '
                                                '("fair by default"). Marks from the internet are not trusted.')
                # CAKE picks the tin before it washes (lab, TESTING.md slice 6), so with priorities
                # wash both ways too: the tins still follow the marks, the next hop never sees them
                wash = d == 'down' or priorities
                wash_why = ('wash on: DSCP marks are cleared after CAKE has sorted the packet, so nobody '
                            'outside picks your priorities and your marks do not leave the line.') if wash else ''
                iso_why = ('{{term:host-isolation}} (dual-%s): each address gets a fair share first (%s), then its '
                           'flows share that.' % ('dsthost' if d == 'down' else 'srchost', PER_ADDRESS))
                objs.append({'kind': 'cake', 'pipe': bpipe, 'description': '[Insight] ' + bname, 'pipe_ref': 'pipe:%d' % bpipe,
                             'model': {'enabled': '1', 'diffserv': ds, 'isolation': 'dual-dsthost' if d == 'down'
                                       else 'dual-srchost', 'ecn': '1', 'ack_filter': 'ack-filter' if ack else '',
                                       'wash': '1' if wash else '0', 'ingress': '0', 'link': link_kw,
                                       'overhead': str(overhead), 'mpu': mpu, 'description': '[Insight] ' + bname},
                             'fields': dict({'diffserv': ds, 'isolation': 'dual-dsthost' if d == 'down' else 'dual-srchost',
                                             'wash': '1' if wash else '0', 'ecn': '1', 'ack_filter': '1' if ack else '0',
                                             'link': link_kw, 'overhead': overhead}, **({'mpu': int(mpu)} if mpu else {})),
                             'why': ' '.join([ds_why, wash_why, iso_why, ack_why, overhead_why]).replace('  ', ' '),
                             'claims': ['diffserv-mode'] + (['tins-capped'] if priorities else []) + (['wash-download'] if wash else []) + ['host-isolation', 'host-isolation-per-address', 'ack-filter', 'overhead-preset'],
                             'contested': d == 'up' and bool(ratio)})
        for o in objs:
            if o['kind'] in ('pipe', 'queue'):
                ex = existing_all.get(o['ref'])
            elif o['kind'] == 'rule':
                ex = own_rules.get((o['description'], o['model']['interface'], o['model']['direction']))
                if ex is not None:
                    # keep its place in the rule order
                    o['model']['sequence'] = ex.get('sequence')
                    o['sequence'] = _num(ex.get('sequence'))
            else:
                ex = own_cake.get(o['description'])
            if ex is None:
                o['action'] = 'add'
                continue
            o['uuid'] = ex['uuid']
            changed = {k: v for k, v in o['model'].items()
                       if k not in ('pipe', 'target') and str(ex.get(k, '')) != str(v)}
            o['action'] = 'change' if changed else 'same'
            o['changes'] = {k: [ex.get(k, ''), v] for k, v in changed.items()}
        for member in link['members']:
            for r in target_dir.get(member, []):
                if r.get('enabled') == '1' and r.get('origin') not in OURS:
                    off = mode == 'disable' or (mode == 'disable-bulk' and shapes_everything(r))
                    conflicts.append({
                        'level': 'bad', 'link': link['name'], 'uuid': r['uuid'],
                        'action': 'disable' if off else 'keep', 'bulk': shapes_everything(r),
                        'text': 'Your rule %s "%s" already shapes %s %s traffic.' % (
                            r.get('sequence'), r.get('description') or '', member, r.get('direction') or 'both'),
                        'why': 'Two shapers on one link fight each other, and the first matching rule wins: your '
                               'rule sits earlier, so the new rules would never see that traffic.',
                        'claims': ['first-match'],
                        'choices': ['Keep mine and skip this link', 'Disable only my rules that shape all traffic',
                                    'Disable all mine (reversible) and use Shaper Insight']})
        # "Keep mine": the plan shows what Apply really does, so a link with your own shaper
        # rules gets nothing, and Shaper Insight's objects already there stay as they are (F9)
        skipped = mode == 'skip' and any(c['link'] == link['name'] for c in conflicts)
        if skipped:
            kept_uuids.update(o['uuid'] for o in objs if o.get('uuid'))
            objs = []
        counts = {}
        for o in objs:
            counts[o['kind']] = counts.get(o['kind'], 0) + 1
            counts[o['action']] = counts.get(o['action'], 0) + 1
        links.append({'name': link['name'], 'root': link['root'], 'members': link['members'], 'why': link['why'],
                      'objects': objs, 'counts': counts, 'skipped': skipped,
                      'rates': {'down': rates_['down'], 'up': rates_['up'], 'line': line,
                                'line_label': LINE_TYPES[line][0], 'overhead': overhead,
                                'overhead_suggested': suggested, 'own': bool(own.get('down') or own.get('up'))}})

    # Shaper Insight's own objects that no planned object matched (a WAN that left a link, a link
    # that is gone): removed by the same apply, still covered by the section-scoped revert
    matched = {o['uuid'] for lk in links for o in lk['objects'] if o.get('uuid')} | kept_uuids
    remove = []
    for kind, pool in (('cake', own_cake.values()), ('rule', [r for r in cfg['rules'] if r.get('origin') in OURS]),
                       ('queue', own_queues.values()), ('pipe', own_pipes.values())):
        for ex in pool:
            if ex['uuid'] not in matched:
                remove.append({'kind': kind, 'uuid': ex['uuid'], 'action': 'remove', 'description': ex.get('description', ''),
                               'number': _num(ex.get('number')), 'sequence': _num(ex.get('sequence')),
                               'pipe': next((_num(p.get('number')) for p in own_pipes.values() if p['uuid'] == ex.get('pipe')), 0),
                               'fields': {k: ex.get(k) for k in ('interface', 'direction', 'bandwidth') if ex.get(k)},
                               'why': 'Made by an earlier apply, and the new plan has no place for it (for example its '
                                      'WAN left the link), so it would shape traffic nobody planned.'})
    sysctl = kern.get('sysctl', {})
    preflight = []
    # only when a gateway rule (policy routing) sends traffic out of a shaped link: without one
    # the setting changes nothing here (F10); a gateway nobody knows counts, to stay careful
    shaped = {m for lk in links if not lk['skipped'] for m in lk['members']}
    gw_rules = [r for r in det.get('gateway_rules', []) if shaped & set(r['interfaces']) or '' in r['interfaces']]
    for name in ('net.pf.share_forward', 'net.pf.share_forward6'):
        if str(sysctl.get(name, '1')) == '0' and gw_rules:
            preflight.append({'level': 'warn', 'text': '%s is 0, and %d gateway rule%s send%s traffic out of a shaped '
                              'link (%s).' % (name, len(gw_rules), '' if len(gw_rules) == 1 else 's',
                                              's' if len(gw_rules) == 1 else '',
                                              ', '.join('"%s" via %s' % (r['descr'] or '-', r['gateway'])
                                                        for r in gw_rules[:3])),
                              'why': 'Traffic sent out by a gateway rule (policy routing) then skips the shaper.',
                              'claims': ['shared-forwarding'],
                              'example': 'Firewall: Settings: Advanced, "Shared forwarding" on.'})
    if _num(sysctl.get('kern.hz'), 1000) < 1000:
        preflight.append({'level': 'warn', 'text': 'kern.hz is %s.' % sysctl.get('kern.hz'),
                          'why': 'The shaper releases packets on timer ticks; below 1000 it releases them in bursts.',
                          'claims': ['kern-hz'],
                          'example': 'System: Settings: Tunables, kern.hz = 1000, then reboot.'})
    if not cake_available:
        preflight.append({'level': 'info', 'text': 'os-cake is not installed, so the plan uses FQ-CoDel.',
                          'why': 'FQ-CoDel also fixes bufferbloat, and shares the line fairly between connections. '
                                 'It does not share between devices: a device with many connections gets more. '
                                 'CAKE adds a fair share per address (%s) and overhead compensation.' % PER_ADDRESS,
                          'example': '',
                          'claims': ['fq-fallback', 'fq-codel-per-flow']})
    preflight += rate_notes
    for lk in links:
        if not lk['skipped'] and not (lk['rates']['down'] and lk['rates']['up']):
            preflight.append({'level': 'bad', 'text': '%s (%s): both speeds are needed.' % (lk['name'], ', '.join(lk['members'])),
                              'why': 'The shaper is set a little below each link\'s own speed; a link without both '
                                     'speeds cannot be set up (Apply refuses).', 'example': 'Step 2, the speeds of this link.',
                              'claims': ['bottleneck']})
    # hand-made shaper rules on other interfaces (LAN out shapes download too): not a blocking
    # conflict, because they may only carry local traffic, but the line could be shaped twice
    link_members = {m for lk in det['links'] for m in lk['members']}
    for r in cfg['rules']:
        if r.get('enabled') == '1' and r.get('origin') not in OURS and r.get('interface') not in link_members:
            preflight.append({'level': 'warn', 'text': 'Your shaper rule %s "%s" on %s stays as it is.' % (
                r.get('sequence'), r.get('description') or '', r.get('interface')),
                'why': 'If it carries internet traffic (a LAN rule shapes downloads too), that traffic passes two '
                       'shapers and the slower one decides. Check that it only covers local traffic, or disable it.',
                'example': 'Firewall: Shaper: Rules', 'claims': ['one-limit-per-cable']})
    if not det['links']:
        preflight.append({'level': 'bad', 'text': 'No internet link is ticked, so there is nothing to plan.',
                          'why': 'Detection ticks an interface with an upstream gateway (marked "Upstream gateway", or '
                                 'from DHCP, PPPoE or DHCPv6), the default route or an address from upstream.',
                          'example': 'Step 1: tick the interface that leads to the internet.'})
    total = {}
    for lk in links:
        for k, v in lk['counts'].items():
            total[k] = total.get(k, 0) + v
    total['remove'] = len(remove)
    summary = []
    for lk in links:
        if lk['skipped']:
            summary.append({'text': '%s (%s): skipped. Your own shaper rules on it stay as they are, because you chose '
                                    '"Keep mine".' % (lk['name'], ', '.join(lk['members'])), 'claims': ['first-match']})
            continue
        lanes = any(o['kind'] == 'pipe' and ' control ' in o['description'] for o in lk['objects'])
        fair = ('Each address gets a fair share of each direction (%s)' % PER_ADDRESS if cake_available else
                'Connections share each direction fairly (FQ-CoDel: per connection, not per device, so a device '
                'with many connections gets more; os-cake adds a fair share per address)')
        summary.append({'text': '%s (%s): one shaper for download and one for upload%s. %s%s.' % (
            lk['name'], ', '.join(lk['members']),
            ', each with a small lane for IPv6 housekeeping' if lanes else '', fair,
            ', and the priorities you set come first (a priority is per device: picked by MAC it covers all '
            'of the device\'s addresses)' if priorities and cake_available else ''),
            'claims': ['host-isolation', 'host-isolation-per-address'] if cake_available else ['fq-codel-per-flow']})
    if priorities and not cake_available:
        summary.append({'text': 'Your priorities are kept but have no effect: FQ-CoDel has no priority classes; '
                                'they need os-cake (CAKE tins).', 'claims': ['fq-no-weights']})
    apply_steps = [
        {'step': 'Save a restore point', 'why': 'A copy of the shaper and CAKE settings only, so going back never '
         'undoes anything else changed meanwhile. A full backup is kept too, as a manual safety net.'},
        {'step': 'Write the objects and apply the shaper', 'why': 'Pipes, queues, rules, then CAKE entries, through '
         'the same reload the shaper page uses. Each shaper reload leaves traffic unshaped (not cut) for about a '
         'second; Apply reloads once, a revert or Remove once more.'},
        {'step': 'Compare the kernel with the configuration', 'claims': ['apply-no-flush', 'verify-after-apply'],
         'why': 'Apply never removes old objects, so its exit '
         'code proves nothing; the kernel listing does.'},
        {'step': 'Remove stale objects', 'claims': ['apply-no-flush'], 'why': 'If deleted or disabled objects are still loaded, flush and reload '
         'once. Traffic is unshaped (not cut) for about a second while it reloads.'},
    ] + ([
        {'step': 'Check that CAKE is running', 'why': 'The kernel must report "type CAKE" on each new pipe.',
         'claims': ['cake-active']}] if cake_available else []) + [
        {'step': 'Start the 60 s confirm countdown', 'why': 'If you do not confirm, for example because the change '
         'cut you off, the firewall puts the shaper settings back by itself.'},
    ]
    # say exactly what happens to each of your rules in the chosen mode (verification 3, N6)
    for c in conflicts:
        if mode == 'skip':
            continue
        if c['action'] == 'disable':
            c['text'] += ' Apply switches it off (the timer or Remove switches it back on).'
        else:
            c['level'] = 'warn'
            c['text'] += ' It stays on: it shapes only part of the traffic.'
            c['why'] = ('It matches only some packets (a protocol such as ICMPv6, ports, addresses or DSCP), so it '
                        'stays on and, sitting earlier, keeps sending those packets to your own pipe; everything '
                        'else goes to Shaper Insight\'s shaper.')
    disable_rules = [{'uuid': c['uuid'], 'text': c['text'].split(' already shapes')[0].replace('Your rule ', ''),
                      'link': c['link']} for c in conflicts if c.get('action') == 'disable']
    planned = [lk for lk in links if not lk['skipped']]
    ready = bool(planned) and all(lk['rates']['down'] and lk['rates']['up'] for lk in planned)
    if det.get('error') and remove:
        # a link may have dropped out only because detection degraded (no runtime gateway list):
        # never remove on that basis; the digest cannot catch it, preview and apply see the same (S8)
        preflight.append({'level': 'bad', 'text': 'Detection is incomplete (%s), so nothing is removed until it '
                                                  'works again.' % det['error'],
                          'why': 'The plan would remove %d object%s of an earlier apply whose link it no longer sees; '
                                 'with detection degraded that link may still be there.'
                                 % (len(remove), '' if len(remove) == 1 else 's'),
                          'example': 'Check again in a minute; the system log names the failing tool.'})
        ready = False
    plan = {'links': links, 'remove': remove, 'conflicts': conflicts, 'summary': summary, 'apply_steps': apply_steps,
            'priorities': priorities, 'preflight': preflight, 'totals': total,
            'rates': {'down': down_mbit, 'up': up_mbit}, 'cake_available': cake_available,
            'mode': mode, 'ready': ready, 'disable_rules': disable_rules, 'line_types': {k: v[0] for k, v in LINE_TYPES.items()},
            'nothing': ('nothing to apply while you keep your own rules' if links and not planned else
                        ('no internet link selected' if not links else '')),
            'dry_run': True}
    plan['digest'] = plan_digest(plan)
    return plan


def plan_digest(plan):
    """ sha256 over what Apply would write: every object (kind, action, uuid, model, refs), the
    removals and the conflicting rules. The preview shows it, Apply recomputes it and refuses on a
    mismatch, so Apply never writes something nobody previewed (review M8) """
    import hashlib
    import json
    keep = ('kind', 'action', 'uuid', 'model', 'ref', 'pipe_ref', 'target_ref')
    body = {'objects': [{k: o.get(k) for k in keep} for lk in plan['links'] for o in lk['objects']],
            'remove': sorted((o['kind'], o['uuid']) for o in plan['remove']),
            'conflicts': sorted((c['link'], c['uuid']) for c in plan['conflicts']),
            'disable': sorted(c['uuid'] for c in plan['conflicts'] if c.get('action') == 'disable')}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def link_choices(path='/conf/config.xml'):
    """ the user's saved choices (model links): which rows are internet links, and each link's
    speeds and line type, by physical port
    :return: {'select': {interface key: bool}, 'rates': {port: {'down', 'up', 'line'}}} """
    import xml.etree.ElementTree as ET
    out = {'select': {}, 'rates': {}}
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return out
    for c in root.findall('./OPNsense/ShaperInsight/links/choice'):
        if c.findtext('include') in ('yes', 'no') and c.findtext('interface'):
            out['select'][c.findtext('interface')] = c.findtext('include') == 'yes'
    for r in root.findall('./OPNsense/ShaperInsight/links/link'):
        if r.findtext('port'):
            out['rates'][r.findtext('port')] = {'down': _num(r.findtext('download')), 'up': _num(r.findtext('upload')),
                                                'line': r.findtext('line') or 'auto'}
    return out


def priorities_set(path='/conf/config.xml'):
    """ True when the priority picker has an enabled entry: the plan then sorts by DSCP """
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return False
    return any(p.findtext('enabled') == '1'
               for p in root.findall('./OPNsense/ShaperInsight/priorities/priority'))
