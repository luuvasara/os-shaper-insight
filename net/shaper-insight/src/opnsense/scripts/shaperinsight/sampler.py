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
    History for the graphs: a sampler that runs while the plugin is enabled, every 10 s.

      sampler.py start|stop|restart|status
      sampler.py run                  the loop itself (started by `start` through daemon(8))
      sampler.py history SECONDS      JSON for the sharing page: pipes from the RRDs, hosts from the ring
      sampler.py once                 one sample printed as JSON (tests, cost measurement)

    Pipe rates, CAKE drops/marks/delay/backlog per pipe and per tin go into RRD files next to
    core's own (/var/db/rrd, rrdtool as Reporting: Health uses it), one step of 10 s, fixed
    size: 10 s for 6 h, 1 min for 3 days, 10 min for 30 days, 1 h for a year. They survive
    reboots and Reporting: Health lists them under "shaper".
    Hosts are not in the RRDs: an RRD has a fixed set of data sources, and the busiest hosts
    change from minute to minute; a slot "host 1" would mean another device each hour. The top
    16 hosts per pipe are kept for 6 h (10 s steps) in /var/run/shaperinsight/hosts.jsonl,
    which a reboot clears.
    Counters are DERIVE data sources with the time of each read, so a rate is (bytes now -
    bytes before) / (time of this read - time of that read): no point can be inflated by a
    stamp taken early (TESTING.md speed-test analysis the "900 on an 840 pipe").
"""
import json
import os
import re
import resource
import subprocess
import sys
import time
from lib import run, CACHE_DIR, CONFIG, IPFW, SYSCTL, CAKE_STATS, SHAPER_FIRST_RULE, SHAPER_LAST_RULE, \
    parse_cake_stats, parse_ipfw, read_config, cache_dir, config_root
from lib.traffic import iter_states, parse_states, parse_ifconfig, pipe_map, Topology, HostCounter, by_pipe, \
    payload_cap, cake_overheads

STEP = 10
RRD_DIR = '/var/db/rrd'
RRDTOOL = '/usr/local/bin/rrdtool'
PFCTL = '/sbin/pfctl'
IFCONFIG = '/sbin/ifconfig'
PIDFILE = '/var/run/shaperinsight-sampler.pid'          # daemon(8) supervisor (-P)
CHILD_PIDFILE = '/var/run/shaperinsight-sampler-child.pid'
HOSTS = os.path.join(CACHE_DIR, 'hosts.jsonl')
META = os.path.join(CACHE_DIR, 'sampler.json')
HOSTS_KEEP = 6 * 3600 // STEP                           # 2160 lines: 6 h at 10 s
TOP = 16                        # hosts kept per pipe and sample in the ring (the rest summed as "other")
HOSTS_SHOWN = 8                 # default and maximum of general/hosts_shown: coloured series (host simulation: max 8)
NEWEST = os.path.join(CACHE_DIR, 'newest.json')
NEWEST_CAP = 5000               # hosts per pipe in the newest step (the table's list, search and paging)
PAGE_MAX = 2000                 # rows one newest call returns; the page asks again with offset beyond
# (steps per row, rows): 10 s x 6 h, 1 min x 3 days, 10 min x 30 days, 1 h x 365 days
RRA = [(1, 2160), (6, 4320), (60, 4320), (360, 8760)]
MAIN_DS = [('bytes', 'DERIVE', 12500000000), ('drops', 'DERIVE', 100000000), ('marks', 'DERIVE', 100000000),
           ('delay', 'GAUGE', None), ('peak', 'GAUGE', None), ('backlog', 'GAUGE', None)]
REFRESH = 60                                            # topology/config re-read, meta write
PF_TIMEOUT = 5                  # s; pfctl -vss slower than this skips the per-host read (review N12)
STATE_CAP = 50000               # above this many pf states the per-host read pauses (review N11)
PF_BUDGET_MS = 150              # per-host reads that cost more than this run less often (every 2-6 steps)
# above RSS_GIVE_BACK_KB (current RSS; idle is ~18 MB) with fewer than RSS_CALM_STATES states left, the
# sampler re-executes itself to return memory (lab: 48 MB kept after 22k states); never under a large table
RSS_GIVE_BACK_KB = 40 * 1024
RSS_CALM_STATES = 5000
VERSION_FILE = '/usr/local/opnsense/version/shaper-insight'
WATCHDOG = os.path.join(CACHE_DIR, 'watchdog.json')
CAPS_KEEP = 20                  # cap changes remembered per pipe (review N13)
PIPE_RE = re.compile(r'^[0-9]{1,5}$')


def enabled(tries=5):
    """ the "history sampler" setting: True or False, or None when config.xml cannot be read now.
    Core's Config::save truncates and rewrites config.xml in place under flock(LOCK_EX); an unlocked
    read can see half a file (review N10: 40k saves in 150 s stopped the sampler). So: read under
    LOCK_SH, retry a failed parse, and never turn a failed read into "off" """
    import xml.etree.ElementTree as ET
    try:
        root = config_root(CONFIG, tries)
    except (OSError, ET.ParseError):
        return None
    return root.findtext('./OPNsense/ShaperInsight/general/sampler', '1') == '1'


def package_id():
    """ the installed package's version file (version + tree hash), or None. A running sampler
    compares it each step: `pkg add -f` of an older package leaves this script on disk and running
    (Verification 4, 3g), so a changed or missing file ends the sampler; a new package's own
    POST_INSTALL starts its sampler again """
    try:
        with open(VERSION_FILE, 'rb') as f:
            return f.read()
    except OSError:
        return None


def rrd_path(pipe, tins=False):
    return os.path.join(RRD_DIR, 'pipe%s%s-shaper.rrd' % (pipe, '-tins' if tins else ''))


def create_args(path, tins=0):
    """ rrdtool create arguments: the main file (6 data sources, AVERAGE and MAX) or the tins
    file (bytes and drops per tin, AVERAGE only) """
    hb = STEP * 3
    if tins:
        ds = []
        for i in range(tins):
            ds += ['DS:t%db:DERIVE:%d:0:12500000000' % (i, hb), 'DS:t%dd:DERIVE:%d:0:100000000' % (i, hb)]
        cfs = ['AVERAGE']
    else:
        ds = ['DS:%s:%s:%d:0:%s' % (n, t, hb, 'U' if m is None else m) for n, t, m in MAIN_DS]
        cfs = ['AVERAGE', 'MAX']
    rra = ['RRA:%s:0.5:%d:%d' % (cf, steps, rows) for cf in cfs for steps, rows in RRA]
    return ['create', path, '--step', str(STEP), '--start', str(int(time.time()) - STEP)] + ds + rra


def fmt(v):
    return 'U' if v is None else ('%d' % v if isinstance(v, int) else '%.3f' % v)


def pipe_counters(cake, ipfw_rules, cfg):
    """ cumulative counters per pipe: CAKE tins summed (with per-tin rows), else the ipfw
    counters of the rules feeding the pipe (bytes only) """
    out = {}
    for sched, instances in cake.items():
        tot = {'bytes': 0, 'drops': 0, 'marks': 0, 'delay': 0, 'peak': 0, 'backlog': 0, 'tins': [],
               'tinx': [], 'diffserv': 0, 'names': []}
        for inst in instances:
            for i, tin in enumerate(inst['tins']):
                tot['bytes'] += tin.get('bytes', 0)
                tot['drops'] += tin.get('drops', 0)
                tot['marks'] += tin.get('marks', 0)
                tot['backlog'] += tin.get('backlog', 0)
                tot['delay'] = max(tot['delay'], tin.get('avg_delay', 0))
                tot['peak'] = max(tot['peak'], tin.get('peak_delay', 0))
                if i >= len(tot['tins']):
                    tot['tins'].append([0, 0])
                    tot['tinx'].append([0, 0, 0, 0])
                    tot['names'].append(tin.get('name', 'Tin %d' % i))
                tot['tins'][i][0] += tin.get('bytes', 0)
                tot['tins'][i][1] += tin.get('drops', 0)
                # bytes, drops, ECN marks, backlog: the latest step's view of each class (Bulk tin in TESTING.md)
                for k, key in enumerate(('bytes', 'drops', 'marks', 'backlog')):
                    tot['tinx'][i][k] += tin.get(key, 0)
                tot['diffserv'] = tin.get('diffserv', 0)
        out[str(sched)] = tot
    target_pipe = {p['uuid']: str(p.get('number')) for p in cfg['pipes']}
    target_pipe.update({q['uuid']: target_pipe.get(q.get('pipe')) for q in cfg['queues']})
    for r in cfg['rules']:
        k = ipfw_rules.get(r['uuid'])
        num = target_pipe.get(r.get('target'))
        if k is None or num is None or (num in out and 'rule_bytes' not in out[num]):
            continue
        tot = out.setdefault(num, {'bytes': 0, 'drops': None, 'marks': None, 'delay': None, 'peak': None,
                                   'backlog': None, 'tins': [], 'rule_bytes': True})
        tot['bytes'] += k['bytes']
    return out


class Rrd:
    """ one `rrdtool -` process for all updates (rrdtool(1) "remote control" mode): no process
    start per file and step """

    REPLY_TIMEOUT = 10      # s; a hung rrdtool is killed and started again (review V4 item 5)
    BROKEN_AFTER = 3        # failed updates in a row before a file is moved aside and made again

    def __init__(self):
        self.proc = None
        self.errors = 0
        self.failed = {}

    def _readline(self):
        import select
        ready, _, _ = select.select([self.proc.stdout], [], [], self.REPLY_TIMEOUT)
        if not ready:
            raise TimeoutError('rrdtool gave no answer in %d s' % self.REPLY_TIMEOUT)
        return self.proc.stdout.readline()

    def cmd(self, args):
        if self.proc is None or self.proc.poll() is not None:
            self.proc = subprocess.Popen([RRDTOOL, '-'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, text=True, bufsize=1)
        reply = ''
        try:
            self.proc.stdin.write(' '.join(args) + '\n')
            self.proc.stdin.flush()
            while True:
                line = self._readline()
                if not line:
                    break
                if line.startswith('OK') or line.startswith('ERROR'):
                    reply = line.strip()
                    break
        except (OSError, TimeoutError) as exc:
            reply = str(exc)
            self.proc.kill()
            self.proc.wait()
            self.proc = None
        ok = reply.startswith('OK')
        if not ok:
            self.errors += 1
            log('rrdtool: %s: %s' % (args[0], reply or 'no answer'))
        if args[0] == 'update':
            path = args[1]
            self.failed[path] = 0 if ok else self.failed.get(path, 0) + 1
            if self.failed[path] >= self.BROKEN_AFTER:
                # a broken file (a partial create, a foreign file) fails every step: move it aside,
                # the next step makes a new one (review N14)
                try:
                    os.replace(path, path + '.broken')
                    log('rrdtool: %s failed %d times in a row: moved to %s.broken, starting a new file'
                        % (path, self.failed[path], path))
                except OSError:
                    pass
                self.failed[path] = 0
        return ok

    def ensure(self, path, tins=0):
        if os.path.isfile(path) and not os.path.islink(path):
            return True
        if os.path.islink(path):
            os.unlink(path)
        return self.cmd(create_args(path, tins))

    def cpu_seconds(self):
        if self.proc is None:
            return 0.0
        rc, out, _ = run(['/bin/ps', '-o', 'time=', '-p', str(self.proc.pid)])
        return parse_ps_time(out) if rc == 0 else 0.0

    def close(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.stdin.write('quit\n')
                self.proc.stdin.flush()
                self.proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()


def parse_ps_time(text):
    """ ps time= "0:01.23" or "1:02:03.45" -> seconds """
    total = 0.0
    for part in text.strip().split(':'):
        try:
            total = total * 60 + float(part)
        except ValueError:
            return 0.0
    return total


def log(msg):
    run(['/usr/bin/logger', '-t', 'shaperinsight', 'sampler: ' + msg])


class Quiet:
    """ the same failure every step logs once, then every 60th time with the count, and once
    when it clears (review V4 item 5: one syslog line per failing step, unbounded) """

    def __init__(self):
        self.last, self.count = None, 0

    def fail(self, what):
        if what != self.last:
            self.last, self.count = what, 0
        self.count += 1
        if self.count == 1 or self.count % 60 == 0:
            log('%s%s' % (what, '' if self.count == 1 else ' (%d times in a row)' % self.count))

    def ok(self, what='recovered'):
        if self.last is not None:
            log('%s after %d failed step(s): %s' % (what, self.count, self.last))
        self.last, self.count = None, 0


def read_pipes():
    """ CAKE and ipfw counters, read back to back and stamped at the middle of those two reads
    only: the pipe history does not wait for pf (review N12) """
    w0 = time.time()
    cake = run([SYSCTL, '-n', CAKE_STATS])[1]
    rules = run([IPFW, 'show', '%d-%d' % (SHAPER_FIRST_RULE, SHAPER_LAST_RULE)])[1]
    w1 = time.time()
    return (w0 + w1) / 2, w1 - w0, parse_cake_stats(cake), parse_ipfw(rules)['rules']


def state_count():
    """ `pfctl -si` "current entries", cheap (no state list); None when unknown """
    rc, out, _ = run([PFCTL, '-si'], timeout=PF_TIMEOUT)
    m = re.search(r'current entries\s+(\d+)', out) if rc == 0 else None
    return int(m.group(1)) if m else None


def read_states():
    """ the pf state list for the per-host view: (stamp, seconds, text or None, why not).
    Skipped above STATE_CAP states, and given up after PF_TIMEOUT s (review N11, N12) """
    n = state_count()
    if n is not None and n > STATE_CAP:
        return time.time(), 0.0, None, n, 'over %d pf states (%d): per-host view paused' % (STATE_CAP, n)
    w0 = time.time()
    rc, out, _ = run([PFCTL, '-vss'], timeout=PF_TIMEOUT)
    w1 = time.time()
    if rc != 0:
        return (w0 + w1) / 2, w1 - w0, None, n, ('pfctl -vss took over %d s: per-host view paused' % PF_TIMEOUT
                                                 if w1 - w0 >= PF_TIMEOUT else 'pfctl -vss failed (%d)' % rc)
    return (w0 + w1) / 2, w1 - w0, out, n, ''


def read_counters():
    """ pipes, then pf (`sampler.py once`) """
    ts, read_s, cake, rules = read_pipes()
    _, pf_s, text, _, _ = read_states()
    return ts, read_s + pf_s, cake, parse_states(text or ''), rules


def current_rss_kb():
    """ resident memory now (ru_maxrss is the peak and never goes down) """
    rc, out, _ = run(['/bin/ps', '-o', 'rss=', '-p', str(os.getpid())])
    try:
        return int(out.strip()) if rc == 0 else 0
    except ValueError:
        return 0


def topology(cfg):
    pmap = pipe_map(cfg)
    devs = {d for d, _ in pmap}
    ifaddrs = parse_ifconfig(run([IFCONFIG, '-a'])[1])
    return pmap, Topology(ifaddrs, devs)


def pipes_meta(cfg, pmap, counters):
    over = cake_overheads(cfg)
    names = {str(p.get('number')): p for p in cfg['pipes']}
    where = {}
    for (dev, d), n in pmap.items():
        where.setdefault(n, []).append('%s %s' % (dev, d))
    out = {}
    for n, c in counters.items():
        p = names.get(n, {})
        try:
            bw = float(p.get('bandwidth') or 0) * {'bit': 1, 'Kbit': 1e3, 'Mbit': 1e6, 'Gbit': 1e9}.get(
                p.get('bandwidthMetric') or 'Mbit', 1e6)
        except ValueError:
            bw = 0
        out[n] = {'description': p.get('description', ''), 'bandwidth': int(bw), 'overhead': over.get(n, 0),
                  'payload_cap': payload_cap(bw, over.get(n, 0)) if bw else 0, 'cake': 'rule_bytes' not in c,
                  'diffserv': c.get('diffserv', 0), 'tins': c.get('names', []), 'where': sorted(where.get(n, []))}
    return out


def ring_slots(full, top=TOP):
    """ the ring keeps the TOP busiest hosts of each pipe and the sum of the rest """
    return {k: {'hosts': v['hosts'][:top], 'other': v['other'] + sum(x for _, x in v['hosts'][top:])}
            for k, v in full.items()}


def write_newest(record):
    """ every host of the newest pf step (up to NEWEST_CAP a pipe), for the table's group rows,
    search and paging; replaced at each pf read, so its size stays bounded """
    tmp = NEWEST + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(record, f, separators=(',', ':'))
    os.rename(tmp, NEWEST)


def read_newest():
    try:
        with open(NEWEST) as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get('p'), dict) else None
    except (OSError, ValueError):
        return None


def newest(pipe, q='', sort='rate', offset=0, limit=50):
    """ one pipe's hosts in the newest step, rate in bit/s: searched (address substring), sorted
    (rate | ip) and paged server side (host simulation: beyond about 2,000 hosts the page must not get them all) """
    d = read_newest()
    if d is None:
        return {'status': 'ok', 'hosts': [], 'total': 0, 'sending': 0, 't': None}
    dt = d.get('dt') or STEP
    rows = [[h, round(v * 8 / dt)] for h, v in (d['p'].get(str(pipe)) or []) if h]
    sending = sum(1 for _, v in rows if v > 0)
    q = (q or '').strip().lower()
    if q:
        rows = [x for x in rows if q in x[0].lower()]
    if sort == 'ip':
        import ipaddress

        def key(x):
            try:
                a = ipaddress.ip_address(x[0].split('%')[0])
                return (a.version, int(a), x[0])
            except ValueError:
                return (9, 0, x[0])
        rows.sort(key=key)
    else:
        rows.sort(key=lambda x: (-x[1], x[0]))
    offset = max(0, int(offset))
    limit = max(1, min(PAGE_MAX, int(limit)))
    return {'status': 'ok', 't': d.get('t'), 'dt': dt, 'total': len(rows), 'sending': sending,
            'offset': offset, 'limit': limit, 'hosts': rows[offset:offset + limit]}


def append_hosts(record):
    """ one line per sample; the file is cut back to HOSTS_KEEP lines when it grows a quarter past it """
    line = json.dumps(record, separators=(',', ':')) + '\n'
    fd = os.open(HOSTS, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as f:
        f.write(line)
        f.flush()
        fresh = os.fstat(f.fileno()).st_size == len(line.encode())
    append_hosts.count = None if fresh else getattr(append_hosts, 'count', None)
    if append_hosts.count is None:
        with open(HOSTS) as f:
            append_hosts.count = sum(1 for _ in f)
    else:
        append_hosts.count += 1
    if append_hosts.count > HOSTS_KEEP * 5 // 4:
        with open(HOSTS) as f:
            keep = f.readlines()[-HOSTS_KEEP:]
        tmp = HOSTS + '.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.writelines(keep)
        os.rename(tmp, HOSTS)
        append_hosts.count = len(keep)


def end_supervisor():
    """ daemon(8) -r would start the loop again: end the supervisor (our parent) too. By its
    command line, not the pidfile: after a package change the pidfile may already name a newer
    supervisor, which must stay """
    import signal
    ppid = os.getppid()
    rc, out, _ = run(['/bin/ps', '-o', 'command=', '-p', str(ppid)])
    if ppid > 1 and rc == 0 and 'shaperinsight-sampler' in out:
        os.kill(ppid, signal.SIGTERM)


def loop():
    import signal

    def term(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, term)
    if enabled() is False:
        end_supervisor()
        return
    cache_dir()
    os.makedirs(RRD_DIR, exist_ok=True)
    rrd = Rrd()
    hosts = HostCounter()
    try:
        sample_loop(rrd, hosts)
    finally:
        rrd.close()


def load_caps():
    """ the payload caps seen so far, per pipe, from the last meta file (survives a restart, not a
    reboot): [[since, cap], ...] """
    try:
        with open(META) as f:
            return {n: p.get('caps', []) for n, p in json.load(f).get('pipes', {}).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def note_caps(caps, pipes, now):
    """ remember each change of a pipe's payload cap, so a reading is judged by the cap in force
    when it was taken (review N13) """
    for n, info in pipes.items():
        hist = caps.setdefault(n, [])
        if not hist or hist[-1][1] != info.get('payload_cap'):
            hist.append([round(now), info.get('payload_cap')])
            del hist[:-CAPS_KEEP]
        info['caps'] = hist


def pipe_deltas(now, before):
    """ per pipe: delivered bytes and per-tin [bytes, drops, marks, backlog] between two pipe
    reads (CAKE counts the bytes it sent, so this is after its drops) """
    out = {}
    for n, c in now.items():
        b = before.get(n)
        if b is None or 'rule_bytes' in c:
            continue
        d = {'delivered': max(0, c['bytes'] - b['bytes'])}
        if len(c.get('tinx', [])) > 1 and len(b.get('tinx', [])) == len(c['tinx']):
            d['tins'] = [[max(0, x[0] - y[0]), max(0, x[1] - y[1]), max(0, x[2] - y[2]), x[3]]
                         for x, y in zip(c['tinx'], b['tinx'])]
        out[n] = d
    return out


def sample_loop(rrd, hosts):
    cfg, pmap, topo, meta_pipes = None, {}, None, {}
    cfg_mtime, refreshed = None, 0.0
    cpu, started, ticks = [], time.time(), 0
    pkg = package_id()
    caps = load_caps()
    quiet, pf_quiet = Quiet(), Quiet()
    pf = {'ts': None, 'pipes': None, 'every': 1, 'skip': '', 'states': 0, 'read_s': 0.0, 'parse_ms': 0.0}
    last_pipes = None
    while True:
        tick0 = time.monotonic()
        ru0 = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
        try:
            # the directory can vanish under us (a /var/run cleanup); make it again each tick (mkdir+lstat)
            cache_dir()
            now_pkg = package_id()
            if pkg is not None and now_pkg != pkg:
                log('the package changed or was removed: stopping (a new package starts its own sampler)')
                end_supervisor()
                break
            mtime = os.stat(CONFIG).st_mtime
            if cfg is None or mtime != cfg_mtime or time.time() - refreshed > REFRESH:
                # off only on two reads that both say "off"; a read that fails says nothing (review N10)
                if mtime != cfg_mtime and cfg is not None and enabled() is False:
                    time.sleep(1)
                    if enabled() is False:
                        log('switched off in the settings: stopping')
                        end_supervisor()
                        break
                cfg, cfg_mtime = read_config(), mtime
                pmap, topo = topology(cfg)
                refreshed = time.time()
            # 1. the pipes: every step, stamped on their own
            ts, read_s, cake, rules = read_pipes()
            counters = pipe_counters(cake, rules, cfg)
            for n, c in counters.items():
                if not PIPE_RE.match(n):
                    continue
                if rrd.ensure(rrd_path(n)):
                    rrd.cmd(['update', rrd_path(n), '%.3f:%s' % (ts, ':'.join(fmt(c[k]) for k, _, _ in MAIN_DS))])
                if len(c['tins']) > 1 and rrd.ensure(rrd_path(n, True), len(c['tins'])):
                    vals = ':'.join('%d:%d' % (b, d) for b, d in c['tins'])
                    rrd.cmd(['update', rrd_path(n, True), '%.3f:%s' % (ts, vals)])
            # 2. the hosts: every pf['every'] steps, while pf is quick and the state table is not huge
            if ticks % pf['every'] == 0:
                pts, pf_s, text, n_states, why = read_states()
                pf['read_s'], pf['skip'] = pf_s, why
                if n_states is not None:
                    pf['states'] = n_states
                if text is None:
                    pf_quiet.fail(why)
                    hosts.__init__()            # the next good read primes again (no stale deltas)
                    pf['ts'], pf['pipes'] = None, None
                else:
                    pf_quiet.ok('per-host view back')
                    p0 = time.process_time()       # CPU, not wall time: a busy box must not look costly
                    deltas = hosts.update(iter_states(text), topo, STEP * pf['every'] if pf['ts'] is None
                                          else pts - pf['ts'])
                    del text
                    pf['states'] = hosts.count
                    pf['parse_ms'] = (time.process_time() - p0) * 1000
                    # adaptive: a costly parse runs less often (at most every 6th step, 60 s)
                    pf['every'] = max(1, min(6, int(pf['parse_ms'] // PF_BUDGET_MS) + 1))
                    if pf['ts'] is not None and pf['pipes'] is not None:
                        full = by_pipe(deltas, pmap, NEWEST_CAP)
                        rec = {'t': round(pts, 3), 'dt': round(pts - pf['ts'], 3), 'p': ring_slots(full)}
                        write_newest({'t': rec['t'], 'dt': rec['dt'], 'p': {k: v['hosts'] for k, v in full.items()}})
                        del full
                        for n, d in pipe_deltas(counters, pf['pipes']).items():
                            if n in rec['p']:
                                rec['p'][n]['delivered'] = d['delivered']
                            else:
                                rec['p'][n] = {'hosts': [], 'other': 0, 'delivered': d['delivered']}
                        append_hosts(rec)
                    pf['ts'], pf['pipes'] = pts, counters
            # 3. the tins' latest step (for "Bulk is yielding"), every step
            tin_now = pipe_deltas(counters, last_pipes or {}) if last_pipes else {}
            last_pipes = counters
            ru1 = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
            cpu.append(sum(b.ru_utime + b.ru_stime - a.ru_utime - a.ru_stime for a, b in zip(ru0, ru1)))
            cpu = cpu[-6:]
            ticks += 1
            if not meta_pipes or ticks % 6 == 0 or tin_now:
                if not meta_pipes or ticks % 6 == 0:
                    meta_pipes = pipes_meta(cfg, pmap, counters)
                    note_caps(caps, meta_pipes, time.time())
                rss = current_rss_kb() if ticks % 6 == 0 or ticks == 1 else None
                write_meta(started, meta_pipes, cpu, read_s, pf, rrd, time.monotonic() - tick0, rss, tin_now, ts)
                if rss and rss > RSS_GIVE_BACK_KB and pf['states'] < RSS_CALM_STATES:
                    # Python keeps freed memory; after a state flood has passed, start afresh in the
                    # same process (daemon(8) keeps its child; one per-host interval is lost)
                    log('memory: %d MB resident after a large state table; restarting to give it back' % (rss // 1024))
                    rrd.close()
                    os.execv(sys.executable, [sys.executable, os.path.abspath(__file__), 'run'])
            quiet.ok()
        except Exception as exc:         # one bad sample must not end the history
            quiet.fail('sample failed: %r' % (exc,))
        # the next step on the 10 s grid
        time.sleep(max(1.0, STEP - (time.time() % STEP)))


def write_meta(started, pipes, cpu, read_s, pf, rrd, tick_s, rss=None, tin_now=None, ts=None):
    size = 0
    for n in pipes:
        for p in (rrd_path(n), rrd_path(n, True)):
            try:
                size += os.path.getsize(p)
            except OSError:
                pass
    try:
        ring = os.path.getsize(HOSTS)
    except OSError:
        ring = 0
    if rss is None:
        try:
            with open(META) as f:
                rss = json.load(f).get('cost', {}).get('rss_kb')
        except (OSError, ValueError):
            rss = None
    meta = {'started': started, 'written': time.time(), 'step': STEP, 'pipes': pipes,
            'latest': {'t': ts, 'step': STEP, 'pipes': tin_now or {}},
            'cost': {'cpu_ms_per_sample': round(1000 * sum(cpu) / max(1, len(cpu)), 1),
                     'read_ms': round(read_s * 1000, 1), 'tick_ms': round(tick_s * 1000, 1),
                     'states': pf['states'], 'pf_read_ms': round(pf['read_s'] * 1000, 1),
                     'pf_parse_ms': round(pf['parse_ms'], 1), 'hosts_every_s': pf['every'] * STEP,
                     'hosts_paused': pf['skip'], 'state_cap': STATE_CAP,
                     'rss_kb': rss or resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                     'rss_peak_kb': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                     'rrdtool_cpu_s': round(rrd.cpu_seconds(), 2), 'rrd_bytes': size, 'ring_bytes': ring,
                     'rrd_errors': rrd.errors}}
    tmp = META + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'w') as f:
        json.dump(meta, f)
    os.rename(tmp, META)


# ------------------------------------------------------------------ history (the page reads this)

def choose_step(window):
    """ the finest archive that gives at most 720 points over the window """
    for steps, rows in RRA:
        if window / (steps * STEP) <= 720:
            return steps * STEP
    return RRA[-1][0] * STEP


def parse_fetch(text):
    """ `rrdtool fetch` output -> (names, [(t, [values])]); nan -> None """
    lines = [x for x in text.splitlines() if x.strip()]
    if not lines:
        return [], []
    names = lines[0].split()
    rows = []
    for line in lines[1:]:
        ts, sep, rest = line.partition(':')
        if not sep or not ts.strip().isdigit():
            continue
        vals = []
        for v in rest.split():
            try:
                f = float(v)
                vals.append(None if f != f else f)
            except ValueError:
                vals.append(None)
        rows.append((int(ts), vals))
    return names, rows


def fetch(path, cf, start, end, step, factor):
    """ rrdtool fetch (this rrdtool is built without graphics, so no xport) -> {start, step,
    series: {ds: [value x factor or None]}}; a row stamped t covers (t - step, t] """
    rc, out, _ = run([RRDTOOL, 'fetch', path, cf, '-r', str(step), '-s', str(int(start)), '-e', str(int(end))],
                     timeout=20)
    if rc != 0:
        return None
    names, rows = parse_fetch(out)
    # the newest row is still being filled: drop trailing rows without data
    while rows and all(v is None for v in rows[-1][1]):
        rows.pop()
    if not rows:
        return None
    real = rows[1][0] - rows[0][0] if len(rows) > 1 else step
    series = {n: [None if r[1][i] is None else round(r[1][i] * factor.get(n, 1), 3) for r in rows]
              for i, n in enumerate(names)}
    return {'start': rows[0][0], 'step': real, 'series': series}


def host_series(row0, step, n, weights=None, shown=HOSTS_SHOWN):
    """ per pipe: the top 6 hosts over the rows (+ the rest as ''), bit/s per row, and the hosts'
    unscaled sum per row ("offered").
    - Rows as the RRD gives them: row k is stamped row0 + k * step and covers the step before it. A
      record of about one step goes to the row that holds the middle of its interval; a longer one
      (the adaptive interval) is spread over the rows it covers, in proportion to the pipe's own bytes
      in those rows (weights: bit/s per row per pipe) when known, else by time.
    - pf counts a host's bytes before dummynet drops them, so under drops the hosts add up to more
      than the pipe sent (review N9: 107 % at 362 drops/s). Each record's hosts are scaled down to
      the pipe's delivered bytes when they exceed them; "offered" keeps what pf counted.
    :return: (series per pipe, first record time, offered per pipe) """
    try:
        with open(HOSTS) as f:
            lines = f.readlines()
    except OSError:
        return {}, None, {}
    import math
    start = row0 - step
    first = None
    buckets, offered, grouped = {}, {}, {}
    for line in lines:
        try:
            rec = json.loads(line)
            t, dt = float(rec['t']), max(0.001, float(rec['dt']))
        except (ValueError, KeyError, TypeError):
            continue
        if t - dt / 2 <= start:
            continue
        first = t if first is None else first
        if dt <= 1.5 * step:
            spans = [(int(math.ceil((t - dt / 2 - row0) / step)), 1.0)]
        else:
            lo = t - dt
            spans = []
            for b in range(max(0, int(math.ceil((lo - row0) / step))), int(math.ceil((t - row0) / step)) + 1):
                part = min(t, row0 + b * step) - max(lo, row0 + (b - 1) * step)
                if part > 0:
                    spans.append((b, part / dt))
        spans = [(b, f) for b, f in spans if 0 <= b < n]
        if not spans:
            continue
        for pipe, slot in rec['p'].items():
            sp = spans
            w = (weights or {}).get(pipe)
            if w and len(spans) > 1:
                ws = [(b, f * (w[b] or 0)) for b, f in spans]
                tot = sum(x for _, x in ws)
                if tot > 0:
                    sp = [(b, x / tot * sum(f for _, f in spans)) for b, x in ws]
            pairs = list(slot.get('hosts', []))
            if slot.get('other'):
                pairs.append(['', slot['other']])
            total = sum(v for _, v in pairs)
            deliv = slot.get('delivered')
            scale = deliv / total if deliv is not None and total > deliv else 1.0
            pb = buckets.setdefault(pipe, {})
            ob = offered.setdefault(pipe, {})
            for b, f in sp:
                ob[b] = ob.get(b, 0) + total * f
                for h, v in pairs:
                    pb.setdefault(h, {})
                    pb[h][b] = pb[h].get(b, 0) + v * scale * f
    out = {}
    for pipe, hosts in buckets.items():
        totals = sorted(((sum(v.values()), h) for h, v in hosts.items() if h), reverse=True)
        top = [h for _, h in totals[:shown]]
        other = {}
        for h, v in hosts.items():
            if h not in top:
                for b, x in v.items():
                    other[b] = other.get(b, 0) + x
        series = [[h, [round(hosts[h].get(i, 0) * 8 / step) for i in range(n)]] for h in top]
        if other:
            series.append(['', [round(other.get(i, 0) * 8 / step) for i in range(n)]])
        # for the table's expander: hosts seen in the window outside the coloured set
        grouped[pipe] = sorted(h for h in hosts if h and h not in top)
        out[pipe] = series
    offered_bits = {pipe: [round(ob.get(i, 0) * 8 / step) for i in range(n)] for pipe, ob in offered.items()}
    host_series.grouped = grouped
    return out, first, offered_bits


def cap_rows(caps, start, step, n):
    """ the payload cap in force at each row (review N13: a reading is judged by the cap of its
    time); None for rows from before the first known cap """
    out = []
    for i in range(n):
        t = start + i * step
        cur = None
        for since, cap in caps:
            if since <= t:
                cur = cap
        out.append(cur)
    return out


def above_cap(bits, caps):
    """ readings above the cap of their own time (1 % tolerance) """
    return sum(1 for v, c in zip(bits, caps) if v is not None and c and v > c * 1.01)


def fmt_rate(bits):
    for unit, div in (('Gbit/s', 1e9), ('Mbit/s', 1e6), ('kbit/s', 1e3)):
        if bits >= div:
            return '%.1f %s' % (bits / div, unit)
    return '%d bit/s' % bits


def bulk_status(info, latest, prio):
    """ what the Background class (CAKE's Bulk tin) does on this pipe now, in words, from the
    newest step's per-tin deltas (Bulk tin in TESTING.md). Tin order in this dn_cake build:
    diffserv4 0 Best Effort, 1 Bulk, 2 Video, 3 Voice; diffserv3 0 Best Effort, 1 Bulk, 2 Voice.
    :return: {state, floor, text} or None when the pipe has no Bulk class and no Background priority """
    if not info.get('cake'):
        return None
    names = info.get('tins') or []
    bg = prio.get('background', 0)
    if info.get('diffserv') not in (1, 2) or 'Bulk' not in names:
        if bg:
            return {'state': 'besteffort', 'floor': None,
                    'text': 'Priorities have no effect on this pipe: CAKE is set to besteffort here, so Background '
                            'traffic shares the line like everything else. Set diffserv4 (or diffserv3) on its CAKE '
                            'entry for Background to give way.'}
        return None
    cap = info.get('payload_cap') or info.get('bandwidth') or 0
    floor = cap / 16
    out = {'state': 'unknown', 'floor': floor,
           'floor_text': 'Background is guaranteed about %s (1/16 of the line) when the line is full; all Background '
                         'devices share this floor together.' % fmt_rate(floor)}
    tins = (latest or {}).get('tins')
    step = (latest or {}).get('step') or STEP
    k = names.index('Bulk')
    if not tins or k >= len(tins):
        out['text'] = 'No per-class reading yet.'
        return out
    b = tins[k]
    rate = b[0] * 8 / step
    other = sum(x[0] for i, x in enumerate(tins) if i != k) * 8 / step
    pressure = b[1] > 0 or b[2] > 0 or b[3] > 0
    # "nothing" in the other classes is a fixed share of the line, not of Background's own rate: with Bulk at
    # 38 Mbit/s, 3 Mbit/s of other traffic is not nothing (review 2026-11-22)
    nothing = max(cap * 0.02, 64000)
    busy = pressure or rate + other >= 0.8 * cap
    out.update({'rate': rate, 'other': other})
    if rate < max(floor / 100, 8000):
        out.update(state='idle', text='No Background traffic in the last %d s.' % step)
    elif rate <= 1.5 * floor and other > nothing and busy:
        out.update(state='yielding', text='Background is yielding: %s, while %s of other traffic uses the line. It gets '
                   'the whole line when nothing else needs it.' % (fmt_rate(rate), fmt_rate(other)))
    elif other <= nothing:
        out.update(state='free', text='Background is using the free line: %s.' % fmt_rate(rate))
    else:
        out.update(state='sharing', text='Background %s next to %s of other traffic; not held back in the last %d s.'
                   % (fmt_rate(rate), fmt_rate(other), step))
    return out


def hosts_other(series, grouped, nd):
    """ per pipe, for the expander "Show the M other hosts · K sending now": M = hosts seen in the
    window (ring) or now (newest step) outside the coloured set, K = those sending now """
    out = {}
    for p in set(grouped) | set(nd['p']):
        shown = {h for h, _ in series.get(p, [])}
        now = {h for h, v in nd['p'].get(p, []) if h and v} - shown
        out[p] = {'seen': len(set(grouped.get(p, [])) | now), 'sending': len(now)}
    return out


def hosts_shown():
    """ general/hosts_shown (3-8), else the default """
    import xml.etree.ElementTree as ET
    try:
        v = int(config_root(CONFIG).findtext('./OPNsense/ShaperInsight/general/hosts_shown', HOSTS_SHOWN))
    except (OSError, ET.ParseError, ValueError):
        return HOSTS_SHOWN
    return max(3, min(HOSTS_SHOWN, v))


def priorities_set():
    """ enabled priorities per class, for the per-tin text (a Background priority on a besteffort
    pipe changes nothing, Bulk tin in TESTING.md) """
    import xml.etree.ElementTree as ET
    out = {}
    try:
        root = config_root(CONFIG)
    except (OSError, ET.ParseError):
        return out
    for p in root.findall('./OPNsense/ShaperInsight/priorities/priority'):
        if p.findtext('enabled') == '1':
            c = p.findtext('class') or ''
            out[c] = out.get(c, 0) + 1
    return out


def history(window):
    window = max(60, min(int(window), 365 * 86400))
    now = time.time()
    step = choose_step(window)
    end = int(now // step) * step
    try:
        with open(META) as f:
            meta = json.load(f)
    except (OSError, ValueError):
        meta = {}
    pipes = {}
    for n, info in sorted(meta.get('pipes', {}).items()):
        main = fetch(rrd_path(n), 'AVERAGE', end - window, end, step, {'bytes': 8, 'delay': 0.001, 'peak': 0.001})
        if main is None:
            continue
        peak = fetch(rrd_path(n), 'MAX', end - window, end, step, {'bytes': 8})
        entry = dict(info, start=main['start'], step=main['step'], series=main['series'],
                     max_bits=peak['series'].get('bytes', []) if peak else [])
        if len(info.get('tins', [])) > 1 and os.path.exists(rrd_path(n, True)):
            names = ['t%db' % i for i in range(len(info['tins']))]
            t = fetch(rrd_path(n, True), 'AVERAGE', end - window, end, step, {x: 8 for x in names})
            if t is not None:
                entry['tin_series'] = t['series']
        nrows = len(entry['series'].get('bytes', []))
        entry['cap_rows'] = cap_rows(info.get('caps') or [], entry['start'], entry['step'], nrows)
        entry['above_cap'] = above_cap(entry['series'].get('bytes', []), entry['cap_rows'])
        pipes[n] = entry
    prio = priorities_set()
    latest = meta.get('latest', {})
    for n, entry in pipes.items():
        lp = (latest.get('pipes') or {}).get(n)
        entry['latest_tins'] = (lp or {}).get('tins')
        entry['bulk'] = bulk_status(entry, dict(lp or {}, step=latest.get('step')), prio)
    hosts, first, offered = ({}, None, {})
    shown = hosts_shown()
    host_series.grouped = {}
    nd = read_newest() or {'p': {}}
    if window <= HOSTS_KEEP * STEP:
        ref = next(iter(pipes.values()), None)
        row0, n = (ref['start'], len(ref['series']['bytes'])) if ref else (end - window + step, window // step)
        hosts, first, offered = host_series(row0, step, n,
                                            {k: v['series'].get('bytes') for k, v in pipes.items()}, shown)
    st = run_state()
    return {'status': 'ok' if meta else 'off', 'now': now, 'window': window, 'step': step, 'pipes': pipes,
            'hosts': hosts, 'hosts_offered': offered, 'hosts_shown': shown,
            # the expander's numbers ("Show the M other hosts · K sending now"); the rows come from newest
            'hosts_other': hosts_other(hosts, host_series.grouped, nd),
            'hosts_stored': TOP, 'hosts_since': first, 'hosts_keep_s': HOSTS_KEEP * STEP,
            'running': st == 'running', 'state': st, 'enabled': enabled(), 'cost': meta.get('cost', {}),
            'priorities': prio, 'watchdog': watchdog_info(),
            'started': meta.get('started'), 'written': meta.get('written')}


# ------------------------------------------------------------------ process control

def live_pid():
    """ the supervisor's pid when it runs, else None: daemon(8) flocks its pidfile while it lives,
    and the pid's command line must be the sampler's (as bbtest.live_pid, review S10) """
    import fcntl
    try:
        fd = os.open(PIDFILE, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return None             # nobody holds it: no supervisor
        except OSError:
            pass
        pid = int(os.read(fd, 32).decode().strip() or '0')
    except ValueError:
        return None
    finally:
        os.close(fd)
    if pid <= 1:
        return None
    rc, out, _ = run(['/bin/ps', '-o', 'command=', '-p', str(pid)])
    # daemon(8) -t names the supervisor: "daemon: shaperinsight-sampler[child pid] (daemon)"
    return pid if rc == 0 and 'shaperinsight-sampler' in out else None


def child_alive():
    """ the sampler process itself (daemon(8) -p); during daemon's 30 s restart delay only the
    supervisor lives (review N14: status said "running" then) """
    try:
        with open(CHILD_PIDFILE) as f:
            pid = int(f.read().strip() or '0')
    except (OSError, ValueError):
        return False
    if pid <= 1:
        return False
    rc, out, _ = run(['/bin/ps', '-o', 'command=', '-p', str(pid)])
    return rc == 0 and 'sampler.py run' in out


def run_state():
    """ running, restarting (supervisor waiting to start the sampler again) or stopped """
    if live_pid() is None:
        return 'stopped'
    return 'running' if child_alive() else 'restarting'


def watchdog_info():
    try:
        with open(WATCHDOG) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def ensure():
    """ the watchdog (cron, every 5 minutes while the sampler is on): start it when it is switched
    on but not running, and record that it had stopped (review N10: the history stopped silently) """
    on = enabled()
    if on is not True or live_pid() is not None:
        return {'status': 'ok', 'action': 'none', 'enabled': on, 'state': run_state()}
    res = start()
    info = watchdog_info()
    info = {'last': time.time(), 'count': int(info.get('count', 0)) + 1, 'started': bool(res.get('running'))}
    cache_dir()
    tmp = WATCHDOG + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'w') as f:
        json.dump(info, f)
    os.rename(tmp, WATCHDOG)
    log('watchdog: the sampler was switched on but not running; %s' % (
        'started it again' if res.get('running') else 'starting it failed: %s' % res.get('message', '')))
    return {'status': res.get('status'), 'action': 'started', 'running': res.get('running')}


def start():
    if enabled() is False:
        return {'status': 'ok', 'running': False, 'message': 'switched off in the settings'}
    if live_pid() is not None:
        return {'status': 'ok', 'running': True, 'message': 'already running'}
    rc, _, err = run(['/usr/sbin/daemon', '-f', '-r', '-R', '30', '-P', PIDFILE, '-p', CHILD_PIDFILE,
                      '-t', 'shaperinsight-sampler', sys.executable, os.path.abspath(__file__), 'run'])
    for _ in range(20):
        if live_pid() is not None:
            break
        time.sleep(0.1)
    return {'status': 'ok' if rc == 0 and live_pid() is not None else 'failed', 'running': live_pid() is not None,
            'message': err.strip()}


def stop():
    """ SIGTERM to the supervisor (daemon(8) passes it on); after 5 s, SIGKILL to both """
    import signal
    pid = live_pid()
    if pid is not None:
        os.kill(pid, signal.SIGTERM)    # daemon(8) passes it on to the sampler and exits
        for _ in range(50):
            if live_pid() is None:
                break
            time.sleep(0.1)
    if live_pid() is not None or child_alive():
        for path in (PIDFILE, CHILD_PIDFILE):
            try:
                with open(path) as f:
                    victim = int(f.read().strip() or '0')
                rc, out, _ = run(['/bin/ps', '-o', 'command=', '-p', str(victim)])
                if victim > 1 and rc == 0 and ('shaperinsight-sampler' in out or 'sampler.py run' in out):
                    os.kill(victim, signal.SIGKILL)
            except (OSError, ValueError):
                pass
        time.sleep(0.5)
    gone = live_pid() is None and not child_alive()
    return {'status': 'ok' if gone else 'failed', 'running': not gone}


def status():
    try:
        with open(META) as f:
            meta = json.load(f)
    except (OSError, ValueError):
        meta = {}
    st = run_state()
    return {'status': 'ok', 'enabled': enabled(), 'running': st == 'running', 'state': st, 'step': STEP,
            'watchdog': watchdog_info(),
            'cost': meta.get('cost', {}), 'started': meta.get('started'), 'written': meta.get('written'),
            'rrd_dir': RRD_DIR, 'hosts_keep_s': HOSTS_KEEP * STEP,
            'archives': [{'step_s': s * STEP, 'rows': r, 'span_s': s * STEP * r} for s, r in RRA]}


def once():
    cfg = read_config()
    pmap, topo = topology(cfg)
    hc = HostCounter()
    t0 = time.monotonic()
    ts, read_s, cake, states, rules = read_counters()
    hc.update(states, topo, STEP)
    parse_s = time.monotonic() - t0
    time.sleep(STEP)
    ts2, _, _, states2, _ = read_counters()
    deltas = hc.update(states2, topo, ts2 - ts)
    return {'ts': ts, 'read_ms': round(read_s * 1000, 1), 'read_parse_ms': round(parse_s * 1000, 1),
            'states': len(states), 'counters': pipe_counters(cake, rules, cfg),
            'pmap': {'%s %s' % k: v for k, v in pmap.items()}, 'hosts': by_pipe(deltas, pmap, TOP)}


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if cmd == 'run':
        loop()
        sys.exit(0)
    try:
        if cmd == 'history':
            result = history(sys.argv[2] if len(sys.argv) > 2 and sys.argv[2].isdigit() else 3600)
        elif cmd == 'newest':
            a = (sys.argv[2:] + ['', '', '', '', ''])[:5]
            result = newest(a[0] if a[0].isdigit() else '', a[1] if re.fullmatch(r'[0-9A-Fa-f:.]{1,64}', a[1]) else '',
                            a[2] if a[2] in ('rate', 'ip') else 'rate', a[3] if a[3].isdigit() else 0,
                            a[4] if a[4].isdigit() else 50)
        else:
            actions = {'start': start, 'stop': stop, 'status': status, 'once': once, 'ensure': ensure,
                       'restart': lambda: (stop(), start())[1]}
            result = actions[cmd]() if cmd in actions else {'status': 'failed', 'message': 'unknown command'}
    except Exception as exc:
        result = {'status': 'failed', 'message': 'crashed: %r' % (exc,)}
    print(json.dumps(result))
