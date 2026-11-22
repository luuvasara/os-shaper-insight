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
    Read-only collection of the traffic shaper's state: the saved configuration, the
    objects loaded in the kernel, the CAKE module status from os-cake (when installed)
    and the per-tin CAKE counters.

    Cost rules (see TESTING.md, "Performance budget"):
      - one snapshot forks dnctl once (all "show" commands from one command file),
        ipfw once, kldstat once and sysctl twice;
      - a snapshot is cached for SNAPSHOT_TTL seconds and shared by every caller
        (status page, widget, compiled page), under a lock so that concurrent polls
        don't each fork the tools;
      - os-cake's own status (which hashes dummynet.ko and runs its own dnctl) is cached
        until config.xml or the loaded module list changes, or OSCAKE_TTL passes.
"""
import fcntl
import json
import os
import re
import stat
import time
# xml.etree, select and signal are imported where they are used: a status
# poll answered from the cached snapshot needs none of them, and they cost ~25 ms of
# start-up per poll on a small CPU (python -X importtime, TESTING.md "Performance")

CONFIG = '/conf/config.xml'
CACHE_DIR = '/var/run/shaperinsight'
KLD_TTL = 30.0
SNAPSHOT_TTL = 3.0
OSCAKE_TTL = 300.0
OSCAKE_STATUS = '/usr/local/opnsense/scripts/cake/status.py'
OSCAKE_DIR = '/usr/local/opnsense/scripts/cake'
CAKE_DNCTL = '/usr/local/libexec/cake/dnctl'
BASE_DNCTL = '/sbin/dnctl'
IPFW = '/sbin/ipfw'
SYSCTL = '/sbin/sysctl'
KLDSTAT = '/sbin/kldstat'
VERSION_DIR = '/usr/local/opnsense/version'
SHAPER_FIRST_RULE = 60000
SHAPER_LAST_RULE = 65535
# files the core Shaper/IPFW templates write (IPFW/+TARGETS, Shaper/+TARGETS)
DNCTL_CONF = '/usr/local/etc/dnctl.conf'
IPFW_RULES = '/usr/local/etc/ipfw.rules'

SYSCTLS = ['kern.hz', 'kern.vm_guest', 'net.pf.share_forward', 'net.pf.share_forward6',
           'net.inet.ip.fw.one_pass', 'net.inet.ip.dummynet.io_pkt_drop']
CAKE_STATS = 'net.inet.ip.dummynet.cake.stats'

# tin names by CAKE diffserv mode, in kernel tin order (cake-kmod / sch_cake)
TINS = {
    0: ['Best Effort'],
    1: ['Best Effort', 'Bulk', 'Voice'],
    2: ['Best Effort', 'Bulk', 'Video', 'Voice'],
}
DIFFSERV_NAME = {0: 'besteffort', 1: 'diffserv3', 2: 'diffserv4', 3: 'diffserv8', 4: 'precedence'}

UNIT = {'bit': 1, 'kbit': 1000, 'mbit': 1000 ** 2, 'gbit': 1000 ** 3}


def run(argv, timeout=10):
    """ run a fixed command, never through a shell
    os.posix_spawn with two pipes instead of subprocess: importing subprocess and its Popen
    set-up cost ~25 ms CPU per status poll on a small firewall (cProfile, TESTING.md)
    :return: (returncode, stdout, stderr); returncode -1 on spawn failure or timeout
    """
    import select
    import signal
    out_r, out_w = os.pipe()
    err_r, err_w = os.pipe()
    try:
        null = os.open(os.devnull, os.O_RDONLY)
        pid = os.posix_spawn(argv[0], argv, os.environ,
                             file_actions=[(os.POSIX_SPAWN_DUP2, null, 0), (os.POSIX_SPAWN_DUP2, out_w, 1),
                                           (os.POSIX_SPAWN_DUP2, err_w, 2)])
        os.close(null)
    except OSError as exc:
        for fd in (out_r, out_w, err_r, err_w):
            os.close(fd)
        return -1, '', str(exc)
    os.close(out_w)
    os.close(err_w)
    bufs = {out_r: [], err_r: []}
    deadline = time.monotonic() + timeout
    timed_out = False
    while bufs and not timed_out:
        left = deadline - time.monotonic()
        ready = select.select(list(bufs), [], [], max(left, 0))[0] if left > 0 else []
        if not ready:
            timed_out = True
            break
        for fd in ready:
            chunk = os.read(fd, 65536)
            if chunk:
                bufs[fd].append(chunk)
            else:
                os.close(fd)
                out = bufs.pop(fd)
                if fd == out_r:
                    stdout = out
                else:
                    stderr = out
    if timed_out:
        os.kill(pid, signal.SIGKILL)
        for fd in list(bufs):
            os.close(fd)
        os.waitpid(pid, 0)
        return -1, '', 'timed out after %d s: %s' % (timeout, argv[0])
    status = os.waitpid(pid, 0)[1]
    rc = os.waitstatus_to_exitcode(status)
    return rc, b''.join(stdout).decode('utf-8', 'replace'), b''.join(stderr).decode('utf-8', 'replace')


def dnctl_path():
    """ cake-kmod's dnctl prints CAKE's options; the stock one lists them as "type CAKE" only """
    return CAKE_DNCTL if os.access(CAKE_DNCTL, os.X_OK) else BASE_DNCTL


def bits(value, metric):
    """ configured bandwidth to bit/s, None when unset """
    try:
        return int(float(value) * UNIT[(metric or 'bit').lower()])
    except (TypeError, ValueError, KeyError):
        return None


def parse_rate(text):
    """ "910.000 Mbit/s" -> 910000000 """
    m = re.match(r'([\d.]+)\s*([KMG]?)bit/s', text)
    if not m:
        return None
    return int(round(float(m.group(1)) * {'': 1, 'K': 1000, 'M': 1000 ** 2, 'G': 1000 ** 3}[m.group(2)]))


# ------------------------------------------------------------------ configuration

def _items(root, path):
    out = []
    for el in root.findall(path):
        item = {c.tag: (c.text or '').strip() for c in el}
        item['uuid'] = el.get('uuid', '')
        out.append(item)
    return out


def config_root(path=CONFIG, tries=5):
    """ config.xml parsed under a shared flock, retried: core's Config::save truncates and rewrites
    the file in place under LOCK_EX, so an unlocked read can see half a file (Verification 4, N10:
    a save loop made every unlocked reader fail now and then).
    :raises: the last OSError/ParseError after `tries` attempts """
    import fcntl
    import xml.etree.ElementTree as ET
    last = None
    for attempt in range(tries):
        if attempt:
            time.sleep(0.2)
        try:
            with open(path, 'rb') as f:
                deadline = time.monotonic() + 2
                while True:
                    try:
                        fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(0.05)
                try:
                    return ET.fromstring(f.read())
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except (OSError, ET.ParseError) as exc:
            last = exc
    raise last


def read_config(path=CONFIG):
    """ the shaper and os-cake sections of config.xml (read-only)
    :return: dict with pipes, queues, rules, cake (lists of flat dicts), cake_general
    """
    root = config_root(path)
    result = {
        'pipes': _items(root, './OPNsense/TrafficShaper/pipes/pipe'),
        'queues': _items(root, './OPNsense/TrafficShaper/queues/queue'),
        'rules': _items(root, './OPNsense/TrafficShaper/rules/rule'),
        'cake': _items(root, './OPNsense/Cake/pipes/pipe'),
        'cake_general': {},
        'interfaces': {},
    }
    general = root.find('./OPNsense/Cake/general')
    if general is not None:
        result['cake_general'] = {c.tag: (c.text or '').strip() for c in general}
    ifs = root.find('./interfaces')
    if ifs is not None:
        for el in ifs:
            result['interfaces'][el.tag] = {
                'if': el.findtext('if') or '',
                'descr': el.findtext('descr') or el.tag.upper(),
                'enable': el.find('enable') is not None or el.tag in ('wan', 'lan'),
            }
    return result


# ------------------------------------------------------------------ kernel

def parse_dnctl(text):
    """ parse the output of "queue show" followed by "sched show" (one dnctl run).
    Everything before the first "NNNNN:" pipe header is the queue listing.
    :return: dict pipes {n: {...}}, queues {n: {...}}
    """
    pipes, queues = {}, {}
    lines = text.splitlines()
    i = 0
    while i < len(lines) and not re.match(r'^\d{5}:', lines[i]):
        m = re.match(r'^q(\d+)\s+(\d+)\s+(\S+)\s+(\d+) flows \((\d+) buckets\) sched (\d+) weight (\d+)', lines[i])
        if m and int(m.group(1)) < 65536:
            queues[int(m.group(1))] = {
                'sched': int(m.group(6)), 'weight': int(m.group(7)), 'flows': int(m.group(4)),
                'line': lines[i].strip()}
        i += 1
    cur = None
    for line in lines[i:]:
        m = re.match(r'^(\d{5}):\s+(.*?bit/s)\s+(\d+) ms\s+burst (\d+)', line)
        if m:
            cur = int(m.group(1))
            pipes[cur] = {'bandwidth': parse_rate(m.group(2)), 'bandwidth_text': m.group(2).strip(),
                          'delay_ms': int(m.group(3)), 'type': '?', 'params': '', 'children': [],
                          'buckets': 0, 'active': 0, 'mask': False, 'lines': [line.rstrip()]}
            continue
        if cur is None:
            continue
        pipes[cur]['lines'].append(line.rstrip())
        m = re.match(r'^\s*sched (\d+) type (\S+) flags (0x[0-9a-f]+) (\d+) buckets (\d+) active', line)
        if m and int(m.group(1)) == cur:
            pipes[cur].update({'type': m.group(2), 'flags': m.group(3), 'buckets': int(m.group(4)),
                               'active': int(m.group(5)), 'mask': int(m.group(3), 16) & 1 == 1})
            continue
        m = re.match(r'^\s*Children flowsets:\s*(.*)', line)
        if m:
            pipes[cur]['children'] = [int(x) for x in m.group(1).split() if x.isdigit()]
            continue
        stripped = line.strip()
        if pipes[cur]['type'] != '?' and not pipes[cur]['params'] and \
                stripped.upper().startswith(pipes[cur]['type'].upper() + ' '):
            pipes[cur]['params'] = stripped
    return {'pipes': pipes, 'queues': queues}


def parse_ipfw(text):
    """ shaper rules from "ipfw show 60000-65535": counters keyed by the uuid comment """
    rules, other = {}, []
    for line in text.splitlines():
        m = re.match(r'^(\d+)\s+(\d+)\s+(\d+)\s+(.*?)\s*//\s*([0-9a-f-]{36})\s*(.*)$', line)
        if m:
            rules[m.group(5)] = {'num': int(m.group(1)), 'pkts': int(m.group(2)),
                                 'bytes': int(m.group(3)), 'body': m.group(4), 'comment': m.group(6)}
        elif line.strip():
            other.append(line.rstrip())
    return {'rules': rules, 'other': other}


def parse_kldstat(text):
    names = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].isdigit():
            names.append(parts[4])
    return {'modules': names, 'dummynet': 'dummynet.ko' in names, 'dn_cake': 'dn_cake.ko' in names,
            'dummynet_st': 'dummynet_st.ko' in names, 'ipfw': 'ipfw.ko' in names}


def parse_sysctl_e(text):
    """ "name=value" lines from sysctl -ie """
    out = {}
    for line in text.splitlines():
        if '=' in line:
            k, v = line.split('=', 1)
            out[k.strip()] = v.strip()
    return out


def parse_cake_stats(text):
    """ net.inet.ip.dummynet.cake.stats (cake-kmod >= 1.3): one line per sched/instance/tin
    :return: {sched: [ {inst, tins: [..]} ]}
    """
    result = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 8 or len(parts) % 2:
            continue
        try:
            tin = {k: int(v) for k, v in zip(parts[0::2], parts[1::2])}
        except ValueError:
            continue
        if 'sched' not in tin or 'tin' not in tin:
            continue
        names = TINS.get(tin.get('diffserv', 0), [])
        tin['name'] = names[tin['tin']] if tin['tin'] < len(names) else 'Tin %d' % tin['tin']
        result.setdefault(tin['sched'], {}).setdefault(tin.get('inst', 0), []).append(tin)
    return {s: [{'inst': i, 'tins': sorted(t, key=lambda x: x['tin'])} for i, t in sorted(insts.items())]
            for s, insts in result.items()}


def read_kernel(kldstat_text=None):
    """ :param kldstat_text: recent kldstat output to reuse instead of running kldstat """
    """ one run of each tool; returns raw text too, for the compiled page """
    raw, timing = {}, {}
    t0 = time.monotonic()
    # one dnctl run for both listings: dnctl reads commands from a file (as rc.d/dnctl does)
    cmdfile = os.path.join(CACHE_DIR, 'show.cmd')
    want = 'queue show\nsched show\n'
    try:
        fd = os.open(cmdfile, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as f:
            ok = f.read() == want
    except OSError:
        ok = False
    if not ok:
        _write_text(cmdfile, want)
    rc, out, err = run([dnctl_path(), cmdfile])
    raw['dnctl'] = out
    raw['dnctl_error'] = err.strip() if rc != 0 and 'Protocol not available' not in err else ''
    timing['dnctl'] = time.monotonic() - t0

    t0 = time.monotonic()
    # every counter source is stamped at its own read (the middle of the run), not when the
    # snapshot started: a stamp taken before the config parse and the os-cake status could be
    # a second off and inflate a rate by 20 % (TESTING.md speed-test analysis the "900 on an 840 pipe")
    w0 = time.time()
    rc, out, err = run([IPFW, 'show', '%d-%d' % (SHAPER_FIRST_RULE, SHAPER_LAST_RULE)])
    counters_ts = {'ipfw': (w0 + time.time()) / 2}
    raw['ipfw'] = out
    # ipfw.ko is only loaded once the shaper (or captive portal) has rules: not an error
    raw['ipfw_error'] = err.strip() if rc != 0 and 'Protocol not available' not in err else ''
    timing['ipfw'] = time.monotonic() - t0

    t0 = time.monotonic()
    raw['kldstat'] = kldstat_text if kldstat_text is not None else run([KLDSTAT])[1]
    # one sysctl run; the multi-line CAKE statistics come last, so everything after its
    # "name=" belongs to it
    w0 = time.time()
    out = run([SYSCTL, '-ie'] + SYSCTLS + [CAKE_STATS])[1]
    counters_ts['cake'] = (w0 + time.time()) / 2
    head, sep, stats = out.partition(CAKE_STATS + '=')
    raw['sysctl'] = head.strip()
    raw['cake_stats'] = stats.strip() if sep else ''
    rc = 0 if sep else 1
    timing['sysctl_kldstat'] = time.monotonic() - t0

    sysctls = parse_sysctl_e(raw['sysctl'])
    return {
        'dnctl': parse_dnctl(raw['dnctl']),
        'ipfw': parse_ipfw(raw['ipfw']),
        'kld': parse_kldstat(raw['kldstat']),
        'sysctl': sysctls,
        'cake_stats': parse_cake_stats(raw['cake_stats']),
        'cake_stats_available': rc == 0,
        'raw': raw,
        'timing': timing,
        'counters_ts': counters_ts,
    }


# ------------------------------------------------------------------ os-cake

def oscake_installed():
    return os.path.isfile(OSCAKE_STATUS)


def oscake_status(kld_signature):
    """ os-cake's own status JSON (the same as /api/cake/service/status), cached until
    config.xml or the loaded modules change, OSCAKE_TTL at most
    :return: dict or None when os-cake is not installed
    """
    if not oscake_installed():
        return None
    key = '%s|%s' % (_mtime_ns(CONFIG), kld_signature)
    path = os.path.join(CACHE_DIR, 'oscake.json')
    try:
        cached = _read_json(path)
        if cached.get('key') == key and time.time() - cached.get('ts', 0) < OSCAKE_TTL:
            cached['data']['_cached'] = True
            return cached['data']
    except (OSError, ValueError):
        pass
    rc, out, err = run(['/usr/local/bin/python3', OSCAKE_STATUS], timeout=30)
    try:
        data = json.loads(out)
    except ValueError:
        data = {'status': 'failed', 'error': (err or out).strip()[:300]}
    _write_json(path, {'key': key, 'ts': time.time(), 'data': data})
    return data


def oscake_rewrite(config_text):
    """ what os-cake's wrapper feeds dnctl: the core dnctl.conf with each CAKE pipe's
    "type X" swapped for "type cake <args>", CoDel/PIE keywords dropped. The per-pipe
    arguments come from os-cake's own lib (cake_pipes), run in its directory; the line
    rewrite mirrors os-cake dnctl.py cake_rule() (BSD-2-Clause, Tim Pletcher).
    :return: (text or None, errors list)
    """
    if not os.path.isdir(OSCAKE_DIR):
        return None, []
    code = ('import json,sys; sys.path.insert(0, %r); from lib import cake_pipes; e = []; '
            'p = cake_pipes(e); print(json.dumps({"pipes": p, "errors": e}))' % OSCAKE_DIR)
    rc, out, err = run(['/usr/local/bin/python3', '-c', code], timeout=20)
    try:
        data = json.loads(out)
    except ValueError:
        return None, ['os-cake lib: %s' % (err or out).strip()[:200]]
    pipes = data.get('pipes', {})
    lines = []
    for line in config_text.splitlines(True):
        parts = line.split()
        if len(parts) > 3 and parts[0] == 'pipe' and parts[2] == 'config' and parts[1] in pipes \
                and 'type' in parts:
            idx = parts.index('type')
            params = [x for x in parts[idx + 2:] if x not in ('codel', 'pie')]
            line = ' '.join(parts[:idx] + ['type', 'cake'] + params + pipes[parts[1]]) + '\n'
        lines.append(line)
    return ''.join(lines), data.get('errors', [])


# ------------------------------------------------------------------ versions

def versions():
    out = {'kernel': ' '.join(os.uname()[2:4])}
    for name, key in (('core', 'opnsense'), ('shaper-insight', 'plugin'), ('cake', 'os_cake')):
        try:
            with open(os.path.join(VERSION_DIR, name)) as f:
                data = json.load(f)
            out[key] = data.get('product_version') or data.get('version')
        except (OSError, ValueError):
            out[key] = None
    for name, path in (('cake_kmod', '/usr/local/libexec/cake/kernel.version'),):
        out[name + '_built_for'] = _read(path)
    return out


# ------------------------------------------------------------------ snapshot + cache

def _mtime_ns(path):
    """ change marker of a file: mtime in ns plus size and inode (an edit within one second
    still changes the ns mtime; a replace changes the inode) """
    try:
        st = os.stat(path)
        return '%d:%d:%d' % (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        return ''


def _mtime(path):
    try:
        return int(os.stat(path).st_mtime)
    except OSError:
        return 0


def _read(path, limit=1 << 20):
    try:
        with open(path) as f:
            return f.read(limit).strip()
    except OSError:
        return None


class CacheDirError(RuntimeError):
    pass


def cache_dir(path=None):
    """ the private cache directory: created 0700 when missing; refused when it is a
    symlink, not a directory, not owned by us (root under configd) or open to others
    :return: path
    """
    path = path or CACHE_DIR
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise CacheDirError('%s is not a plain directory' % path)
    if st.st_uid != os.geteuid() or st.st_mode & 0o077:
        raise CacheDirError('%s must be owned by uid %d with mode 0700' % (path, os.geteuid()))
    return path


def _read_json(path):
    """ read a cache file without following a symlink """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as f:
        return json.load(f)


def _write_text(path, text):
    """ write a cache file atomically: a new file with a random name (O_EXCL, so a planted
    file or symlink is never opened), then rename over the target """
    tmp = os.path.join(os.path.dirname(path), '.tmp.%s' % os.urandom(8).hex())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_json(path, data):
    _write_text(path, json.dumps(data))


def snapshot(max_age=SNAPSHOT_TTL):
    """ configuration + kernel + os-cake status, cached and shared between callers
    :param max_age: seconds a cached snapshot may be old; 0 forces a new one
    :return: dict
    """
    cache_dir()
    path = os.path.join(CACHE_DIR, 'snapshot.json')
    lock_fd = os.open(os.path.join(CACHE_DIR, 'snapshot.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = None
        try:
            old = _read_json(path)
            if max_age > 0 and time.time() - os.lstat(path).st_mtime < max_age:
                old['cached'] = True
                return old
        except (OSError, ValueError):
            pass
        t0 = time.monotonic()
        snap = {'generated': time.time(), 'cached': False, 'config_mtime': _mtime_ns(CONFIG)}
        if old and old.get('config_mtime') == snap['config_mtime'] and not old.get('config_error'):
            # config.xml unchanged: reuse its parse (and the versions read with it)
            snap['config'], snap['config_error'], snap['versions'] = old['config'], '', old['versions']
        else:
            from xml.etree.ElementTree import ParseError
            try:
                snap['config'] = read_config()
                snap['config_error'] = ''
            except (OSError, ParseError) as exc:
                snap['config'] = {'pipes': [], 'queues': [], 'rules': [], 'cake': [], 'cake_general': {},
                                  'interfaces': {}}
                snap['config_error'] = str(exc)
            snap['versions'] = versions()
        # the module list changes only on kldload/kldunload (os-cake's Apply); re-read it every
        # KLD_TTL seconds (or at once when config.xml changed) instead of on every poll
        reuse = None
        if old and old.get('config_mtime') == snap['config_mtime'] and time.time() - old.get('kld_read', 0) < KLD_TTL:
            reuse = old.get('kernel', {}).get('raw', {}).get('kldstat')
        snap['kernel'] = read_kernel(reuse)
        snap['kld_read'] = old.get('kld_read', 0) if reuse is not None else time.time()
        kld_sig = ' '.join(sorted(snap['kernel']['kld']['modules']))
        t1 = time.monotonic()
        snap['oscake'] = oscake_status(kld_sig)
        snap['kernel']['timing']['oscake'] = time.monotonic() - t1
        snap['kernel']['timing']['total'] = time.monotonic() - t0
        _write_json(path, snap)
        return snap


def apply_countdown():
    """ the running apply's countdown for the banner on every page (one small file read, so the
    regular status poll carries it): None unless an apply waits for its confirmation """
    try:
        st = _read_json('/conf/shaperinsight-apply.json')
    except (OSError, ValueError):
        return None
    left = st.get('deadline', 0) - time.time()
    if st.get('state') != 'waiting' or left <= 0:
        return None
    # a job id, not the confirm token: this feed is in the read-only ACL (review L3)
    return {'seconds_left': int(left), 'job': job_id(st.get('token', ''))}


def job_id(token):
    """ the public name of an apply job: a hash of its confirm token. The UI confirms "this job",
    so a stale tab cannot confirm a newer apply, and the token itself never leaves the box """
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()[:16] if token else ''
