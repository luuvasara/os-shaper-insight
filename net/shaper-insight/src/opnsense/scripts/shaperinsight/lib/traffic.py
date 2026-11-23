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
    --------------------------------------------------------------------------------------
    Who uses each pipe: per-host byte counters from pf states, mapped to the shaper pipes, plus
    the payload cap a pipe can carry. Pure functions; the sampler (sampler.py) runs the tools.

    pf states (`pfctl -vss`) carry bytes per state with the pre-NAT address in parentheses, so
    upload is credited to the LAN host. Cost on a busy home firewall: 6 231 states, pfctl 0.02 s
    (TESTING.md speed-test analysis F1). A state that ends between two samples loses its last delta.

    Where pf counts (lab, TESTING.md per-host counting): a forwarded connection has two
    states, one per interface, and each counts a packet on the interface where it came in, also
    when CAKE then drops it. With CAKE on the WAN device:
    - upload: the WAN-side (NAT) state counts as the packet leaves, after CAKE: exact (hosts sum
      to 0.998-1.001 of what the pipe sent);
    - download: the WAN-side state counts as the packet comes in, before CAKE (1.04-1.12 x with
      TCP, 2.45 x with a UDP flood), the LAN-side state counts it as it leaves, after CAKE
      (0.989-1.011). So download is taken from the LAN-side state, paired with its WAN-side state
      by (protocol, host address:port, remote address:port) to know the WAN device (2026-11-23).
    What has no LAN-side state (the firewall's own traffic, states that do not pair) keeps the
    WAN-side count, which lib/fairshare.py corrects against the pipe's own byte counters.
"""
import functools
import ipaddress
import re

MTU = 1500
# os-cake line presets: the per-packet overhead they stand for (os-cake 1.4.15 `link`)
LINK_OVERHEAD = {'ethernet': 38, 'docsis': 18, 'pppoe-ptm': 30, 'bridged-ptm': 22, 'pppoe-vcmux': 32,
                 'pppoe-llcsnap': 40, 'pppoa-vcmux': 10, 'pppoa-llc': 14, 'bridged-vcmux': 24,
                 'bridged-llcsnap': 32, 'ipoa-vcmux': 8, 'ipoa-llcsnap': 16}
FIREWALL = 'this firewall'
STATE_RE = re.compile(r'^(\S+)\s+(\S+)\s+(\S+)(?:\s+\((\S+)\))?\s+(->|<-)\s+(\S+)(?:\s+\((\S+)\))?\s+\S+:\S+\s*$')
BYTES_RE = re.compile(r'\b(\d+):(\d+) bytes\b')
AGE_RE = re.compile(r'\bage (?:(\d+)d)?(\d+):(\d+):(\d+)')


def payload_cap(bandwidth, overhead, mtu=MTU):
    """ the IP-layer rate a pipe can carry: CAKE counts IP length + overhead per packet """
    try:
        overhead = max(0, int(overhead))
    except (TypeError, ValueError):
        overhead = 0
    return int(bandwidth * mtu / (mtu + overhead))


def cake_overheads(cfg):
    """ pipe number -> overhead bytes of its enabled os-cake entry (numeric overhead first, else
    the line preset's); pipes without CAKE are missing (FQ-CoDel counts no overhead) """
    numbers = {p.get('uuid'): str(p.get('number')) for p in cfg.get('pipes', [])}
    out = {}
    for c in cfg.get('cake', []):
        n = numbers.get(c.get('pipe'))
        if not n or c.get('enabled') == '0':
            continue
        over = c.get('overhead') or ''
        if over.lstrip('-').isdigit():
            out[n] = int(over)
        elif c.get('link') in LINK_OVERHEAD:
            out[n] = LINK_OVERHEAD[c['link']]
    return out


@functools.lru_cache(maxsize=65536)
def _ip(text):
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def parse_addr(text):
    """ '192.0.2.1:443', '2001:db8::1[443]', '192.0.2.1' -> ip address object or None (cached:
    the same few hundred addresses come back in every read) """
    t = text
    if '[' in t:
        t = t.split('[', 1)[0]
    elif t.count(':') == 1:
        t = t.split(':', 1)[0]
    return _ip(t)


def iter_lines(text):
    """ the lines of text one by one, without the list splitlines() builds (at 20k states the
    pfctl output is ~10 MB; a list of its lines would double that, review N11) """
    i = 0
    n = len(text)
    while i < n:
        j = text.find('\n', i)
        if j < 0:
            j = n
        yield text[i:j]
        i = j + 1


def iter_states(text):
    """ `pfctl -vss` -> {key, proto, left, left_nat, arrow, right, right_nat, b0, b1, age, lt, ln, rt} one
    at a time (lt, ln, rt: the left, left NAT and right address:port as printed); key is a hash of the state line (64 bit), not the line, to keep the per-state memory small.
    b0 counts the packets in the state's own direction (out for "->", in for "<-"), b1 the reverse """
    cur = None
    for line in iter_lines(text):
        if not line.startswith((' ', '\t')):
            if cur is not None:
                yield cur
            m = STATE_RE.match(line)
            cur = None
            if m:
                cur = {'key': hash(line.rsplit(None, 1)[0]), 'proto': m.group(2),
                       'left': parse_addr(m.group(3)), 'left_nat': parse_addr(m.group(4)) if m.group(4) else None,
                       'arrow': m.group(5), 'right': parse_addr(m.group(6)),
                       'right_nat': parse_addr(m.group(7)) if m.group(7) else None, 'b0': 0, 'b1': 0, 'age': None,
                       # address:port as printed, for pairing the two states of one connection
                       'lt': m.group(3), 'ln': m.group(4), 'rt': m.group(6)}
            continue
        if cur is None or 'bytes' not in line:
            continue
        m = BYTES_RE.search(line)
        if m:
            cur['b0'], cur['b1'] = int(m.group(1)), int(m.group(2))
        m = AGE_RE.search(line)
        if m:
            cur['age'] = int(m.group(1) or 0) * 86400 + int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4))
    if cur is not None:
        yield cur


def parse_states(text):
    """ iter_states as a list (tests, `sampler.py once`) """
    return list(iter_states(text))


def parse_ifconfig(text):
    """ `ifconfig -a` -> {device: [ip_interface, ...]} (no link-local, no loopback) """
    out, dev = {}, None
    for line in text.splitlines():
        m = re.match(r'^([A-Za-z0-9_.]+): flags=', line)
        if m:
            dev = m.group(1)
            out.setdefault(dev, [])
            continue
        parts = line.split()
        if dev is None or len(parts) < 2 or parts[0] not in ('inet', 'inet6'):
            continue
        addr = parts[1].split('%')[0]
        if parts[0] == 'inet':
            mask = parts[parts.index('netmask') + 1] if 'netmask' in parts else '0xffffffff'
            try:
                plen = bin(int(mask, 16)).count('1')
                ifa = ipaddress.ip_interface('%s/%d' % (addr, plen))
            except ValueError:
                continue
        else:
            plen = parts[parts.index('prefixlen') + 1] if 'prefixlen' in parts else '128'
            try:
                ifa = ipaddress.ip_interface('%s/%s' % (addr, plen))
            except ValueError:
                continue
        if ifa.ip.is_link_local or ifa.ip.is_loopback:
            continue
        out[dev].append(ifa)
    return out


def pipe_map(cfg):
    """ (device, 'up'|'down') -> pipe number of the shaper rules on that device: 'out' rules shape
    upload, 'in' rules download, a rule without direction both. Several rules (a small control
    lane next to the catch-all) -> the pipe with the highest bandwidth, the one hosts share """
    pipes = {p.get('uuid'): p for p in cfg.get('pipes', []) if p.get('enabled') != '0'}
    queues = {q.get('uuid'): q.get('pipe') for q in cfg.get('queues', []) if q.get('enabled') != '0'}
    ifs = cfg.get('interfaces', {})

    def bw(p):
        try:
            return float(p.get('bandwidth') or 0) * {'bit': 1, 'Kbit': 1e3, 'Mbit': 1e6, 'Gbit': 1e9}.get(
                p.get('bandwidthMetric') or 'Mbit', 1e6)
        except ValueError:
            return 0
    best = {}
    for r in cfg.get('rules', []):
        if r.get('enabled') == '0':
            continue
        target = r.get('target')
        pipe = pipes.get(target) or pipes.get(queues.get(target))
        dev = ifs.get(r.get('interface') or '', {}).get('if')
        if pipe is None or not dev:
            continue
        dirs = {'out': ['up'], 'in': ['down']}.get(r.get('direction') or '', ['up', 'down'])
        for d in dirs:
            if (dev, d) not in best or bw(pipe) > bw(best[(dev, d)]):
                best[(dev, d)] = pipe
    return {k: str(p.get('number')) for k, p in best.items()}


class Topology:
    """ what the attribution needs: the firewall's own addresses (and their device), the
    shaped devices, and the local networks behind the other interfaces """

    def __init__(self, ifaddrs, shaped_devs):
        self.own = {}
        self.local = []
        fam = {4: set(), 6: set()}
        for dev, addrs in ifaddrs.items():
            for ifa in addrs:
                self.own[ifa.ip] = dev
                if dev in shaped_devs:
                    fam[ifa.version].add(dev)
                else:
                    self.local.append(ifa.network)
        # without NAT the state does not say which link a host used; with one shaped link per
        # address family it is that one, otherwise "not attributed"
        self.single = {v: (sorted(d)[0] if len(d) == 1 else None) for v, d in fam.items()}
        self._local = {}

    def is_local(self, ip):
        if ip is None or ip in self.own:
            return False
        hit = self._local.get(ip)
        if hit is None:
            hit = self._local[ip] = any(ip in n for n in self.local if n.version == ip.version)
            if len(self._local) > 100000:
                self._local.clear()
        return hit

    def is_remote(self, ip):
        return ip is not None and ip not in self.own and not self.is_local(ip)


def classify(state, topo):
    """ one state -> ('wan', host, device or None, up, down, pair key or None), ('lan', host, up, down,
    pair key), or None when it is neither.
    - 'wan': the internet-side state of a host's (or the firewall's) connection; this is the one the
      per-host view counts (each forwarded connection has one state per interface).
    - 'lan': the LAN-side twin of a forwarded connection: no NAT, a remote address on the left and a
      local host on the right. pfctl prints the address that is on the interface's own side first, so
      on the LAN device the remote end comes first, on the WAN device the host (or its NAT address).
      Its download count is after CAKE (see the module notes).
    The pair key is a hash of (protocol, host address:port, remote address:port), the same for both
    states of one connection; the firewall's own traffic has no LAN-side twin (key None). """
    left, lnat, right = state['left'], state['left_nat'], state['right']
    out = state['arrow'] == '->'
    up, down = (state['b0'], state['b1']) if out else (state['b1'], state['b0'])
    if lnat is not None:
        # NAT on the internet side: one of the two is ours (the external address), the other the host
        if left in topo.own and topo.is_local(lnat):
            ext, host, htok = left, lnat, state.get('ln')
        elif lnat in topo.own and topo.is_local(left):
            ext, host, htok = lnat, left, state.get('lt')
        else:
            return None
        if not topo.is_remote(right):
            return None
        return 'wan', str(host), topo.own[ext], up, down, hash((state['proto'], htok, state.get('rt')))
    if state['right_nat'] is not None:
        return None
    if topo.is_remote(right):
        if left in topo.own:
            return 'wan', FIREWALL, topo.own[left], up, down, None
        if topo.is_local(left):
            return ('wan', str(left), topo.single.get(left.version), up, down,
                    hash((state['proto'], state.get('lt'), state.get('rt'))))
        return None
    if topo.is_remote(left) and topo.is_local(right):
        # LAN side: "<-" = created inbound on the LAN (the host opened it), so b0 is the host's upload;
        # "->" = created outbound on the LAN (a port forward or an inbound IPv6 connection), b0 is download
        lup, ldown = (state['b0'], state['b1']) if not out else (state['b1'], state['b0'])
        return 'lan', str(right), lup, ldown, hash((state['proto'], state.get('rt'), state.get('lt')))
    return None


def attribute(state, topo):
    """ one state -> (host, device or None, up bytes, down bytes), or None when it is not traffic
    between a local host (or the firewall) and the internet, or is the LAN-side state of a pair
    (each forwarded connection has one state per interface; the internet-side one is the host's) """
    c = classify(state, topo)
    if c is None or c[0] != 'wan':
        return None
    return c[1], c[2], c[3], c[4]


class HostCounter:
    """ per-host byte deltas between two `pfctl -vss` reads. A state seen for the first time
    counts in full when it is younger than the interval (it started since the last read), else
    only from now on (it existed before the sampler started).
    update() returns what the internet-side states counted ("offered": download before CAKE);
    self.exact then holds, per (device, 'down') and host, [bytes the paired LAN-side states counted
    (after CAKE), bytes the same connections' internet-side states counted], so a caller can replace
    the one by the other and estimate only the rest (lib/fairshare.estimate) """

    def __init__(self):
        self.last = {}
        self.primed = False
        self.count = 0
        self.exact = {}

    def _delta(self, key, i, now, age, interval):
        if key in self.last:
            before = self.last[key][i]
            return now - before if now >= before else now
        if self.primed and age is not None and age <= interval + 1:
            return now
        return 0

    def update(self, states, topo, interval):
        """ states: any iterable (a generator keeps one state in memory at a time); self.count is
        the number of states read """
        seen, deltas = {}, {}
        wan, lan = {}, {}           # pair key -> (device, host, download delta) / (host, download delta)
        self.count = 0
        for st in states:
            self.count += 1
            c = classify(st, topo)
            if c is None:
                continue
            key = st['key']
            if c[0] == 'lan':
                _, host, up, down, pk = c
                seen[key] = (up, down)
                dd = self._delta(key, 1, down, st['age'], interval)
                prev = lan.get(pk)
                lan[pk] = (host, dd + (prev[1] if prev and prev[0] == host else 0))
                continue
            _, host, dev, up, down, pk = c
            seen[key] = (up, down)
            du = self._delta(key, 0, up, st['age'], interval)
            dd = self._delta(key, 1, down, st['age'], interval)
            for d, v in (('up', du), ('down', dd)):
                if v:
                    slot = deltas.setdefault((dev, d), {})
                    slot[host] = slot.get(host, 0) + v
            if pk is not None:
                prev = wan.get(pk)
                wan[pk] = (dev, host, dd + (prev[2] if prev and prev[1] == host else 0))
        exact = {}
        for pk, (dev, host, dd) in wan.items():
            pair = lan.get(pk)
            if pair is None or pair[0] != host or not (dd or pair[1]):
                continue
            x = exact.setdefault((dev, 'down'), {}).setdefault(host, [0, 0])
            x[0] += pair[1]
            x[1] += dd
        first, self.primed = not self.primed, True
        self.last = seen
        self.exact = {} if first else exact
        return {} if first else deltas


def by_pipe(deltas, pmap, top=8, exact=None):
    """ {(dev, dir): {host: bytes}} -> {pipe: {'hosts': [entry, ..top], 'other': bytes}}; traffic on a
    device with no pipe, or not attributed to a device, goes to 'na-up'/'na-down'.
    An entry is [host, bytes pf counted] or, with exact ({(dev, dir): {host: [after, before]}}, see
    HostCounter.exact), [host, bytes pf counted, bytes counted after CAKE, the part of the first these
    replace]. Hosts are ranked by their best count (counted - before + after); the summed rest's exact
    parts go to 'other_x' [after, before] """
    merged, mx = {}, {}
    for (dev, d), hosts in deltas.items():
        pipe = pmap.get((dev, d)) if dev else None
        key = pipe if pipe else 'na-' + d
        slot = merged.setdefault(key, {})
        for h, v in hosts.items():
            slot[h] = slot.get(h, 0) + v
    for (dev, d), hosts in (exact or {}).items():
        pipe = pmap.get((dev, d)) if dev else None
        key = pipe if pipe else 'na-' + d
        slot = mx.setdefault(key, {})
        for h, (a, b) in hosts.items():
            x = slot.setdefault(h, [0, 0])
            x[0] += a
            x[1] += b
            merged.setdefault(key, {}).setdefault(h, 0)
    out = {}
    for key, hosts in merged.items():
        xs = mx.get(key, {})

        def entry(h, v):
            x = xs.get(h)
            return [h, v, x[0], x[1]] if x and (x[0] or x[1]) else [h, v]

        def best(kv):
            x = xs.get(kv[0])
            return kv[1] - x[1] + x[0] if x else kv[1]
        ranked = sorted(hosts.items(), key=lambda kv: (-best(kv), kv[0]))
        out[key] = {'hosts': [entry(h, v) for h, v in ranked[:top]], 'other': sum(v for _, v in ranked[top:])}
        rest = [xs[h] for h, _ in ranked[top:] if h in xs]
        if rest:
            out[key]['other_x'] = [sum(x[0] for x in rest), sum(x[1] for x in rest)]
    return out
