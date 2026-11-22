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
    Bufferbloat test, run on the firewall: idle, then download, upload and both
    directions under load, while ICMP echo probes measure the round trip to the WAN gateway
    and to a few reflectors five times a second.

      bbtest.py start     start the test as a background job (refused while an apply waits
                          for its confirmation, or while another test runs)
      bbtest.py run       the job itself (started by "start")
      bbtest.py status    progress and the samples so far (JSON)
      bbtest.py cancel    stop the job and its load processes
      bbtest.py results   the last 20 results (JSON)

    Headline number: ADDED delay = loaded round trip minus idle round trip (median), per phase;
    graded by the worse of download and upload like the Waveform test (A+ < 5 ms, A < 30,
    B < 60, C < 200, D < 400, else F) (waveform.com bufferbloat test bands).
    Load: curl against the configured download/upload URLs (defaults: Cloudflare's speed test
    endpoints, named on the page), N parallel streams, optionally rate-capped.
"""
import json
import os
import select
import signal
import socket
import statistics
import struct
import sys
import time
import xml.etree.ElementTree as ET
from lib import run, CONFIG, parse_cake_stats

STATE_DIR = '/var/run/shaperinsight-bb'
PROGRESS = os.path.join(STATE_DIR, 'progress.json')
PIDFILE = os.path.join(STATE_DIR, 'job.pid')
# the one lock shared with apply and Remove (apply.py LOCK): a test never runs through an apply
LOCK = '/var/run/shaperinsight.lock'
RESULTS = '/var/db/shaperinsight/bufferbloat.json'
APPLY_STATE = '/conf/shaperinsight-apply.json'
CURL = '/usr/local/bin/curl'
KEEP = 20
PROBE_HZ = 5
GRADES = ((5, 'A+'), (30, 'A'), (60, 'B'), (200, 'C'), (400, 'D'))


def settings():
    root = ET.parse(CONFIG).getroot()
    node = root.find('./OPNsense/ShaperInsight/bufferbloat')

    def get(name, default):
        v = node.findtext(name) if node is not None else None
        return v if v not in (None, '') else default
    return {
        'download_url': get('download_url', 'https://speed.cloudflare.com/__down?bytes=90000000'),
        'upload_url': get('upload_url', 'https://speed.cloudflare.com/__up'),
        'reflectors': [r for r in get('reflectors', '1.1.1.1,9.9.9.9').split(',') if r][:5],
        'streams': max(1, min(16, int(get('streams', '4')))),
        'phase_seconds': max(10, min(60, int(get('phase_seconds', '15')))),
        'cap_mbit': max(0, int(get('cap_mbit', '0'))),
        'label': get('label', ''),
    }, root


def default_route():
    """ :return: (gateway, interface) of the IPv4 default route """
    _, out, _ = run(['/sbin/route', '-n', 'get', '-inet', 'default'])
    gw = ifname = ''
    for line in out.splitlines():
        k, _, v = line.strip().partition(':')
        if k == 'gateway':
            gw = v.strip()
        elif k == 'interface':
            ifname = v.strip()
    return gw, ifname


def shaped(root, ifname):
    """ does a shaper rule use this interface? (the test then measures the shaper) """
    names = [el.tag for el in root.find('./interfaces') if el.findtext('if') == ifname]
    return any(r.findtext('enabled') == '1' and r.findtext('interface') in names
               for r in root.findall('./OPNsense/TrafficShaper/rules/rule'))


def if_bytes(ifname):
    _, out, _ = run(['/usr/bin/netstat', '-I', ifname, '-bn'])
    for line in out.splitlines()[1:]:
        p = line.split()
        if len(p) >= 11 and p[2].startswith('<Link'):
            return int(p[7]), int(p[10])
    return None


def cake_sample():
    """ per CAKE pipe and tin: bytes, drops (counters) and average delay in us (max over instances) """
    _, out, _ = run(['/sbin/sysctl', '-n', 'net.inet.ip.dummynet.cake.stats'])
    res = {}
    for sched, insts in parse_cake_stats(out).items():
        for inst in insts:
            for t in inst['tins']:
                key = '%s/%s' % (sched, t['name'])
                cur = res.setdefault(key, {'bytes': 0, 'drops': 0, 'delay': 0})
                cur['bytes'] += t.get('bytes', 0)
                cur['drops'] += t.get('drops', 0)
                cur['delay'] = max(cur['delay'], t.get('avg_delay', 0))
    return res


def checksum(data):
    if len(data) % 2:
        data += b'\0'
    s = sum(struct.unpack('!%dH' % (len(data) // 2), data))
    s = (s >> 16) + (s & 0xffff)
    s += s >> 16
    return ~s & 0xffff


class Prober:
    """ ICMP echo to every target PROBE_HZ times a second over one raw socket """
    def __init__(self, targets):
        self.targets = targets
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
        self.sock.setblocking(False)
        self.ident = os.getpid() & 0xffff
        self.seq = 0
        self.sent = {}          # seq -> (target, t_sent)

    def send(self, now):
        for target in self.targets:
            self.seq = (self.seq + 1) & 0xffff
            body = struct.pack('!d', now)
            hdr = struct.pack('!BBHHH', 8, 0, 0, self.ident, self.seq)
            pkt = struct.pack('!BBHHH', 8, 0, checksum(hdr + body), self.ident, self.seq) + body
            try:
                self.sock.sendto(pkt, (target, 0))
                self.sent[self.seq] = (target, now)
            except OSError:
                pass

    def receive(self):
        """ :return: [(target, t_sent, rtt_ms)] of the replies waiting on the socket """
        got = []
        while True:
            try:
                data, addr = self.sock.recvfrom(2048)
            except (BlockingIOError, InterruptedError):
                break
            now = time.monotonic()
            ihl = (data[0] & 0x0f) * 4
            if len(data) < ihl + 8:
                continue
            typ, _, _, ident, seq = struct.unpack('!BBHHH', data[ihl:ihl + 8])
            if typ == 0 and ident == self.ident and seq in self.sent and self.sent[seq][0] == addr[0]:
                target, t0 = self.sent.pop(seq)
                got.append((target, t0, (now - t0) * 1000.0))
        return got

    def expire(self, now, timeout=2.0):
        """ :return: [(target, t_sent)] of probes without a reply after timeout s: lost """
        lost = [(s, v) for s, v in self.sent.items() if now - v[1] > timeout]
        for s, _ in lost:
            del self.sent[s]
        return [v for _, v in lost]


class Load:
    """ N curl streams per direction; a stream that ends early is started again """
    def __init__(self, cfg, seconds):
        self.cfg = cfg
        self.procs = {'down': [], 'up': []}
        self.until = 0
        self.seconds = seconds
        self.zero = os.open('/dev/zero', os.O_RDONLY)
        self.null = os.open(os.devnull, os.O_WRONLY)

    def _spawn(self, direction):
        left = max(1, int(self.until - time.monotonic()) + 1)
        argv = [CURL, '-s', '-f', '-o', '/dev/null', '--max-time', str(left)]
        if self.cfg['cap_mbit']:
            # per stream, in bytes/s
            argv += ['--limit-rate', str(int(self.cfg['cap_mbit'] * 125000 / self.cfg['streams']))]
        if direction == 'down':
            argv += [self.cfg['download_url']]
            stdin = self.null
        else:
            argv += ['-X', 'POST', '-H', 'Content-Type: application/octet-stream', '-T', '-',
                     self.cfg['upload_url']]
            stdin = self.zero
        return os.posix_spawn(CURL, argv, os.environ, file_actions=[
            (os.POSIX_SPAWN_DUP2, stdin, 0), (os.POSIX_SPAWN_DUP2, self.null, 1),
            (os.POSIX_SPAWN_DUP2, self.null, 2)])

    def start(self, directions, until):
        self.until = until
        for d in directions:
            self.procs[d] = [self._spawn(d) for _ in range(self.cfg['streams'])]

    def tick(self):
        """ reap ended streams, restart them while the phase lasts """
        for d, pids in self.procs.items():
            for i, pid in enumerate(pids):
                try:
                    done, _ = os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    done = pid
                if done and time.monotonic() < self.until - 1:
                    pids[i] = self._spawn(d)
                elif done:
                    pids[i] = 0
            self.procs[d] = [p for p in pids if p]

    def stop(self):
        for d, pids in self.procs.items():
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                    os.waitpid(pid, 0)
                except (ProcessLookupError, ChildProcessError):
                    pass
            self.procs[d] = []


def pct(values, q):
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(round(q / 100.0 * (len(v) - 1))))], 2)


def grade(added_ms):
    if added_ms is None:
        return '?'
    for limit, g in GRADES:
        if added_ms < limit:
            return g
    return 'F'


def write_progress(data):
    tmp = PROGRESS + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f)
    os.replace(tmp, PROGRESS)


def summarise(samples, targets, gateway, phases):
    """ per phase and target: p50/p95/p99 round trip, loss; added delay against idle """
    out = {}
    for name, _, _ in phases:
        per = {}
        for t in targets:
            rtts = [s['rtt'] for s in samples['probes'] if s['phase'] == name and s['target'] == t and s['rtt'] is not None]
            lost = sum(1 for s in samples['probes'] if s['phase'] == name and s['target'] == t and s['rtt'] is None)
            n = len(rtts) + lost
            per[t] = {'p50': pct(rtts, 50), 'p95': pct(rtts, 95), 'p99': pct(rtts, 99), 'n': n,
                      'loss_pct': round(100.0 * lost / n, 1) if n else None}
        out[name] = {'targets': per}
    idle = out.get('idle', {}).get('targets', {})
    for name in out:
        for t, v in out[name]['targets'].items():
            base = idle.get(t, {}).get('p50')
            for q in ('p50', 'p95', 'p99'):
                v['added_' + q] = round(v[q] - base, 2) if v[q] is not None and base is not None else None
        # reflectors decide; the gateway is shown on its own (it may answer from a busy CPU)
        refl = [v['added_p50'] for t, v in out[name]['targets'].items() if t != gateway and v['added_p50'] is not None]
        out[name]['added_p50'] = round(statistics.median(refl), 2) if refl else None
        rates = [s for s in samples['rates'] if s['phase'] == name]
        out[name]['down_mbit'] = round(statistics.median([r['down'] for r in rates]), 1) if rates else None
        out[name]['up_mbit'] = round(statistics.median([r['up'] for r in rates]), 1) if rates else None
        out[name]['rpm'] = None
        p50s = [v['p50'] for t, v in out[name]['targets'].items() if t != gateway and v['p50']]
        if p50s:
            out[name]['rpm'] = int(60000 / statistics.median(p50s))
    worst = max([out[p]['added_p50'] for p in ('download', 'upload') if out.get(p, {}).get('added_p50') is not None],
                default=None)
    return out, worst


def public(samples):
    return {k: v for k, v in samples.items() if k != 'probes'}


def job():
    cfg, root = settings()
    gateway, ifname = default_route()
    targets = ([gateway] if gateway and gateway[0].isdigit() else []) + [r for r in cfg['reflectors'] if r != gateway]
    ps = cfg['phase_seconds']
    phases = [('idle', 10, []), ('download', ps, ['down']), ('upload', ps, ['up']),
              ('both', ps, ['down', 'up']), ('cooldown', 5, [])]
    total = sum(p[1] for p in phases)
    info = {'state': 'running', 'started': int(time.time()), 'total': total, 'interface': ifname,
            'gateway': gateway, 'targets': targets, 'shaped': shaped(root, ifname), 'settings': cfg,
            'phases': [{'name': n, 'seconds': s} for n, s, _ in phases]}
    # raw probes stay in memory for the percentiles; the page gets one value per second
    samples = {'probes': [], 'latency': [], 'rates': [], 'cake': []}
    prober = Prober(targets)
    load = Load(cfg, ps)
    t_start = time.monotonic()
    last_bytes = if_bytes(ifname) if ifname else None
    last_cake = cake_sample()
    agg_from = 0        # first raw probe not yet folded into the per-second series
    try:
        for name, seconds, dirs in phases:
            info['phase'] = name
            p_end = time.monotonic() + seconds
            load.start(dirs, p_end)
            next_probe = next_sec = time.monotonic()
            while time.monotonic() < p_end:
                now = time.monotonic()
                if now >= next_probe:
                    prober.send(now)
                    next_probe += 1.0 / PROBE_HZ
                for target, t0, rtt in prober.receive():
                    samples['probes'].append({'t': round(t0 - t_start, 2), 'phase': name, 'target': target,
                                              'rtt': round(rtt, 2)})
                for target, t0 in prober.expire(now):
                    samples['probes'].append({'t': round(t0 - t_start, 2), 'phase': name, 'target': target,
                                              'rtt': None})
                if now >= next_sec:
                    next_sec += 1.0
                    load.tick()
                    t = round(now - t_start, 1)
                    cur = if_bytes(ifname) if ifname else None
                    if cur and last_bytes:
                        samples['rates'].append({'t': t, 'phase': name, 'down': round((cur[0] - last_bytes[0]) * 8 / 1e6, 2),
                                                 'up': round((cur[1] - last_bytes[1]) * 8 / 1e6, 2)})
                    last_bytes = cur
                    ck = cake_sample()
                    samples['cake'].append({'t': t, 'phase': name, 'tins': {
                        k: {'delay_ms': round(v['delay'] / 1000.0, 2),
                            'drops': v['drops'] - last_cake.get(k, {}).get('drops', 0),
                            'mbit': round((v['bytes'] - last_cake.get(k, {}).get('bytes', 0)) * 8 / 1e6, 2)}
                        for k, v in ck.items()}})
                    last_cake = ck
                    fresh = samples['probes'][agg_from:]
                    agg_from = len(samples['probes'])
                    row = {'t': t, 'phase': name}
                    for target in targets:
                        r = [x['rtt'] for x in fresh if x['target'] == target and x['rtt'] is not None]
                        row[target] = round(statistics.median(r), 2) if r else None
                    samples['latency'].append(row)
                    info['elapsed'] = int(now - t_start)
                    write_progress(dict(info, samples=public(samples)))
                # wait on the socket, not a fixed sleep: a reply is timestamped when it is read,
                # so a sleep would add up to its own length to every round trip
                wake = min(next_probe, next_sec, p_end) - time.monotonic()
                if wake > 0:
                    select.select([prober.sock], [], [], wake)
            load.stop()
        summary, worst = summarise(samples, targets, gateway, phases)
        result = dict(info, state='done', samples=public(samples), summary=summary, added_worst=worst,
                      grade=grade(worst), finished=int(time.time()))
    except Exception as exc:        # the progress file must always end
        load.stop()
        result = dict(info, state='failed', error=str(exc)[:200], samples=public(samples))
    write_progress(result)
    if result['state'] == 'done':
        os.makedirs(os.path.dirname(RESULTS), exist_ok=True)
        try:
            with open(RESULTS) as f:
                hist = json.load(f)
        except (OSError, ValueError):
            hist = []
        hist = ([result] + hist)[:KEEP]
        with open(RESULTS + '.tmp', 'w') as f:
            json.dump(hist, f)
        os.replace(RESULTS + '.tmp', RESULTS)


def apply_waiting():
    try:
        with open(APPLY_STATE) as f:
            return json.load(f).get('state') in ('running', 'waiting')
    except (OSError, ValueError):
        return False


def lock(block=False):
    import fcntl
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if block else fcntl.LOCK_NB))
        return fd
    except OSError:
        os.close(fd)
        return None


def live_pid():
    """ the pid of the running test job, or None. The pidfile alone is not proof: daemon(8) holds
    an flock on it (pidfile(3)) while the job lives, so a file nobody locks is stale (an abnormal
    end leaves it), and its pid may since belong to another process; the pid's command line must
    also be ours before anything is signalled (review S10) """
    import fcntl
    try:
        fd = os.open(PIDFILE, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            stale = True            # nobody holds it: daemon is gone
        except OSError:
            stale = False
        if stale:
            try:
                os.unlink(PIDFILE)
            except OSError:
                pass
            return None
        pid = int(os.read(fd, 32).decode().strip() or '0')
    except ValueError:
        return None
    finally:
        os.close(fd)
    if pid <= 1:
        return None
    rc, out, _ = run(['/bin/ps', '-o', 'command=', '-p', str(pid)])
    return pid if rc == 0 and 'bbtest.py' in out and ' run' in out else None


def job_alive():
    """ the test job still exists; the lock alone cannot tell, because apply and Remove share it """
    return live_pid() is not None


def main(cmd):
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    if cmd == 'start':
        if apply_waiting():
            return {'status': 'failed', 'message': 'an apply is waiting for its confirmation; test after it'}
        fd = lock()
        if fd is None:
            return {'status': 'failed', 'message': 'a test, an apply or a Remove is running; try again after it'}
        # the lock is handed to the job, so there is no moment where it is free (review L7)
        os.set_inheritable(fd, True)
        write_progress({'state': 'starting'})
        rc, _, err = run(['/usr/sbin/daemon', '-f', '-p', PIDFILE, sys.executable, os.path.abspath(__file__), 'run',
                          str(fd)])
        os.close(fd)
        return {'status': 'started' if rc == 0 else 'failed', 'message': err.strip()}
    if cmd == 'run':
        import fcntl
        try:
            fd = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None
            if fd is None:
                fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return None
        os.setpgid(0, 0)
        job()
        return None
    if cmd == 'cancel':
        pid = live_pid()
        if pid is not None:
            try:
                os.killpg(pid, signal.SIGTERM)
            except OSError:
                pass
        try:
            with open(PROGRESS) as f:
                p = json.load(f)
        except (OSError, ValueError):
            p = {}
        if p.get('state') in ('running', 'starting'):
            p['state'] = 'cancelled'
            write_progress(p)
        return {'status': 'ok'}
    if cmd == 'status':
        try:
            with open(PROGRESS) as f:
                p = json.load(f)
        except (OSError, ValueError):
            return {'state': 'none'}
        if p.get('state') in ('running', 'starting') and not job_alive():
            p['state'] = 'failed'
            p['error'] = 'the test stopped unexpectedly'
        return p
    if cmd == 'results':
        try:
            with open(RESULTS) as f:
                return {'results': json.load(f)}
        except (OSError, ValueError):
            return {'results': []}
    return {'status': 'failed', 'message': 'unknown command'}


if __name__ == '__main__':
    out = main(sys.argv[1] if len(sys.argv) > 1 else 'status')
    if out is not None:
        print(json.dumps(out))
