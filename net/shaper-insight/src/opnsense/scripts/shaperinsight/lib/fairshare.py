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
    Per-host bytes a CAKE pipe sent, estimated from what pf counted (lab results in
    TESTING.md, per-host counting).

    pf counts a host's bytes before dummynet drops them, so when the line is full the hosts
    add up to more than the pipe sent. CAKE with dual-srchost (upload) / dual-dsthost
    (download) shares the line per host first: a host below its fair share loses nothing, and
    the drops fall on the hosts above it. So instead of shrinking every host by the same
    factor, the pipe's delivered bytes are handed out the way CAKE hands out the line:
    max-min fair (water-filling). Hosts offering less than the current fair level keep what
    they offered; the rest of the capacity is split equally among the others, repeated until
    nobody is left below the level.

    With diffserv tins the line is first split between tins by CAKE's tin thresholds, then per
    host inside each tin. The per-tin delivered bytes are exact (CAKE's own counters), so when
    it is known which tin a host's traffic goes to (a Background priority = CS1 = Bulk tin), each
    tin's delivered bytes are water-filled among that tin's hosts. Hosts with no known tin share
    the default tin plus every tin that no mapped host uses.

    Assumptions, and where this can still be wrong:
    - one host = one CAKE host bucket (an address). A dual-stack device is two hosts to CAKE.
    - all of a host's traffic in one tin. A host with some flows marked (EF voice, CS1 backup)
      and some not is split by CAKE but not here; such a host falls in the default group.
    - pf and CAKE are read at about the same moment; a misaligned window moves bytes between
      neighbouring steps, which no allocation can undo.
    - a responsive (TCP) host above its share backs off, so its pf count is close to what it
      got; most of the excess pf counts is from unresponsive senders (UDP floods, many fresh
      flows), whose drops this model puts on exactly those hosts.
    - delivered bytes that pf could not attribute to a host (the firewall's own traffic in some
      setups, unattributed IPv6) are handed to the attributed hosts.
    - a host is never given more than pf counted for it: when a group offered less than its
      capacity (counters misaligned, traffic pf could not attribute), the offers stand.

    What the sampler uses since 1.0.k is estimate() below: download bytes counted after CAKE on the
    LAN-side state are taken as they are (exact), and only the rest is estimated, per tin by
    allocate's water-filling, or scaled when the tins of those hosts are unknown. allocate() is
    kept as the lab's comparison.
"""

DEFAULT_TIN = 0            # dn_cake tin order: diffserv3/4 0 = Best Effort
TOL = 0.02                 # counting tolerance: pf and CAKE are read ~35 ms apart; exact counts were
                           # within 0.989-1.011 of the pipe in the lab. Below it nothing is corrected
EXACT, CHECKED, FAIR, SCALED, COUNTED = 'exact', 'checked', 'fair', 'scaled', 'counted'


def water_fill(offered, capacity, weights=None):
    """ max-min fair split of capacity among hosts that offered `offered` ({host: amount}).
    A host gets min(offered, weight x level), with the level chosen so the shares add up to
    capacity. Nobody gets more than offered: when the hosts offered no more than capacity, each
    keeps its offer (the line was not the limit, or the counters are misaligned).
    :param weights: {host: weight}, default 1 (an aggregate of k hosts: weight k)
    :return: {host: amount} (floats) """
    out = {h: 0.0 for h in offered}
    live = [(h, float(v), float((weights or {}).get(h, 1) or 1)) for h, v in offered.items() if v and v > 0]
    total = sum(v for _, v, _ in live)
    if capacity is None or total <= capacity:
        for h, v, _ in live:
            out[h] = v
        return out
    remaining = max(0.0, float(capacity))
    # ascending by offer per unit of weight: whoever is below the level is fixed first
    live.sort(key=lambda x: (x[1] / x[2], str(x[0])))
    wsum = sum(w for _, _, w in live)
    for i, (h, v, w) in enumerate(live):
        level = remaining / wsum if wsum > 0 else 0.0
        if v <= w * level:
            out[h] = v
            remaining -= v
            wsum -= w
            continue
        # everyone from here on offers more than the level: they share what is left by weight
        for h2, _, w2 in live[i:]:
            out[h2] = w2 * level
        break
    return out


def two_level(flows, capacity, weights=None):
    """ CAKE's dual-srchost / dual-dsthost order: water-fill the hosts first, then each host's
    share among its own flows (tc-cake(8): "fairness is applied first over source addresses,
    then over individual flows").
    :param flows: {host: {flow: bytes}}
    :return: ({host: bytes}, {host: {flow: bytes}}). The host totals are those of water_fill over
             the hosts' sums: the flow level only splits a host's share, it never moves bytes between
             hosts (a host's flows all compete for that host's share). """
    hosts = water_fill({h: sum(v for v in f.values() if v and v > 0) for h, f in flows.items()}, capacity, weights)
    return hosts, {h: water_fill(f, hosts[h]) for h, f in flows.items()}


def proportional(offered, capacity):
    """ the previous estimate (sampler.host_series before 2026-11-23): every host shrunk by the
    same factor when the hosts add up to more than the pipe sent. Kept for comparison. """
    total = sum(v for v in offered.values() if v and v > 0)
    scale = capacity / total if capacity is not None and total > capacity else 1.0
    return {h: (v * scale if v and v > 0 else 0.0) for h, v in offered.items()}


def fill(alloc, capacity):
    """ the constrained variant: a group that offered less than its (exact) capacity is scaled up
    to it, so the hosts add up to what CAKE says it sent. Used only for comparison in the lab: it breaks "never more than pf counted". """
    tot = sum(alloc.values())
    if capacity is None or tot <= 0 or tot >= capacity:
        return alloc
    k = capacity / tot
    return {h: v * k for h, v in alloc.items()}


def allocate(offered, delivered, tins=None, host_tin=None, weights=None, default_tin=DEFAULT_TIN, strict=False):
    """ per-host bytes the pipe sent in one interval.
    :param offered: {host: bytes pf counted}
    :param delivered: bytes the pipe sent (all tins), None = unknown (offered returned)
    :param tins: [bytes sent per tin] in the pipe's tin order, or None
    :param host_tin: {host: tin index} for hosts whose tin is known (priorities); others default
    :param weights: {host: weight} (aggregate rows), see water_fill
    :param strict: each group sums to its capacity exactly (scaled up when it offered less)
    :return: ({host: bytes}, how) with how 'none' (nothing to correct), 'pipe' (one water-filling
             over the whole pipe) or 'tins' (per tin) """
    if delivered is None:
        return {h: float(v or 0) for h, v in offered.items()}, 'none'
    total = sum(v for v in offered.values() if v and v > 0)
    # a host mapped to the default tin is simply in the default group
    mapped = {h: t for h, t in (host_tin or {}).items() if h in offered and offered[h] and t != default_tin}
    usable = (tins and len(tins) > 1 and mapped
              and all(isinstance(t, int) and 0 <= t < len(tins) for t in mapped.values())
              and abs(sum(tins) - delivered) <= max(1, 0.01 * delivered))
    if usable:
        groups = {}
        for h in offered:
            groups.setdefault(mapped.get(h, -1), {})[h] = offered[h]
        used = set(t for t in groups if t >= 0)
        # a mapped tin that sent nothing while its hosts offered bytes: the mapping does not
        # match what CAKE did (marks not applied, a besteffort pipe) -> whole pipe instead
        if any(tins[t] <= 0 for t in used):
            usable = False
    if not usable:
        if total <= delivered and not strict:
            return {h: float(v or 0) for h, v in offered.items()}, 'none'
        got = water_fill(offered, delivered, weights)
        return (fill(got, delivered) if strict else got), 'pipe'
    out = {}
    rest_cap = sum(b for t, b in enumerate(tins) if t not in used)
    for t, members in groups.items():
        cap = rest_cap if t < 0 else tins[t]
        got = water_fill(members, cap, weights)
        out.update(fill(got, cap) if strict else got)
    return out, 'tins'


def estimate(entries, delivered, tins=None, host_tin=None, weights=None, default_tin=DEFAULT_TIN, tol=TOL,
             after=False):
    """ per-host bytes the pipe sent in one interval, and how each was obtained (1.0.k, lab results in
    TESTING.md, per-host counting).
    :param entries: {host: (pf bytes, bytes counted after CAKE, the part of pf bytes those replace)};
                    a plain number means (pf bytes, 0, 0)
    :param delivered: bytes the pipe sent (all tins); None = unknown (no CAKE counters)
    :param tins: [bytes sent per tin] (a besteffort pipe: [delivered]); None = the tins are unknown
    :param host_tin: {host: tin index} from the priority tables
    :param after: pf's counts of this pipe are after CAKE already (upload: the WAN-side state counts a packet
                  as it leaves), so what needs no correction is exact
    :return: ({host: bytes}, {host: {method: bytes}}) with method
             'exact'   counted after CAKE (LAN-side state, or upload on the WAN side);
             'checked' pf's count before CAKE, kept because the pipe's own total shows nothing to correct
                       (within tol: in the lab the firewall's own TCP download read 2 % high this way);
             'fair'    the rest, estimated by max-min fair share inside the host's CAKE tin;
             'scaled'  the rest, scaled down by one factor: the tins of those hosts are unknown (no per-tin
                       counters, a mapped tin that sent nothing, or more than tol of the bytes in tins no
                       host is known to use). Never whole-pipe water-filling: with a Background host it was
                       worse than scaling (RESULTS, runs b and d);
             'counted' pf's count as it is: no pipe total to check against. """
    ex, rest = {}, {}
    for h, e in entries.items():
        if isinstance(e, (list, tuple)):
            off, a, b = (list(e) + [0, 0])[:3]
        else:
            off, a, b = e, 0, 0
        ex[h] = float(max(0, a or 0))
        rest[h] = float(max(0, (off or 0) - (b or 0)))
    out = dict(ex)
    how = {h: ({EXACT: ex[h]} if ex[h] else {}) for h in entries}

    def put(h, v, m):
        out[h] += v
        if v:
            how[h][m] = how[h].get(m, 0.0) + v

    def keep(hs, m):
        for h in hs:
            put(h, rest[h], m)
    if delivered is None:
        keep(entries, COUNTED)
        return out, how
    kept = EXACT if after else CHECKED
    if sum(ex.values()) + sum(rest.values()) <= delivered * (1 + tol):
        keep(entries, kept)
        return out, how

    def scale(hs, cap):
        tot = sum(rest[h] for h in hs)
        k = min(1.0, max(0.0, cap) / tot) if tot > 0 else 1.0
        for h in hs:
            put(h, rest[h] * k, SCALED)
    usable = (isinstance(tins, (list, tuple)) and len(tins) >= 1
              and abs(sum(tins) - delivered) <= max(1, 0.01 * delivered))
    mapped = {}
    if usable:
        mapped = {h: t for h, t in (host_tin or {}).items()
                  if h in entries and (ex[h] or rest[h]) and t != default_tin
                  and isinstance(t, int) and 0 <= t < len(tins)}
        # a mapped tin that sent nothing while its hosts sent: the mapping does not match what CAKE did
        if any(tins[t] <= 0 for t in set(mapped.values())):
            usable = False
    if not usable:
        scale(list(entries), delivered - sum(ex.values()))
        return out, how
    groups = {}
    for h in entries:
        groups.setdefault(mapped.get(h, -1), []).append(h)
    used = set(t for t in groups if t >= 0)
    rest_tins = [t for t in range(len(tins)) if t not in used]
    for t, hs in groups.items():
        cap = sum(tins[k] for k in rest_tins) if t < 0 else tins[t]
        ex_g, r_g = sum(ex[h] for h in hs), sum(rest[h] for h in hs)
        if r_g <= 0:
            continue
        if ex_g + r_g <= cap * (1 + tol):
            keep(hs, kept)
            continue
        if t < 0:
            unknown = sum(tins[k] for k in rest_tins if k != default_tin)
            if unknown > tol * cap:
                # bytes in tins no host is known to use (a host marking its own traffic, a port-based
                # priority): which of these hosts sent them is unknown
                scale(hs, cap - ex_g)
                continue
        got = water_fill({h: rest[h] for h in hs}, max(0.0, cap - ex_g), weights)
        for h in hs:
            put(h, got[h], FAIR)
    return out, how
