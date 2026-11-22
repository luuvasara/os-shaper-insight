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
    (TESTING.md speed-test analysis F1). pf counts before dummynet: packets CAKE drops are included, so
    a host's upload reads slightly high while the pipe is saturated. A state that ends between
    two samples loses its last delta.
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


def parse_states(text):
    """ `pfctl -vss` -> [{key, proto, left, left_nat, arrow, right, right_nat, b0, b1, age}]
    b0 counts the packets in the state's own direction (out for "->", in for "<-"), b1 the reverse """
    states, cur = [], None
    for line in text.splitlines():
        if not line.startswith((' ', '\t')):
            m = STATE_RE.match(line)
            cur = None
            if m:
                cur = {'key': line.rsplit(None, 1)[0], 'proto': m.group(2),
                       'left': parse_addr(m.group(3)), 'left_nat': parse_addr(m.group(4)) if m.group(4) else None,
                       'arrow': m.group(5), 'right': parse_addr(m.group(6)),
                       'right_nat': parse_addr(m.group(7)) if m.group(7) else None, 'b0': 0, 'b1': 0, 'age': None}
                states.append(cur)
            continue
        if cur is None or 'bytes' not in line:
            continue
        m = BYTES_RE.search(line)
        if m:
            cur['b0'], cur['b1'] = int(m.group(1)), int(m.group(2))
        m = AGE_RE.search(line)
        if m:
            cur['age'] = int(m.group(1) or 0) * 86400 + int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4))
    return states


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


def attribute(state, topo):
    """ one state -> (host, device or None, up bytes, down bytes), or None when it is not traffic
    between a local host (or the firewall) and the internet, or is the second state of a pair
    (each forwarded connection has one state per interface; only the internet-side one counts) """
    left, lnat, right = state['left'], state['left_nat'], state['right']
    out = state['arrow'] == '->'
    up, down = (state['b0'], state['b1']) if out else (state['b1'], state['b0'])
    if lnat is not None:
        # NAT on the internet side: one of the two is ours (the external address), the other the host
        if left in topo.own and topo.is_local(lnat):
            ext, host = left, lnat
        elif lnat in topo.own and topo.is_local(left):
            ext, host = lnat, left
        else:
            return None
        if not topo.is_remote(right):
            return None
        return str(host), topo.own[ext], up, down
    if not topo.is_remote(right):
        return None
    if left in topo.own:
        return FIREWALL, topo.own[left], up, down
    if topo.is_local(left):
        return str(left), topo.single.get(left.version), up, down
    return None


class HostCounter:
    """ per-host byte deltas between two `pfctl -vss` reads. A state seen for the first time
    counts in full when it is younger than the interval (it started since the last read), else
    only from now on (it existed before the sampler started) """

    def __init__(self):
        self.last = {}
        self.primed = False

    def update(self, states, topo, interval):
        seen, deltas = {}, {}
        for st in states:
            a = attribute(st, topo)
            if a is None:
                continue
            host, dev, up, down = a
            key = st['key']
            seen[key] = (up, down)
            if key in self.last:
                pu, pd = self.last[key]
                du, dd = (up - pu if up >= pu else up), (down - pd if down >= pd else down)
            elif self.primed and st['age'] is not None and st['age'] <= interval + 1:
                du, dd = up, down
            else:
                du, dd = 0, 0
            for d, v in (('up', du), ('down', dd)):
                if v:
                    slot = deltas.setdefault((dev, d), {})
                    slot[host] = slot.get(host, 0) + v
        first, self.primed = not self.primed, True
        self.last = seen
        return {} if first else deltas


def by_pipe(deltas, pmap, top=8):
    """ {(dev, dir): {host: bytes}} -> {pipe: {'hosts': [[host, bytes], ..top], 'other': bytes}};
    traffic on a device with no pipe, or not attributed to a device, goes to 'na-up'/'na-down' """
    merged = {}
    for (dev, d), hosts in deltas.items():
        pipe = pmap.get((dev, d)) if dev else None
        key = pipe if pipe else 'na-' + d
        slot = merged.setdefault(key, {})
        for h, v in hosts.items():
            slot[h] = slot.get(h, 0) + v
    out = {}
    for key, hosts in merged.items():
        ranked = sorted(hosts.items(), key=lambda kv: (-kv[1], kv[0]))
        out[key] = {'hosts': [[h, v] for h, v in ranked[:top]], 'other': sum(v for _, v in ranked[top:])}
    return out
