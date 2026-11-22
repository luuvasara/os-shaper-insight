#!/usr/bin/env python3
#
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
Claim ids: every "Why?" the UI shows must point at an entry of the claims registry.

    tools/claims/build.py --check                  the ids the code uses exist in claims.json (CI)
    tools/claims/build.py --check <claims.yaml>    ... and every claims.json id exists in the registry,
                                                   and no "status: future" claim is used
    tools/claims/build.py <claims.yaml>            add the used ids that claims.json lacks, from the
                                                   registry (entries already in claims.json are kept)

The registry (claims.yaml, kept by the original authors and not part of this
repository) is the source of truth with verbatim quotes; claims.json is the
plugin's copy of the entries the UI links. Only http(s) sources are copied: a registry source
that is a local file (a lab note) becomes the grade "measured in a lab", never a path.
Exit 1 on any problem.
"""
import json
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')
SRC = os.path.join(ROOT, 'net', 'shaper-insight', 'src', 'opnsense')
CLAIMS_JSON = os.path.join(SRC, 'mvc', 'app', 'library', 'OPNsense', 'ShaperInsight', 'claims.json')
# every way the code names a claim: 'claim': 'x', entry['claim'] = 'x', 'claims': ['x', 'y'],
# SA.why('x'), data-claim="x"
PATTERNS = [
    re.compile(r"""['"]claim['"]\s*:\s*['"]([a-z0-9-]+)['"]"""),
    re.compile(r"""\['claim'\]\s*=\s*'([a-z0-9-]+)'(?!\s*,)"""),
    re.compile(r"""\['claim'\]\s*=\s*'[a-z]+',\s*'([a-z0-9-]*)'"""),
    re.compile(r"""SA\.why\(\s*(?:[^'")]*\?\s*)?['"]([a-z0-9-]+)['"]"""),
    re.compile(r"""data-claim=["']([a-z0-9-]+)["']"""),
]
# a 'claims' value runs until the next key of the same dict (or its end); every quoted id in it
CLAIMS_KEY = re.compile(r"""['"]claims['"]\s*:""")
CLAIMS_END = re.compile(r"""['"](?:why|contested|choices|example|text|step|kind|level)['"]\s*:|\}""")
ID = re.compile(r"""['"]([a-z][a-z0-9]*(?:-[a-z0-9]+)*)['"]""")
GRADE = {'measured-in-our-lab': 'measured-lab', 'cited-measurement': 'cited', 'standard': 'cited',
         'expert-opinion': 'expert', 'inference': 'inference'}


def used_ids():
    """ :return: {id: [files]} for every claim id in the plugin's python, volt and js """
    found = {}
    for base, _, files in os.walk(SRC):
        for name in files:
            if not name.endswith(('.py', '.volt', '.js')):
                continue
            path = os.path.join(base, name)
            with open(path, encoding='utf-8') as f:
                text = f.read()
            ids = set()
            for pat in PATTERNS:
                ids |= set(pat.findall(text))
            for m in CLAIMS_KEY.finditer(text):
                rest = text[m.end():m.end() + 600]
                end = CLAIMS_END.search(rest)
                ids |= set(ID.findall(rest[:end.start() if end else len(rest)]))
            ids.discard('')
            for cid in ids:
                found.setdefault(cid, []).append(os.path.relpath(path, ROOT))
    return found


def load_registry(path):
    import yaml
    with open(path, encoding='utf-8') as f:
        return {c['id']: c for c in yaml.safe_load(f)}


def to_ui(c):
    srcs = [{'url': s['url'], 'quote': s['quote'].strip(), 'locator': ''} for s in c.get('sources', [])
            if s.get('url', '').startswith(('http://', 'https://'))]
    out = {'text': ' '.join(c['text'].split()), 'grade': GRADE.get(c['evidence_level'], 'inference'),
           'mechanism': ' '.join(c['mechanism'].split()), 'sources': srcs}
    if (c.get('contested') or {}).get('value'):
        out['contested'] = 'Our sources disagree on this; the value is a starting point, not a settled number.'
    return out


def main(argv):
    check = '--check' in argv
    args = [a for a in argv[1:] if a != '--check']
    with open(CLAIMS_JSON, encoding='utf-8') as f:
        ui = json.load(f)
    used = used_ids()
    reg = load_registry(args[0]) if args else None
    problems = []
    if reg is not None and not check:
        for cid in sorted(used):
            if cid not in ui and cid in reg:
                ui[cid] = to_ui(reg[cid])
        with open(CLAIMS_JSON, 'w', encoding='utf-8') as f:
            json.dump(ui, f, indent=1, ensure_ascii=False)
            f.write('\n')
    for cid, files in sorted(used.items()):
        if cid not in ui:
            problems.append('%s: used in %s but not in claims.json' % (cid, ', '.join(sorted(set(files)))))
    if reg is not None:
        for cid in sorted(ui):
            if cid not in reg:
                problems.append('%s: in claims.json but not in the registry' % cid)
            elif reg[cid].get('status') == 'future' and cid in used:
                problems.append('%s: the registry marks it future, the UI must not claim it yet' % cid)
    for p in problems:
        print('FAIL', p)
    print('%d ids used, %d in claims.json, %d problems' % (len(used), len(ui), len(problems)))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
