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
Build the plugin's glossary.json from the sourced research glossary (markdown).

    tools/glossary/build.py <glossary.md> <glossary.json>

Part 1 entries (**t:id ...** with Say / People say / Applies / not / Example / Source bullets)
become terms grouped by their G1-G8 heading; Part 2's table becomes the mix-up list. Terms
already in glossary.json (short UI definitions written for the chips) keep their text; the
research fields are added beside it. Every quote and URL is copied verbatim.
"""
import json
import re
import sys

FIELDS = {'Say': 'say', 'People say': 'people', 'Applies / not': 'applies', 'Example': 'example', 'Source': 'source_text'}


ACRONYMS = {'qos', 'dscp', 'aqm', 'ecn', 'fifo', 'icmp', 'rpm', 'mpu', 'ipdv', 'phbs', 'pcp', 'wmm', 'nat', 'tos', 'ip',
            'pie', 'cake', 'l4s'}
SPECIAL = {'8021p': '802.1p', '80211e': '802.11e', 'codel': 'CoDel', 'fq': 'FQ', 'vs': 'vs', 'pfsense': 'pfSense',
           'mikrotik': 'MikroTik', 'phbs': 'PHBs'}


NAMES = {'priority-strict': 'Priority (strict)', 'weight-share': 'Weight (share)', 'tin-class': 'Tin (class)',
         'latency-delay': 'Latency, delay', 'jitter-ipdv': 'Jitter (IPDV)', 'responsiveness-rpm': 'Responsiveness (RPM)',
         'ingress-egress': 'Ingress, egress', 'overhead-mpu-framing': 'Overhead, MPU, framing',
         'dummynet-pipe-queue-rule': 'Dummynet pipe, queue, rule', 'limiter-pfsense': 'Limiter (pfSense)',
         'queue-tree-mikrotik': 'Queue tree (MikroTik)', 'ip-precedence-tos': 'IP precedence, TOS',
         'pcp-8021p': 'PCP (802.1p)', 'wmm-80211e': 'WMM (802.11e)', 'fifo-tail-drop': 'FIFO, tail drop',
         'line-vs-sync-rate': 'Line rate vs sync rate', 'capacity-vs-bandwidth': 'Capacity vs bandwidth',
         'buffer-vs-queue': 'Buffer vs queue', 'loss-vs-drop': 'Loss vs drop', 'ping-vs-icmp': 'Ping vs ICMP',
         'wf2q': 'WF2Q+', 'qfq': 'QFQ', 'drr': 'Deficit Round Robin (DRR)', 'fq-pie': 'FQ-PIE'}


def display(tid):
    return ' '.join(SPECIAL.get(w, w.upper() if w in ACRONYMS else w) for w in tid.split('-')).replace('FQ CoDel', 'FQ-CoDel')


def parse(md):
    groups, terms, mixups = [], {}, []
    group = None
    term = None
    field = None
    part = 0
    for raw in md.splitlines():
        line = raw.rstrip()
        if line.startswith('## Part '):
            part = int(re.match(r'## Part (\d+)', line).group(1))
            continue
        if part == 1:
            m = re.match(r'^### (G\d)\. (.+)$', line)
            if m:
                group = {'id': m.group(1), 'title': m.group(2)}
                groups.append(group)
                continue
            m = re.match(r'^\*\*t:([a-z0-9-]+)((?: / t:[a-z0-9-]+)*)(?: \(([^)]*)\))?\*\*\s*(?:\(([^)]*)\))?', line)
            if m:
                tid = m.group(1)
                also = re.findall(r't:([a-z0-9-]+)', m.group(2) or '')
                name = m.group(3) or NAMES.get(tid) or ', '.join(display(x) for x in [tid] + also)
                # a header may name a second term ("**t:ecn** (RFC 3168) versus **t:l4s** (RFC 9330)")
                rest = re.sub(r'\*\*t:([a-z0-9-]+)\*\*', lambda x: display(x.group(1)), line[m.end():]).strip()
                note = (m.group(4) or '') if not rest else (('(' + m.group(4) + ') ' if m.group(4) else '') + rest)
                term = {'id': tid, 'name': name[0].upper() + name[1:], 'group': group['id'] if group else '',
                        'note': note.strip(), 'also': also}
                terms[tid] = term
                field = None
                continue
            m = re.match(r'^- ([A-Za-z /]+?)(?: \(([^)]*)\))?: (.*)$', line)
            if term is not None and m and m.group(1) in FIELDS:
                field = FIELDS[m.group(1)]
                term[field] = m.group(3).strip()
                if field == 'source_text' and m.group(2):
                    term['source_note'] = m.group(2)
                continue
            m = re.match(r'^- ([A-Z]{2,3}(?: \([^)]*\))?): (.*)$', line)
            if term is not None and m:
                # sub-entries such as the PHBs (EF, AF, CS, LE)
                term.setdefault('details', []).append({'label': m.group(1), 'text': m.group(2).strip()})
                field = 'detail'
                continue
            if term is not None and field == 'detail' and line.startswith('  ') and line.strip():
                term['details'][-1]['text'] += ' ' + line.strip()
                continue
            if term is not None and field and line.startswith('  ') and line.strip():
                term[field] += ' ' + line.strip()
                continue
            if not line.strip():
                field = None
        elif part == 2:
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if len(cells) == 4 and cells[0].isdigit():
                mixups.append({'n': int(cells[0]), 'people': cells[1], 'say': cells[2],
                               'terms': re.findall(r't:([a-z0-9-]+)', cells[3])})
    for t in terms.values():
        src = t.get('source_text', '') + ' ' + ' '.join(d['text'] for d in t.get('details', []))
        urls = [u.rstrip('.,:;') for u in re.findall(r'https?://[^\s)"]+', src)]
        # an RFC cited by number only: the raw text URL the research read it from
        for n in re.findall(r'RFC (\d{3,5})', src):
            u = 'https://www.rfc-editor.org/rfc/rfc%s.txt' % n
            if u not in urls:
                urls.append(u)
        # an IETF draft cited by name: its datatracker page (the research read it there)
        for d in re.findall(r'(draft-[a-z0-9-]*[a-z])(?:-\d\d)?', src):
            u = 'https://datatracker.ietf.org/doc/%s/' % d
            if u not in urls and not any('datatracker' in x for x in urls):
                urls.append(u)
        t['urls'] = urls
    # "same URL as above": the research names a source once with its URL, later by name only
    known = {}
    for t in terms.values():
        for name, url in re.findall(r'([A-Za-z][\w.\- ]*?(?:\(\d\))?) \((https?://[^)\s]+)\)', t.get('source_text', '')):
            known.setdefault(name.strip().split(' ')[-1] if name.strip().startswith('Also') else name.strip(), url)
        if t['urls']:
            known.setdefault(t.get('source_text', '').split(':')[0].split(' (')[0].strip(), t['urls'][0])
    for t in terms.values():
        if t['urls'] or not t.get('source_text'):
            continue
        name = t['source_text'].split(':')[0].split(',')[0].split(' (')[0].strip()
        for k, url in known.items():
            if k and (k == name or k.endswith(name) or name.endswith(k)):
                t['urls'] = [url]
                break
    return groups, terms, mixups


def main(md_path, out_path):
    with open(md_path) as f:
        groups, terms, mixups = parse(f.read())
    try:
        with open(out_path) as f:
            current = json.load(f)
    except (OSError, ValueError):
        current = {}
    # keep the old flat shape for the chips (id -> entry), add the research fields
    merged = {}
    for tid, entry in current.items():
        if not tid.startswith('_'):
            merged[tid] = dict(entry, id=tid)
    for tid, t in terms.items():
        base = merged.get(tid, {})
        entry = dict(t)
        entry.update({k: v for k, v in base.items() if k in ('plain', 'mixup', 'source', 'secondary')})
        if base.get('example'):
            entry['ui_example'] = base['example']
        entry.setdefault('plain', t.get('say', ''))
        merged[tid] = entry
    for tid, e in merged.items():
        e.setdefault('group', 'G0')
    out = dict(merged)
    out['_groups'] = [{'id': 'G0', 'title': 'On this firewall: the shaper objects and CAKE words this plugin uses'}] + groups
    out['_mixups'] = mixups
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write('\n')
    print('%d terms (%d from the research glossary), %d groups, %d mix-ups' % (
        len(merged), len(terms), len(out['_groups']), len(mixups)))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
