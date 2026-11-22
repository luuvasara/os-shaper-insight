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
    Turn a snapshot into plain-word findings: per-pipe scheduler status (is CAKE really
    running?), the intended-versus-loaded diff and the settings that are ignored.

    Every finding carries:
      level  ok | info | warn | bad
      text   plain words, with {{term:id}} markers that the page turns into glossary chips
      claim  id of the evidence entry that explains why (claims.json), may be empty
"""
import re

# schedulers that do not use queue weights. fq_codel/fq_pie/fifo: OPNsense shaper docs
# ("FQ_CoDel ... NO" in the weights column of the control-plane how-to); CAKE: it sorts
# packets into tins by DSCP and shares per flow and host, and os-cake has no queue-to-tin
# mapping (os-cake DESIGN.md, "fwmark tin override: no"), so a weight has nothing to act on.
WEIGHTLESS = {'FQ_CODEL': 'FQ-CoDel', 'FQ_PIE': 'FQ-PIE', 'FIFO': 'FIFO', 'CAKE': 'CAKE'}
SCHED_WORDS = {'': 'WF2Q+', 'wf2q+': 'WF2Q+', 'fq_codel': 'FQ-CoDel', 'fq_pie': 'FQ-PIE', 'fifo': 'FIFO',
               'rr': 'Deficit Round Robin', 'qfq': 'QFQ', 'cake': 'CAKE'}
# scheduler word -> glossary term, so every scheduler the pages show carries a chip
SCHED_TERM = {'WF2Q+': 'wf2q', 'QFQ': 'qfq', 'Deficit Round Robin': 'drr', 'FQ-PIE': 'fq-pie', 'FIFO': 'fifo-tail-drop',
              'PRIO': 'priority-strict', 'FQ-CoDel': 'fq-codel', 'CAKE': 'cake'}
OUR_ORIGINS = ('ShaperInsight', 'ShaperAssistant')


def sched_words(word):
    """ 'WF2Q+' -> '{{term:wf2q|WF2Q+}}' (a chip with the word as shown), unknown words as they are """
    if word in SCHED_TERM:
        return '{{term:%s|%s}}' % (SCHED_TERM[word], word)
    return word or ''


def sched_why(intended, ours, want_cake, n_queues, bandwidth):
    """ why this pipe runs its scheduler, and what that does here (user feedback 2026-11-22:
    "WF2Q+ has no tooltip, nor why?") """
    small = bandwidth is not None and bandwidth <= 20000000
    if ours:
        if want_cake:
            return ('Set by Shaper Insight\'s setup: FQ-CoDel in the pipe settings, which os-cake replaces with '
                    '{{term:cake}} through the CAKE entry (fair per host, priority classes, line overhead).')
        if intended in ('WF2Q+', 'QFQ', 'Deficit Round Robin', 'FIFO'):
            return ('Set by Shaper Insight\'s setup for a small control lane: %s, not FQ-CoDel, because a lane '
                    'this small carries a few housekeeping packets and has no competing flows to keep apart.'
                    % sched_words(intended))
        return 'Set by Shaper Insight\'s setup: %s.' % sched_words(intended)
    head = 'Chosen in your pipe settings (Firewall: Shaper: Pipes, scheduler). '
    if want_cake:
        return ('Your CAKE entry (Firewall: Shaper: CAKE) replaces the pipe\'s scheduler with {{term:cake}}: fair '
                'per host, priority classes by DSCP, line overhead.')
    if intended in ('WF2Q+', 'QFQ', 'Deficit Round Robin'):
        if n_queues <= 1:
            return head + ('%s shares a pipe between its queues by weight; with %s it has nothing to share, so '
                           'packets leave in arrival order, like {{term:fifo-tail-drop|FIFO}}. %s'
                           % (sched_words(intended), 'one queue' if n_queues == 1 else 'no queue',
                              'Fine for a small lane such as an ICMPv6 control pipe, where per-flow fairness adds '
                              'nothing.' if small else
                              'On a busy internet pipe, {{term:fq-codel}} or {{term:cake}} keep the delay low; '
                              'this does not.'))
        return head + ('%s shares the pipe between its %d queues by their weights when they are all busy; '
                       'inside each queue packets leave in arrival order.' % (sched_words(intended), n_queues))
    if intended == 'FIFO':
        return head + ('{{term:fifo-tail-drop|FIFO}}: one line in arrival order, the newest packet dropped when it '
                       'is full; no fairness between flows.')
    if intended == 'FQ-CoDel':
        return head + ('{{term:fq-codel}}: each flow gets its own sub-queue, served in turn, and CoDel keeps each '
                       'short. Queue weights on this pipe are not used.')
    if intended == 'FQ-PIE':
        return head + ('{{term:fq-pie}}: each flow gets its own sub-queue, served in turn, and PIE keeps each '
                       'short. Queue weights on this pipe are not used.')
    return head.strip()


KERNEL_WORDS = {'FQ_CODEL': 'FQ-CoDel', 'FQ_PIE': 'FQ-PIE', 'FIFO': 'FIFO', 'WF2Q+': 'WF2Q+',
                'RR': 'Deficit Round Robin', 'QFQ': 'QFQ', 'CAKE': 'CAKE', 'PRIO': 'PRIO'}


def _on(item, default='1'):
    return (item.get('enabled') or default) == '1'


def _fmt_rate(bps):
    if bps is None:
        return '?'
    for unit, div in (('Gbit/s', 1e9), ('Mbit/s', 1e6), ('kbit/s', 1e3)):
        if bps >= div:
            value = bps / div
            return ('%.3g' % value if value < 100 else '%d' % round(value)) + ' ' + unit
    return '%d bit/s' % bps


def _bits(value, metric):
    units = {'bit': 1, 'kbit': 1000, 'mbit': 1000 ** 2, 'gbit': 1000 ** 3}
    try:
        return int(float(value) * units[(metric or 'bit').lower()])
    except (TypeError, ValueError, KeyError):
        return None


def pipe_label(p):
    desc = p.get('description') or ''
    return 'pipe %s%s' % (p.get('number', '?'), (' "%s"' % desc) if desc else '')


def queue_label(q):
    desc = q.get('description') or ''
    return 'queue %s%s' % (q.get('number', '?'), (' "%s"' % desc) if desc else '')


def analyse(snap):
    cfg = snap['config']
    kern = snap['kernel']
    dn = kern['dnctl']
    kpipes = {int(k): v for k, v in dn['pipes'].items()}
    kqueues = {int(k): v for k, v in dn['queues'].items()}
    krules = kern['ipfw']['rules']
    oscake = snap.get('oscake')
    kld = kern['kld']

    pipes_by_uuid = {p['uuid']: p for p in cfg['pipes']}
    cake_by_pipe = {}
    for entry in cfg['cake']:
        if _on(entry) and entry.get('pipe') in pipes_by_uuid:
            cake_by_pipe[entry['pipe']] = entry

    module_usable = bool(oscake and (oscake.get('usable') or (oscake.get('subtick') or {}).get('loaded')))
    oscake_reason = (oscake or {}).get('reason') or ''
    stats = {int(k): v for k, v in kern['cake_stats'].items()}

    findings_diff, findings_ignored, pipes_out = [], [], []

    # rules per target, to know which pipes carry traffic and how
    rules_to = {}
    for r in cfg['rules']:
        if _on(r):
            rules_to.setdefault(r.get('target'), []).append(r)

    # ---------------------------------------------------------------- pipes
    for p in sorted(cfg['pipes'], key=lambda x: int(x.get('number') or 0)):
        try:
            num = int(p.get('number') or 0)
        except ValueError:
            continue
        sched_cfg = (p.get('scheduler') or '').lower()
        want_cake = p['uuid'] in cake_by_pipe and _on(p)
        intended = 'CAKE' if want_cake else SCHED_WORDS.get(sched_cfg, sched_cfg or 'WF2Q+')
        k = kpipes.get(num)
        loaded = KERNEL_WORDS.get(k['type'], k['type']) if k else None
        queues = [q for q in cfg['queues'] if q.get('pipe') == p['uuid']]
        direct_rules = rules_to.get(p['uuid'], [])
        queue_rules = [r for q in queues if _on(q) for r in rules_to.get(q['uuid'], [])]
        entry = {
            'number': num, 'uuid': p['uuid'], 'description': p.get('description', ''),
            'enabled': _on(p), 'bandwidth_config': _bits(p.get('bandwidth'), p.get('bandwidthMetric')),
            'bandwidth_kernel': k['bandwidth'] if k else None, 'intended': intended, 'loaded': loaded,
            'kernel_params': k['params'] if k else '', 'mask': p.get('mask') or 'none',
            'cake_entry': bool(want_cake), 'rules_via_queue': len(queue_rules),
            'rules_direct': len(direct_rules), 'instances': stats.get(num, []),
            'intended_words': sched_words(intended), 'loaded_words': sched_words(loaded) if loaded else '',
            'sched_why': sched_why(intended, p.get('origin') in OUR_ORIGINS, want_cake,
                                   len([q for q in queues if _on(q)]), _bits(p.get('bandwidth'), p.get('bandwidthMetric'))),
        }
        # the plain-word verdict for this pipe
        if not _on(p):
            entry['level'], entry['text'] = 'info', 'Disabled in the configuration.'
            if k:
                entry['level'] = 'warn'
                entry['text'] += (' It is still loaded in the kernel as %s: the core shaper template '
                                  'writes disabled pipes too, and Apply does not flush.' % loaded)
                entry['claim'] = 'apply-no-flush'
        elif k is None:
            entry['level'], entry['claim'] = 'bad', 'apply-no-flush'
            entry['text'] = 'Enabled, but not loaded in the kernel: nothing is shaped by it. Apply the shaper.'
        elif want_cake and loaded == 'CAKE':
            if direct_rules and not queue_rules:
                entry['level'], entry['claim'] = 'bad', 'queue-not-pipe'
                entry['text'] = ('{{term:cake}} is loaded, but every rule points at the pipe itself, so '
                                 'traffic goes through the pipe\'s plain FIFO and CAKE never sees it.')
            elif direct_rules:
                entry['level'], entry['claim'] = 'warn', 'queue-not-pipe'
                entry['text'] = ('{{term:cake}} is running. %d rule(s) point at the pipe itself and bypass '
                                 'CAKE; %d go through a queue and are handled by CAKE.'
                                 % (len(direct_rules), len(queue_rules)))
            elif not queue_rules:
                entry['level'], entry['claim'] = 'warn', ''
                entry['text'] = '{{term:cake}} is running, but no enabled rule sends traffic to it.'
            else:
                entry['level'], entry['claim'] = 'ok', 'cake-active'
                entry['text'] = ('{{term:cake}} is really running on this pipe (the kernel reports '
                                 'scheduler type CAKE).')
        elif want_cake:
            entry['level'], entry['claim'] = 'warn', 'module-match'
            why = oscake_reason or ('the CAKE kernel module is not usable' if not module_usable
                                    else 'Apply has not run since the CAKE entry was added')
            entry['text'] = ('A CAKE entry exists, but the kernel runs %s: %s. Traffic is still shaped, '
                             'by the pipe\'s own scheduler.' % (loaded, why))
        else:
            entry['level'] = 'ok' if loaded == intended else 'warn'
            entry['claim'] = 'fq-fallback' if loaded == 'FQ-CoDel' else ''
            entry['text'] = ('Runs %s, as configured (no CAKE entry for this pipe).' % loaded
                             if loaded == intended else
                             'Configured as %s, but the kernel runs %s.' % (intended, loaded))
        pipes_out.append(entry)

        # diff rows for the pipe
        if k is not None and _on(p):
            rate_c, rate_k = entry['bandwidth_config'], entry['bandwidth_kernel']
            if rate_c and rate_k and abs(rate_c - rate_k) > max(1000, rate_c * 0.005):
                findings_diff.append({'object': pipe_label(p), 'field': 'rate (shaping limit)',
                                      'intended': _fmt_rate(rate_c), 'loaded': _fmt_rate(rate_k),
                                      'level': 'bad', 'claim': 'apply-no-flush',
                                      'text': 'The kernel limits this pipe to a different rate than the '
                                              'configuration says. Apply the shaper.'})
            else:
                findings_diff.append({'object': pipe_label(p), 'field': 'rate (shaping limit)',
                                      'intended': _fmt_rate(rate_c), 'loaded': _fmt_rate(rate_k),
                                      'level': 'ok', 'claim': '', 'text': 'Same.'})
            findings_diff.append({'object': pipe_label(p), 'field': 'scheduler',
                                  'intended': intended, 'loaded': loaded,
                                  'level': 'ok' if intended == loaded else 'warn',
                                  'claim': '' if intended == loaded else 'module-match',
                                  'text': 'Same.' if intended == loaded else entry['text']})
        elif k is not None:
            findings_diff.append({'object': pipe_label(p), 'field': 'presence',
                                  'intended': 'disabled', 'loaded': 'loaded (%s)' % loaded,
                                  'level': 'warn', 'claim': 'apply-no-flush',
                                  'text': 'Disabled in the configuration but still in the kernel.'})
        elif _on(p):
            findings_diff.append({'object': pipe_label(p), 'field': 'presence',
                                  'intended': 'enabled', 'loaded': 'missing', 'level': 'bad',
                                  'claim': 'apply-no-flush', 'text': entry['text']})

        # ignored settings on the pipe (core Shaper/dnctl.conf template logic)
        if sched_cfg == 'fq_codel' and p.get('codel_enable') == '1':
            findings_ignored.append({
                'object': pipe_label(p), 'setting': 'Enable CoDel', 'value': 'on', 'level': 'info',
                'claim': 'template-codel-fq',
                'text': 'Ignored: FQ-CoDel already contains CoDel. The shaper template only writes the '
                        '"codel" keyword for other schedulers.'})
        if p.get('pie_enable') == '1' and (p.get('codel_enable') == '1' or sched_cfg in ('fq_codel', 'fq_pie')):
            findings_ignored.append({
                'object': pipe_label(p), 'setting': 'Enable PIE', 'value': 'on', 'level': 'info',
                'claim': 'template-pie',
                'text': 'Ignored: the template writes PIE only when neither CoDel nor FQ-CoDel/FQ-PIE is '
                        'chosen.'})
        if sched_cfg != 'fq_codel' and any(p.get(f) for f in ('fqcodel_quantum', 'fqcodel_limit', 'fqcodel_flows')):
            findings_ignored.append({
                'object': pipe_label(p), 'setting': 'FQ-CoDel quantum / limit / flows',
                'value': ', '.join(p.get(f) for f in ('fqcodel_quantum', 'fqcodel_limit', 'fqcodel_flows') if p.get(f)),
                'level': 'info', 'claim': 'template-fq-params',
                'text': 'Ignored: these are written only when the scheduler is FQ-CoDel.'})
        if want_cake and (p.get('mask') or 'none') != 'none':
            findings_ignored.append({
                'object': pipe_label(p), 'setting': 'CAKE entry', 'value': 'mask %s' % p.get('mask'),
                'level': 'bad', 'claim': 'mask-none',
                'text': 'The CAKE entry is skipped: a pipe with a mask gets one scheduler instance per '
                        'host, so os-cake does not use CAKE on it. Set the mask to none; CAKE separates '
                        'hosts by itself.'})
        # weights under a scheduler that ignores them
        live_type = k['type'] if k else ('CAKE' if want_cake else (sched_cfg or '').upper())
        weighted = [q for q in queues if _on(q) and (q.get('weight') or '100') != '100']
        weights = {(q.get('weight') or '100') for q in queues if _on(q)}
        if (weighted or len(weights) > 1) and live_type in WEIGHTLESS:
            for q in weighted or queues:
                findings_ignored.append({
                    'object': queue_label(q), 'setting': 'weight', 'value': q.get('weight') or '100',
                    'level': 'warn', 'claim': 'fq-no-weights' if live_type != 'CAKE' else 'cake-no-weights',
                    'text': 'Ignored: this queue\'s pipe runs %s, which shares the line per flow%s and does '
                            'not look at queue weights. To prioritise, use separate pipes or, with CAKE, '
                            'DSCP marks ({{term:tin}}s).'
                            % (WEIGHTLESS[live_type], ' and per address (usually one per device)' if live_type == 'CAKE' else '')})

    # ---------------------------------------------------------------- queues
    for q in sorted(cfg['queues'], key=lambda x: int(x.get('number') or 0)):
        try:
            num = int(q.get('number') or 0)
        except ValueError:
            continue
        kq = kqueues.get(num)
        pipe = pipes_by_uuid.get(q.get('pipe'))
        if _on(q) and kq is None:
            findings_diff.append({'object': queue_label(q), 'field': 'presence', 'intended': 'enabled',
                                  'loaded': 'missing', 'level': 'bad', 'claim': 'apply-no-flush',
                                  'text': 'Enabled but not loaded: rules that use it shape nothing.'})
        elif not _on(q) and kq is not None:
            findings_diff.append({'object': queue_label(q), 'field': 'presence', 'intended': 'disabled',
                                  'loaded': 'loaded', 'level': 'warn', 'claim': 'apply-no-flush',
                                  'text': 'Disabled in the configuration but still in the kernel.'})
        elif kq is not None:
            want_w = int(q.get('weight') or 100)
            want_pipe = int(pipe['number']) if pipe else None
            same = kq['weight'] == want_w and (want_pipe is None or kq['sched'] == want_pipe)
            findings_diff.append({'object': queue_label(q), 'field': 'pipe / weight',
                                  'intended': 'pipe %s / %d' % (want_pipe, want_w),
                                  'loaded': 'pipe %d / %d' % (kq['sched'], kq['weight']),
                                  'level': 'ok' if same else 'bad', 'claim': '' if same else 'apply-no-flush',
                                  'text': 'Same.' if same else 'The kernel differs from the configuration.'})
    known_q = {int(q['number']) for q in cfg['queues'] if (q.get('number') or '').isdigit()}
    for num in sorted(set(kqueues) - known_q):
        findings_diff.append({'object': 'queue %d' % num, 'field': 'presence', 'intended': 'not configured',
                              'loaded': 'loaded', 'level': 'warn', 'claim': 'apply-no-flush',
                              'text': 'Loaded in the kernel but not in the configuration (deleted earlier; '
                                      'Apply does not remove it).'})
    known_p = {int(p['number']) for p in cfg['pipes'] if (p.get('number') or '').isdigit()}
    for num in sorted(set(kpipes) - known_p):
        findings_diff.append({'object': 'pipe %d' % num, 'field': 'presence', 'intended': 'not configured',
                              'loaded': 'loaded', 'level': 'warn', 'claim': 'apply-no-flush',
                              'text': 'Loaded in the kernel but not in the configuration (deleted earlier; '
                                      'Apply does not remove it).'})

    # ---------------------------------------------------------------- rules
    for r in sorted(cfg['rules'], key=lambda x: int(x.get('sequence') or 0)):
        kr = krules.get(r['uuid'])
        label = 'rule %s %s/%s' % (r.get('sequence', '?'), r.get('interface', '?'), r.get('direction') or 'both')
        if r.get('description'):
            label += ' "%s"' % r['description']
        if _on(r) and kr is None:
            findings_diff.append({'object': label, 'field': 'presence', 'intended': 'enabled', 'loaded': 'missing',
                                  'level': 'bad', 'claim': 'apply-no-flush',
                                  'text': 'Enabled but no matching ipfw rule is loaded (its target or interface '
                                          'may be disabled, or Apply has not run).'})
        elif not _on(r) and kr is not None:
            findings_diff.append({'object': label, 'field': 'presence', 'intended': 'disabled', 'loaded': 'loaded',
                                  'level': 'bad', 'claim': '', 'text': 'Disabled but still loaded.'})
        elif kr is not None:
            findings_diff.append({'object': label, 'field': 'ipfw rule', 'intended': 'enabled',
                                  'loaded': 'rule %d, %s packets' % (kr['num'], format(kr['pkts'], ',')),
                                  'level': 'ok', 'claim': '', 'text': 'Loaded.'})

    # ---------------------------------------------------------------- summary
    active = [p for p in pipes_out if p['enabled']]
    cake_running = [p for p in active if p['loaded'] == 'CAKE']
    cake_wanted = [p for p in active if p['cake_entry']]
    problems = [f for f in findings_diff + findings_ignored if f['level'] in ('bad', 'warn')]
    problems += [p for p in pipes_out if p['level'] in ('bad', 'warn')]
    if not cfg['pipes']:
        summary = {'level': 'info', 'text': 'The traffic shaper has no pipes, so nothing is shaped.'}
    elif cake_wanted and len(cake_running) == len(cake_wanted):
        level = 'ok' if not problems else ('bad' if any(f['level'] == 'bad' for f in problems) else 'warn')
        rest = '.' if len(active) == len(cake_wanted) else (
            ' (%d other active pipes use their own scheduler, for example small control lanes).'
            % (len(active) - len(cake_wanted)))
        summary = {'level': level,
                   'text': '{{term:cake}} is running on all %d pipes that have a CAKE entry%s' % (len(cake_wanted), rest)}
    elif cake_wanted:
        summary = {'level': 'warn',
                   'text': '{{term:cake}} is configured on %d pipes but running on %d. The others are still '
                           'shaped by their own scheduler.' % (len(cake_wanted), len(cake_running))}
    else:
        kinds = sorted({p['loaded'] or 'not loaded' for p in active})
        summary = {'level': 'ok' if active else 'info',
                   'text': '%d active pipes, running %s. CAKE is not configured.' % (len(active), ', '.join(kinds))}
    # one object reported in two sections (a stale pipe in the pipe table and the diff) is
    # one problem: count distinct objects ("pipe 10002", "queue 10001", "rule 3 lan/in")
    objects = set()
    for f in problems:
        name = f.get('object') or ('pipe %s' % f.get('number', '?'))
        objects.add(re.match(r'(?:pipe|queue|rule) \S+(?: \S+)?', name).group(0)
                    if name.startswith('rule ') else ' '.join(name.split()[:2]))
    summary['problems'] = len(objects)

    module = {
        'installed': oscake is not None,
        'loaded': kld['dn_cake'] or kld['dummynet_st'],
        'usable': module_usable,
        'reason': oscake_reason,
        'dummynet': (oscake or {}).get('dummynet'),
        'builtin': (oscake or {}).get('builtin'),
        'warnings': (oscake or {}).get('warnings') or [],
        'modules': [m for m in kld['modules'] if m in ('dummynet.ko', 'dn_cake.ko', 'dummynet_st.ko', 'ipfw.ko')],
    }
    sysctl = kern['sysctl']
    tunables = []
    hz = sysctl.get('kern.hz')
    if hz and hz.isdigit() and int(hz) < 1000:
        tunables.append({'level': 'warn', 'claim': 'kern-hz',
                         'text': 'kern.hz is %s. The shaper releases packets once per clock tick, so timing is '
                                 'coarse; set the tunable kern.hz to 1000 and reboot.' % hz})
    for name in ('net.pf.share_forward', 'net.pf.share_forward6'):
        if sysctl.get(name) == '0':
            tunables.append({'level': 'warn', 'claim': 'shared-forwarding',
                             'text': '%s is off: traffic sent out by policy-routing firewall rules skips the '
                                     'shaper. Turn on "Shared forwarding" under Firewall: Settings: Advanced.'
                             % name})
    return {
        'summary': summary,
        'module': module,
        'pipes': pipes_out,
        'diff': findings_diff,
        'ignored': findings_ignored,
        'tunables': tunables,
        'sysctl': {k: v for k, v in sysctl.items()},
        'stats_available': kern['cake_stats_available'],
    }
