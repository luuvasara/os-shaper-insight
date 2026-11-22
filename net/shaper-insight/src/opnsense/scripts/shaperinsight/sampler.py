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
    8 hosts per pipe are kept for 6 h (10 s steps) in /var/run/shaperinsight/hosts.jsonl,
    which a reboot clears.
    Counters are DERIVE data sources with the time of each read, so a rate is (bytes now -
    bytes before) / (time of this read - time of that read): no point can be inflated by a
    stamp taken early (TESTING.md speed-test analysis the "900 on an 840 pipe").
"""
import json
import os
import resource
import subprocess
import sys
import time
from lib import run, CACHE_DIR, CONFIG, IPFW, SYSCTL, CAKE_STATS, SHAPER_FIRST_RULE, SHAPER_LAST_RULE, \
    parse_cake_stats, parse_ipfw, read_config, cache_dir
from lib.traffic import parse_states, parse_ifconfig, pipe_map, Topology, HostCounter, by_pipe, payload_cap, \
    cake_overheads

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
TOP = 8
# (steps per row, rows): 10 s x 6 h, 1 min x 3 days, 10 min x 30 days, 1 h x 365 days
RRA = [(1, 2160), (6, 4320), (60, 4320), (360, 8760)]
MAIN_DS = [('bytes', 'DERIVE', 12500000000), ('drops', 'DERIVE', 100000000), ('marks', 'DERIVE', 100000000),
           ('delay', 'GAUGE', None), ('peak', 'GAUGE', None), ('backlog', 'GAUGE', None)]
REFRESH = 60                                            # topology/config re-read, meta write


def enabled():
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(CONFIG).getroot()
    except (OSError, ET.ParseError):
        return False
    return root.findtext('./OPNsense/ShaperInsight/general/sampler', '1') == '1'


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
               'diffserv': 0, 'names': []}
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
                    tot['names'].append(tin.get('name', 'Tin %d' % i))
                tot['tins'][i][0] += tin.get('bytes', 0)
                tot['tins'][i][1] += tin.get('drops', 0)
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

    def __init__(self):
        self.proc = None
        self.errors = 0

    def cmd(self, args):
        if self.proc is None or self.proc.poll() is not None:
            self.proc = subprocess.Popen([RRDTOOL, '-'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.proc.stdin.write(' '.join(args) + '\n')
        self.proc.stdin.flush()
        reply = ''
        while True:
            line = self.proc.stdout.readline()
            if not line:
                break
            if line.startswith('OK') or line.startswith('ERROR'):
                reply = line.strip()
                break
        if not reply.startswith('OK'):
            self.errors += 1
            log('rrdtool: %s: %s' % (args[0], reply or 'no answer'))
        return reply.startswith('OK')

    def ensure(self, path, tins=0):
        if os.path.exists(path):
            return True
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


def read_counters():
    """ the three counter sources, back to back; the stamp is the middle of the reads """
    w0 = time.time()
    cake = run([SYSCTL, '-n', CAKE_STATS])[1]
    states = run([PFCTL, '-vss'], timeout=20)[1]
    rules = run([IPFW, 'show', '%d-%d' % (SHAPER_FIRST_RULE, SHAPER_LAST_RULE)])[1]
    w1 = time.time()
    return (w0 + w1) / 2, w1 - w0, parse_cake_stats(cake), parse_states(states), parse_ipfw(rules)['rules']


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
    """ daemon(8) -r would start the loop again: end the supervisor (our parent) too """
    import signal
    if live_pid() == os.getppid():
        os.kill(os.getppid(), signal.SIGTERM)


def loop():
    import signal

    def term(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, term)
    if not enabled():
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


def sample_loop(rrd, hosts):
    cfg, pmap, topo, meta_pipes = None, {}, None, {}
    cfg_mtime, refreshed, last_ts = None, 0.0, None
    cpu, started, ticks = [], time.time(), 0
    while True:
        tick0 = time.monotonic()
        ru0 = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
        try:
            # the directory can vanish under us (a /var/run cleanup); make it again each tick (mkdir+lstat)
            cache_dir()
            mtime = os.stat(CONFIG).st_mtime
            if cfg is None or mtime != cfg_mtime or time.time() - refreshed > REFRESH:
                if mtime != cfg_mtime and cfg is not None and not enabled():
                    log('switched off in the settings: stopping')
                    end_supervisor()
                    break
                cfg, cfg_mtime = read_config(), mtime
                pmap, topo = topology(cfg)
                refreshed = time.time()
            ts, read_s, cake, states, rules = read_counters()
            counters = pipe_counters(cake, rules, cfg)
            for n, c in counters.items():
                if rrd.ensure(rrd_path(n)):
                    rrd.cmd(['update', rrd_path(n), '%.3f:%s' % (ts, ':'.join(fmt(c[k]) for k, _, _ in MAIN_DS))])
                if len(c['tins']) > 1 and rrd.ensure(rrd_path(n, True), len(c['tins'])):
                    vals = ':'.join('%d:%d' % (b, d) for b, d in c['tins'])
                    rrd.cmd(['update', rrd_path(n, True), '%.3f:%s' % (ts, vals)])
            deltas = hosts.update(states, topo, STEP if last_ts is None else ts - last_ts)
            if last_ts is not None:
                append_hosts({'t': round(ts, 3), 'dt': round(ts - last_ts, 3), 'p': by_pipe(deltas, pmap, TOP)})
            last_ts = ts
            ru1 = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
            cpu.append(sum(b.ru_utime + b.ru_stime - a.ru_utime - a.ru_stime for a, b in zip(ru0, ru1)))
            cpu = cpu[-6:]
            ticks += 1
            if not meta_pipes or ticks % 6 == 0:
                meta_pipes = pipes_meta(cfg, pmap, counters)
                write_meta(started, meta_pipes, cpu, read_s, len(states), rrd, time.monotonic() - tick0)
        except Exception as exc:         # one bad sample must not end the history
            log('sample failed: %r' % (exc,))
        # the next step on the 10 s grid
        time.sleep(max(1.0, STEP - (time.time() % STEP)))


def write_meta(started, pipes, cpu, read_s, n_states, rrd, tick_s):
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
    meta = {'started': started, 'written': time.time(), 'step': STEP, 'pipes': pipes,
            'cost': {'cpu_ms_per_sample': round(1000 * sum(cpu) / max(1, len(cpu)), 1),
                     'read_ms': round(read_s * 1000, 1), 'tick_ms': round(tick_s * 1000, 1), 'states': n_states,
                     'rss_kb': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
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


def host_series(row0, step, n):
    """ per pipe: the top 6 hosts over the rows (+ the rest as ''), bit/s per row. Rows as the
    RRD gives them: row k is stamped row0 + k * step and covers the step before it; a sample
    goes to the row that holds the middle of the interval it measured """
    try:
        with open(HOSTS) as f:
            lines = f.readlines()
    except OSError:
        return {}, None
    import math
    start = row0 - step
    first = None
    buckets = {}
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        mid = rec['t'] - rec['dt'] / 2
        if mid <= start:
            continue
        first = rec['t'] if first is None else first
        b = int(math.ceil((mid - row0) / step))
        if b >= n:
            continue
        for pipe, slot in rec['p'].items():
            pb = buckets.setdefault(pipe, {})
            for h, v in slot['hosts']:
                pb.setdefault(h, {})
                pb[h][b] = pb[h].get(b, 0) + v
            if slot['other']:
                pb.setdefault('', {})
                pb[''][b] = pb[''].get(b, 0) + slot['other']
    out = {}
    for pipe, hosts in buckets.items():
        totals = sorted(((sum(v.values()), h) for h, v in hosts.items() if h), reverse=True)
        top = [h for _, h in totals[:6]]
        other = {}
        for h, v in hosts.items():
            if h not in top:
                for b, x in v.items():
                    other[b] = other.get(b, 0) + x
        series = [[h, [round(hosts[h].get(i, 0) * 8 / step) for i in range(n)]] for h in top]
        if other:
            series.append(['', [round(other.get(i, 0) * 8 / step) for i in range(n)]])
        out[pipe] = series
    return out, first


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
        pipes[n] = entry
    hosts, first = ({}, None)
    if window <= HOSTS_KEEP * STEP:
        ref = next(iter(pipes.values()), None)
        row0, n = (ref['start'], len(ref['series']['bytes'])) if ref else (end - window + step, window // step)
        hosts, first = host_series(row0, step, n)
    return {'status': 'ok' if meta else 'off', 'now': now, 'window': window, 'step': step, 'pipes': pipes,
            'hosts': hosts, 'hosts_since': first, 'hosts_keep_s': HOSTS_KEEP * STEP,
            'running': live_pid() is not None, 'enabled': enabled(), 'cost': meta.get('cost', {}),
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


def start():
    if not enabled():
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
    import signal
    pid = live_pid()
    if pid is not None:
        os.kill(pid, signal.SIGTERM)    # daemon(8) passes it on to the sampler and exits
        for _ in range(50):
            if live_pid() is None:
                break
            time.sleep(0.1)
    return {'status': 'ok' if live_pid() is None else 'failed', 'running': live_pid() is not None}


def status():
    try:
        with open(META) as f:
            meta = json.load(f)
    except (OSError, ValueError):
        meta = {}
    return {'status': 'ok', 'enabled': enabled(), 'running': live_pid() is not None, 'step': STEP,
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
        else:
            actions = {'start': start, 'stop': stop, 'status': status, 'once': once,
                       'restart': lambda: (stop(), start())[1]}
            result = actions[cmd]() if cmd in actions else {'status': 'failed', 'message': 'unknown command'}
    except Exception as exc:
        result = {'status': 'failed', 'message': 'crashed: %r' % (exc,)}
    print(json.dumps(result))
