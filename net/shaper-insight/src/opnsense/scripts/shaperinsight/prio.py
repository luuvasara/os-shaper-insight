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
    Priorities: put chosen devices into a CAKE tin by marking their packets (DSCP) with ipfw,
    on the internet links whose CAKE pipes sort by DSCP (diffserv3/4/8, precedence).

      prio.py load      (re)load our ipfw rules (rule set 7, numbers 2000-2099) and tables
      prio.py refresh   update the tables only (cron, every minute): devices change addresses
      prio.py unload    remove our rules (tables stay, empty does no harm)
      prio.py purge     remove our rules AND tables (uninstall)
      prio.py status    JSON: classes, members and addresses, rules with counters
      prio.py shares    JSON: local devices (MAC) seen with several addresses right now; CAKE
                        counts each address as a host, so such a device gets several shares

    load/refresh/unload/purge exit 1 when anything failed, with the reasons in the JSON
    "errors" list; rc.ipfw logs them, the status page and the health checks show them.

    Why ipfw and DSCP, not pf rules with their own pipes (TESTING.md, slice 6 lab):
    - `setdscp` does not end rule processing, so the packet goes on to core's queue rules
      (60001+) and lands in a tin of the SAME CAKE pipe: no extra pipe, no double shaping, the
      line stays shared;
    - ipfw sees the LAN address on both sides (out: before NAT, in: after reverse NAT), so
      both directions work; pf `dnqueue` on a rule whose packets are NATed later broke NAT;
    - CAKE picks the tin before it washes, so "wash" on a pipe still hides the marks from the
      next hop without losing the priority (lab: wash on, AF41 traffic in the Video tin, DSCP 0
      on the wire).
    Incoming marks from the internet are cleared first (2000-2009), so only our rules pick a
    tin for incoming traffic; marks set by your own devices on outgoing traffic are kept.
    Rules sit at 2000-2099: after core's localhost skips (200-203), before the captive portal
    (3000+), so every packet to or from the internet link passes them.
"""
import fcntl
import ipaddress
import json
import os
import re
import sys
from lib import run, CONFIG, config_root

IPFW = '/sbin/ipfw'
SET = '7'
STAGE = '30'                # new rules are built here, disabled, then swapped in
MAX_LINKS = 10              # rule numbers: clear 2000-2009, each class 2 per link (2010-2049, 2050-2089)
LOCK = '/var/run/shaperinsight-prio.lock'
RESULT = '/var/run/shaperinsight-prio.json'   # last load/refresh outcome, for health and the page
# set by purge (uninstall): load and refresh then do nothing, so a cron tick or an ipfw reload
# between the purge and the package removal cannot bring marks and tables back (review S4);
# +POST_INSTALL and +POST_DEINSTALL remove it
REMOVING = '/var/run/shaperinsight-prio.removing'
MAX_PER_TARGET = 1024       # addresses one priority may resolve to (an alias can hold 100 000)
MAX_TOTAL = 4096            # all priorities together; beyond that the table edits get slow (S2)
LOAD_WAIT = 30              # seconds load waits for the lock (it runs inside every ipfw reload)
CLASSES = {
    # class: (DSCP, table, first rule number, tin in diffserv4, what it does)
    'background': ('cs1', 'si_prio_background', 2010, 'Bulk',
                   'yields to everything else when the line is full (about 6% guaranteed)'),
    'interactive': ('af41', 'si_prio_interactive', 2050, 'Video',
                    'low delay, priority up to half the line, then shares like everyone else'),
}
DIFFSERV_TINS = ('diffserv3', 'diffserv4', 'diffserv8', 'precedence')
MAC_RE = re.compile(r'^([0-9a-f]{2}:){5}[0-9a-f]{2}$')
ALIAS_RE = re.compile(r'^[A-Za-z0-9_]{1,32}$')
# a protocol and port (range): marks that traffic from every device, both directions
PORT_RE = re.compile(r'^(tcp|udp|tcp\+udp)/([0-9]{1,5})(?:-([0-9]{1,5}))?$')
MAX_PORT_SPECS = 30         # per class and protocol: ipfw takes the list inline in one rule


def ipfw(*args):
    return run([IPFW] + list(args), timeout=10)


def read_cfg():
    return config_root(CONFIG)      # under a shared lock, retried (Verification 4, N10)


def priorities(root):
    """ :return: [{class, target, description}] of the enabled entries """
    out = []
    for item in root.findall('./OPNsense/ShaperInsight/priorities/priority'):
        if item.findtext('enabled') == '1' and item.findtext('class') in CLASSES:
            out.append({'class': item.findtext('class'), 'target': (item.findtext('target') or '').strip(),
                        'description': item.findtext('description') or ''})
    return out


def links(root):
    """ the physical interfaces of enabled shaper rules whose pipe has an enabled CAKE entry
    that sorts by DSCP: only there does a mark change anything """
    pipes = {p.get('uuid'): p for p in root.findall('./OPNsense/TrafficShaper/pipes/pipe')}
    queues = {q.get('uuid'): q for q in root.findall('./OPNsense/TrafficShaper/queues/queue')}
    cake = {c.findtext('pipe'): c for c in root.findall('./OPNsense/Cake/pipes/pipe')
            if c.findtext('enabled') == '1' and c.findtext('diffserv') in DIFFSERV_TINS}
    result = set()
    for rule in root.findall('./OPNsense/TrafficShaper/rules/rule'):
        if rule.findtext('enabled') != '1':
            continue
        target = rule.findtext('target') or ''
        pipe = target if target in pipes else (queues[target].findtext('pipe') if target in queues else None)
        if pipe in cake:
            ifname = root.findtext('./interfaces/%s/if' % (rule.findtext('interface') or ''))
            if ifname and re.match(r'^[a-z0-9_.]+$', ifname):
                result.add(ifname)
    return sorted(result)


def neighbours():
    """ MAC -> set of addresses, from ARP, NDP and the DHCP leases (Kea, Dnsmasq) """
    found = {}

    def add(mac, addr):
        mac = (mac or '').lower()
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return
        if MAC_RE.match(mac) and not ip.is_link_local and not ip.is_loopback:
            found.setdefault(mac, set()).add(str(ip))
    _, out, _ = run(['/usr/sbin/arp', '-an'])
    for line in out.splitlines():
        m = re.search(r'\(([0-9.]+)\) at ([0-9a-f:]{17})', line)
        if m:
            add(m.group(2), m.group(1))
    _, out, _ = run(['/usr/sbin/ndp', '-an'])
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) > 1:
            add(parts[1], parts[0].split('%')[0])
    # core's own lease scripts, called directly: this also runs from inside a configd action
    # (ipfw reload -> rc.ipfw -> here), where a nested configctl call came back empty
    core = '/usr/local/opnsense/scripts/'
    for argv in ([core + 'kea/get_kea_leases.py', '--proto', 'inet'],
                 [core + 'kea/get_kea_leases.py', '--proto', 'inet6'],
                 [core + 'dnsmasq/get_dnsmasq_leases.py']):
        if not os.path.exists(argv[0]):
            continue
        _, out, _ = run(argv, timeout=15)
        try:
            for lease in json.loads(out or '{}').get('records', []):
                add(lease.get('hwaddr'), lease.get('address'))
        except ValueError:
            pass
    return found


def port_spec(target):
    """ 'tcp+udp/6881-6889' -> (['tcp', 'udp'], '6881-6889'), or None for any other target """
    m = PORT_RE.match(target.lower())
    if not m:
        return None
    lo, hi = int(m.group(2)), int(m.group(3) or m.group(2))
    if not (1 <= lo <= hi <= 65535):
        return None
    return m.group(1).split('+'), ('%d' % lo if lo == hi else '%d-%d' % (lo, hi))


def mac_warning(target, addresses):
    """ several IPv4 addresses behind one MAC: containers on ipvlan (or a router) share the
    host's MAC, so a MAC priority covers all of them """
    if not MAC_RE.match(target.lower()):
        return ''
    v4 = [a for a in addresses if '.' in a]
    if len(v4) < 2:
        return ''
    return ('%d IPv4 addresses use this MAC (%s): containers on ipvlan, or a router, share their host\'s MAC, so '
            'all of them get this priority. Pick their addresses instead to choose one.'
            % (len(v4), ', '.join(sorted(v4)[:4]) + (', ...' if len(v4) > 4 else '')))


def resolve(target, macs):
    """ a MAC, an address or network, or a firewall alias -> sorted addresses/networks; a port
    target has no addresses (it becomes rules of its own) """
    if port_spec(target):
        return []
    t = target.lower()
    if MAC_RE.match(t):
        return sorted(macs.get(t, ()))
    try:
        return [str(ipaddress.ip_network(target, strict=False))]
    except ValueError:
        pass
    if ALIAS_RE.match(target):
        rc, out, _ = run(['/sbin/pfctl', '-t', target, '-T', 'show'])
        if rc == 0:
            nets = []
            for line in out.split():
                try:
                    nets.append(str(ipaddress.ip_network(line.strip(), strict=False)))
                except ValueError:
                    pass
            return sorted(nets)
    return []


def wanted(root):
    entries = priorities(root)
    macs = neighbours() if any(MAC_RE.match(e['target'].lower()) for e in entries) else {}
    members = {c: {} for c in CLASSES}
    for e in entries:
        members[e['class']][e['target']] = resolve(e['target'], macs)
    return entries, members


def set_rules(st):
    """ :return: [(number, rule text)] of ipfw set st """
    _, out, _ = ipfw('-S', 'show')
    rules = []
    for line in out.splitlines():
        parts = line.split(None, 5)
        if len(parts) == 6 and parts[3] == 'set' and parts[4] == st and parts[0].isdigit():
            rules.append((int(parts[0]), parts[5]))
    return rules


def foreign(st):
    """ rule numbers in set st that are not ours (ours: 2000-2099, setdscp); sets 7 and 30 are
    deleted, disabled and swapped only while nobody else has rules in them (review N5) """
    return [n for n, body in set_rules(st) if not (2000 <= n <= 2099 and body.startswith('setdscp '))]


def set_disabled(st):
    """ True when ipfw set st is disabled ("ipfw set show": "disable 30 enable 0 1 ...") """
    _, out, _ = ipfw('set', 'show')
    words = out.split()
    if 'disable' not in words:
        return False
    rest = words[words.index('disable') + 1:]
    return st in rest[:rest.index('enable')] if 'enable' in rest else st in rest


def drop_ours(st, errors):
    """ delete our rules from set st: the whole set when it holds only ours, else by number """
    ours = sorted({n for n, body in set_rules(st) if 2000 <= n <= 2099 and body.startswith('setdscp ')})
    if not ours:
        return
    rc, _, err = (ipfw('-q', 'delete', 'set', st) if not foreign(st) else
                  ipfw('-q', 'set', st, 'delete', *[str(n) for n in ours]))
    if rc != 0:
        errors.append('set %s: delete failed: %s' % (st, err.strip()))


def table_list(name):
    rc, out, _ = ipfw('table', name, 'list')
    if rc != 0:
        return None
    return {str(ipaddress.ip_network(line.split()[0], strict=False)) for line in out.splitlines()
            if line and not line.startswith('-') and '/' in line.split()[0]}


def enforce_cap(members):
    """ refuse (empty) a target that resolves to more than MAX_PER_TARGET addresses, and the
    targets past MAX_TOTAL: one huge alias must not stall every ipfw reload (review S2)
    :return: errors """
    errors, total = [], 0
    for cls in sorted(members):
        for target in sorted(members[cls]):
            n = len(members[cls][target])
            if n > MAX_PER_TARGET:
                errors.append('%s resolves to %d addresses, more than %d: refused, it gets no priority (pick a '
                              'smaller host or network alias)' % (target, n, MAX_PER_TARGET))
                members[cls][target] = []
            elif total + n > MAX_TOTAL:
                errors.append('%s refused: all priorities together would exceed %d addresses' % (target, MAX_TOTAL))
                members[cls][target] = []
            else:
                total += n
    return errors


def batch(lines, errors):
    """ run ipfw commands from one file, one process for all of them (ipfw(8): "ipfw [-q] pathname") """
    import tempfile
    if not lines:
        return
    fd, path = tempfile.mkstemp(prefix='shaperinsight-prio.')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write('\n'.join(lines) + '\n')
        rc, _, err = ipfw('-q', path)
        if rc != 0 and err.strip():
            errors.append('ipfw batch: %s' % err.strip()[:300])
    finally:
        os.unlink(path)


def sync_tables(members, errors):
    """ one batched ipfw run for all edits, then the tables are read back: every address that is
    not where it should be goes into errors
    :return: number of changes """
    changed = 0
    for cls, (_, table, _, _, _) in CLASSES.items():
        have = table_list(table)
        if have is None:
            rc, _, err = ipfw('table', table, 'create', 'type', 'addr')
            if rc != 0:
                errors.append('table %s: create failed: %s' % (table, err.strip()))
                continue
            have = set()
        # the same form as the table lists them (a host as /32 or /128), or every run would
        # delete and re-add, leaving the table empty in between
        want = {str(ipaddress.ip_network(a, strict=False)) for addrs in members[cls].values() for a in addrs}
        add, delete = sorted(want - have), sorted(have - want)
        if not (add or delete):
            continue
        batch(['table %s add %s' % (table, a) for a in add] + ['table %s delete %s' % (table, a) for a in delete],
              errors)
        now = table_list(table) or set()
        bad = ['add %s' % a for a in add if a not in now] + ['delete %s' % a for a in delete if a in now]
        if bad:
            more = ' and %d more' % (len(bad) - 10) if len(bad) > 10 else ''
            errors.append('table %s: %s failed' % (table, ', '.join(bad[:10]) + more))
        changed += len(add) + len(delete) - len(bad)
    return changed


def build_rules(root, entries):
    """ :return: (rules, errors). Rules for a class only when it has entries, so an unused
    class costs no rule evaluation """
    rules, errors = [], []
    used = {e['class'] for e in entries}
    if not used:
        return rules, errors
    lks = links(root)
    if len(lks) > MAX_LINKS:
        errors.append('%d links sort by DSCP; priorities are set on the first %d only (%s left out)'
                      % (len(lks), MAX_LINKS, ', '.join(lks[MAX_LINKS:])))
        lks = lks[:MAX_LINKS]
    # marks arriving from the internet are not ours: clear them first, so only the rules
    # below choose a tin for incoming traffic (number 2000-2009, one per link)
    for i, ifname in enumerate(lks):
        rules.append([str(2000 + i), 'setdscp', 'be', 'ip', 'from', 'any', 'to', 'any', 'in', 'via', ifname])
    for cls, (dscp, table, first, _, _) in CLASSES.items():
        if cls not in used:
            continue
        # port targets of this class, per protocol: one inline port list each
        ports = {}
        for e in entries:
            spec = port_spec(e.get('target', '')) if e['class'] == cls else None
            if spec:
                for proto in spec[0]:
                    ports.setdefault(proto, [])
                    if spec[1] not in ports[proto]:
                        ports[proto].append(spec[1])
        for proto in sorted(ports):
            if len(ports[proto]) > MAX_PORT_SPECS:
                errors.append('%s: more than %d %s ports; the first %d are used'
                              % (cls, MAX_PORT_SPECS, proto, MAX_PORT_SPECS))
                ports[proto] = ports[proto][:MAX_PORT_SPECS]
        for i, ifname in enumerate(lks):
            rules.append([str(first + 2 * i), 'setdscp', dscp, 'ip', 'from', 'table(%s)' % table,
                          'to', 'any', 'out', 'via', ifname])
            rules.append([str(first + 2 * i + 1), 'setdscp', dscp, 'ip', 'from', 'any',
                          'to', 'table(%s)' % table, 'in', 'via', ifname])
            # the port on either end, both directions (a service here or there); ipfw allows
            # several rules under one number, so the numbering per link stays as it is
            for proto in sorted(ports):
                plist = ','.join(ports[proto])
                for num, way in ((first + 2 * i, 'out'), (first + 2 * i + 1, 'in')):
                    rules.append([str(num), 'setdscp', dscp, proto, 'from', 'any', plist, 'to', 'any',
                                  way, 'via', ifname])
                    rules.append([str(num), 'setdscp', dscp, proto, 'from', 'any', 'to', 'any', plist,
                                  way, 'via', ifname])
    return rules, errors


def load(root):
    """ our rules: built in a disabled staging set first, then swapped with set 7 in one step,
    so a rule that fails to add leaves the previous marks in place (review M5) """
    entries, members = wanted(root)
    errors = enforce_cap(members)
    if not entries:
        # no priority: nothing to load, and nothing created (no tables, set 30 untouched); marks
        # and tables left from an earlier setup go (review N5: install must be side-effect free)
        drop_ours(SET, errors)
        for _, table, _, _, _ in CLASSES.values():
            if table_list(table) is not None:
                rc, _, err = ipfw('table', table, 'destroy')
                if rc != 0:
                    errors.append('table %s: destroy failed: %s' % (table, err.strip()))
        return {'status': 'ok' if not errors else 'failed', 'rules': 0, 'errors': errors, 'kept_previous': False}
    for st in (SET, STAGE):
        if foreign(st):
            return {'status': 'failed', 'rules': 0, 'kept_previous': True,
                    'errors': ['ipfw set %s holds rules that are not Shaper Insight\'s (%s); priorities are not '
                               'loaded so they stay untouched' % (st, ', '.join(str(n) for n in foreign(st)[:5]))]}
    sync_tables(members, errors)
    rules, errs = build_rules(root, entries)
    errors += errs
    stage_was_disabled = set_disabled(STAGE)
    ipfw('-q', 'delete', 'set', STAGE)
    ipfw('set', 'disable', STAGE)
    added = True
    for rule in rules:
        rc, _, err = ipfw('-q', 'add', rule[0], 'set', STAGE, *rule[1:])
        if rc != 0:
            errors.append('rule %s: %s' % (rule[0], err.strip()))
            added = False
    if added:
        rc, _, err = ipfw('set', 'swap', SET, STAGE)
        if rc != 0:
            errors.append('set swap failed: ' + err.strip())
    ipfw('-q', 'delete', 'set', STAGE)
    ipfw('set', 'enable', SET)
    if not stage_was_disabled:
        ipfw('set', 'enable', STAGE)    # the staging set back as it was (ipfw's default: enabled)
    # what is loaded now: refresh keeps exactly these entries current, so a priority saved but not
    # applied never goes live through the cron job (review S7)
    return {'status': 'ok' if not errors else 'failed', 'rules': len(rules) if added else 0, 'errors': errors,
            'kept_previous': not added,
            'entries': [{'class': e['class'], 'target': e['target']} for e in entries] if added else last_entries()}


def last_entries():
    """ the entries of the last successful load, or None """
    try:
        with open(RESULT) as f:
            return json.load(f).get('entries')
    except (OSError, ValueError, AttributeError):
        return None


def refresh(cfg):
    """ keep the addresses of what is LOADED current (a MAC with a new lease); entries saved
    since the last load wait for Apply (review S7) """
    errors = []
    entries = last_entries()
    if not entries:
        return {'status': 'ok', 'changed': 0, 'errors': [], 'skipped': 'nothing loaded'}
    macs = neighbours() if any(MAC_RE.match(e['target'].lower()) for e in entries) else {}
    members = {c: {} for c in CLASSES}
    for e in entries:
        if e.get('class') in CLASSES:
            members[e['class']][e['target']] = resolve(e['target'], macs)
    errors += enforce_cap(members)
    changed = sync_tables(members, errors)
    return {'status': 'ok' if not errors else 'failed', 'changed': changed, 'errors': errors}


def status(root):
    entries, members = wanted(root)
    _, out, _ = ipfw('-S', 'show', '2000-2099')
    rules = []
    for line in out.splitlines():
        # "02010 12 3456 set 7 setdscp cs1 ip from ..."
        parts = line.split(None, 5)
        if len(parts) == 6 and parts[3] == 'set' and parts[4] == SET:
            rules.append({'number': int(parts[0]), 'packets': int(parts[1]), 'bytes': int(parts[2]),
                          'rule': parts[5]})
    classes = []
    for cls, (dscp, table, _, tin, what) in CLASSES.items():
        classes.append({'class': cls, 'dscp': dscp, 'tin': tin, 'what': what,
                        'members': [{'target': t, 'addresses': a, 'port': bool(port_spec(t)),
                                     'warning': mac_warning(t, a)} for t, a in members[cls].items()]})
    return {'classes': classes, 'links': links(read_cfg()), 'rules': rules}


def parse_neighbours(arp_text, ndp_text, skip_ifs=()):
    """ :return: {mac: {'addresses': [..], 'if': name}} from `arp -an` and `ndp -an`: IPv4 ARP
    entries and IPv6 neighbours in state REACHABLE (recently used), never link-local, never the
    firewall's own (permanent) entries or the internet-facing interfaces """
    out = {}

    def add(mac, addr, ifname):
        mac = (mac or '').lower()
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return
        if not MAC_RE.match(mac) or ip.is_link_local or ifname in skip_ifs:
            return
        row = out.setdefault(mac, {'addresses': [], 'if': ifname})
        if str(ip) not in row['addresses']:
            row['addresses'].append(str(ip))
    for line in arp_text.splitlines():
        m = re.search(r'\(([0-9.]+)\) at ([0-9a-f:]{17}) on (\S+)(.*)', line)
        if m and 'permanent' not in m.group(4):
            add(m.group(2), m.group(1), m.group(3))
    for line in ndp_text.splitlines()[1:]:
        parts = line.split()
        # Neighbor Linklayer Netif Expire S [Flags]
        if len(parts) >= 5 and parts[3] != 'permanent' and parts[4] == 'R':
            add(parts[1], parts[0].split('%')[0], parts[2])
    return out


def shares():
    """ devices with more than one address in use: each address is a CAKE host (claim
    host-isolation-per-address), so the device can get one share per address """
    from health import links as detect_links
    det = detect_links()
    wan_ifs = {i['path'][0] for i in det.get('interfaces', []) if i.get('role') == 'wan' and i.get('path')}
    _, arp, _ = run(['/usr/sbin/arp', '-an'])
    _, ndp, _ = run(['/usr/sbin/ndp', '-an'])
    devices = parse_neighbours(arp, ndp, wan_ifs)
    many = [{'mac': mac, 'if': d['if'], 'addresses': sorted(d['addresses'])}
            for mac, d in sorted(devices.items()) if len(d['addresses']) > 1]
    return {'status': 'ok', 'devices': len(devices), 'several': many}


def purge():
    """ uninstall: flag first (load/refresh stop), then take the lock with a bounded wait, so a
    refresh that is running finishes before its tables go; the lock file itself stays (unlinking
    it lets the next caller lock a new inode while the old holder still works, review S4) """
    import time
    errors = []
    with open(REMOVING, 'w') as f:
        f.write(str(int(time.time())))
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        for _ in range(60):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                time.sleep(0.5)
        else:
            errors.append('a priority refresh still held the lock after 30 s; purged anyway (it stops at its end)')
        return _purge(errors)
    finally:
        os.close(fd)


def _purge(errors):
    for st in (SET, STAGE):
        drop_ours(st, errors)       # never another package's rules in the same set (review N5)
    for _, table, _, _, _ in CLASSES.values():
        if table_list(table) is not None:
            rc, _, err = ipfw('table', table, 'destroy')
            if rc != 0:
                errors.append('table %s: destroy failed: %s' % (table, err.strip()))
    if os.path.exists(RESULT):
        os.unlink(RESULT)
    return {'status': 'ok' if not errors else 'failed', 'errors': errors}


def main(cmd):
    if cmd == 'status':
        out = status(read_cfg())
        try:
            with open(RESULT) as f:
                out['last'] = json.load(f)
        except (OSError, ValueError):
            out['last'] = None
        return out
    if cmd == 'purge':
        return purge()
    if cmd == 'enabled':
        # for +POST_INSTALL: exit 0 only when a priority is enabled (then marks must be loaded)
        return {'status': 'ok' if priorities(read_cfg()) else 'failed', 'errors': []}
    if cmd == 'shares':
        return shares()
    if os.path.exists(REMOVING):
        return {'status': 'ok', 'skipped': 'the plugin is being removed', 'errors': []}
    # one lock for load, refresh and unload: an ipfw reload (-> load) and the cron refresh
    # must not interleave their table edits. Never a blocking wait (review S2): refresh skips a
    # minute when busy, load (inside every ipfw reload) waits at most LOAD_WAIT seconds
    import time
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + (0 if cmd == 'refresh' else LOAD_WAIT)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    if cmd == 'refresh':
                        return {'status': 'ok', 'skipped': 'busy', 'errors': []}
                    return {'status': 'failed', 'errors': ['another priority update held the lock for %d s' % LOAD_WAIT]}
                time.sleep(0.5)
        cfg = read_cfg()
        if cmd == 'load':
            out = load(cfg)
        elif cmd == 'refresh':
            out = refresh(cfg)
        elif cmd == 'unload':
            errors = []
            drop_ours(SET, errors)
            out = {'status': 'ok' if not errors else 'failed', 'errors': errors}
        else:
            return {'status': 'failed', 'errors': ['unknown command']}
        out['command'], out['time'] = cmd, int(time.time())
        if cmd == 'refresh':
            out['entries'] = last_entries()
        with open(RESULT + '.tmp', 'w') as f:
            json.dump(out, f)
        os.replace(RESULT + '.tmp', RESULT)
        return out
    finally:
        os.close(fd)


if __name__ == '__main__':
    result = main(sys.argv[1] if len(sys.argv) > 1 else 'status')
    print(json.dumps(result))
    sys.exit(1 if result.get('status') == 'failed' else 0)
