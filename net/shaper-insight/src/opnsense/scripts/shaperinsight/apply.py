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
    Apply with a server-side confirm timer (dead man's switch). Core OPNsense 26.7 has no
    apply-with-revert for the firewall, so the plugin keeps its own:

      apply.py start <down> <up> <skip|disable>  starts the job detached, prints the token
      apply.py run ...                           the job itself (holds the lock throughout)
      apply.py confirm <token>                   keep the change; fails if no timer is waiting
      apply.py status                            state, steps, seconds left
      apply.py boot                              after a reboot: an unconfirmed apply is put back

    The revert is section-scoped: it puts back only the configuration sections an apply
    changes (OPNsense/TrafficShaper, OPNsense/Cake), so whatever else changed during the
    countdown (another admin, DHCP, aliases, other plugins) stays. A full Config::backup() is
    also taken and recorded as a manual safety net; it is never applied automatically.

    The job: restore point -> write the plan -> reconfigure -> compare kernel and config ->
    flush and reload once if stale objects stay loaded -> check "type CAKE" -> wait for the
    confirm call; on timeout or any failure, put the restore point back and reload.
    State lives in /conf (survives a reboot); the lock and confirm files in /var/run.
"""
import fcntl
import json
import os
import secrets
import sys
import time
from lib import snapshot, run, cache_dir, CACHE_DIR, _write_text, _read_json, CAKE_DNCTL, job_id

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = '/conf/shaperinsight-apply.json'
SECTIONS = '/conf/shaperinsight-sections.json'      # the revert's restore point
LEDGER = '/conf/shaperinsight-ledger.json'          # uuids of confirmed objects, for Remove
APPLIED = '/conf/shaperinsight-applied.json'       # the sections right after our write
# ONE lock for everything that changes or measures the shaper: apply (with its countdown),
# Remove and the bufferbloat test (bbtest.py uses the same file). Not inside CACHE_DIR: that
# directory may be emptied, and a deleted lock file lets a second job lock a NEW file while
# the first still runs (reproduced: two overlapping applies left pipes behind).
LOCK = '/var/run/shaperinsight.lock'
BUSY = 'another apply, a Remove or a bufferbloat test is running; wait for it to finish'
PROGRESS = os.path.join(CACHE_DIR, 'apply.progress')
CONFIRM_SECONDS = 60
CONFIGCTL = '/usr/local/sbin/configctl'
WRITER = [os.path.join(HERE, 'writer.php')]


def load_state():
    try:
        return _read_json(STATE)
    except (OSError, ValueError):
        return {}


def load_ledger():
    try:
        return _read_json(LEDGER)
    except (OSError, ValueError):
        return {}


def save_state(st):
    st['updated'] = time.time()
    _write_text(STATE, json.dumps(st))


def progress(line):
    with open(PROGRESS, 'a') as f:
        f.write(line + '\n')


def lock_held():
    """ true while a job (or its countdown) runs: it keeps the lock for its whole life """
    try:
        fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def take_lock():
    """ :return: an fd holding the lock, or None when someone else holds it """
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        os.close(fd)
        return None


def sections_now():
    """ :return: the shaper sections as they are now (writer snapshot), or None """
    tmp = os.path.join(CACHE_DIR, 'sections-now.json')
    ok, _ = writer('snapshot', tmp)
    try:
        return _read_json(tmp) if ok else None
    except (OSError, ValueError):
        return None


def restore(st, why):
    """ put the pre-apply sections back, reload, compare; records the steps in st """
    ok, res = writer('restore-sections', st.get('restore') or SECTIONS)
    st['steps'].append({'name': 'Put the shaper sections back', 'ok': ok,
                        'detail': res.get('message', '') or why})
    ok2, msg = flushreload()
    ok3, detail = settle()
    st['steps'].append({'name': 'Compare kernel and configuration', 'ok': ok3, 'detail': detail})
    st['state'] = 'reverted' if ok and ok2 and ok3 else 'revert-failed'
    st['reason'] = why
    save_state(st)


def recover(st):
    """ a job that stopped without a confirmation (crash, kill, restart). Revert ONLY when the
    shaper sections are exactly what this job wrote (nobody changed them since); otherwise
    ask, and never silently wipe newer edits (review H1) """
    now = sections_now()
    try:
        applied = _read_json(APPLIED)
    except (OSError, ValueError):
        applied = None
    try:
        before = _read_json(st.get('restore') or SECTIONS)
    except (OSError, ValueError):
        before = None
    if now is not None and before is not None and now == before:
        st['state'] = 'failed'
        st['reason'] = 'the job stopped before it changed anything'
        save_state(st)
    elif now is not None and applied is not None and now == applied:
        restore(st, 'the job stopped (restart, crash or kill) before the change was confirmed')
    else:
        st['state'] = 'needs-attention'
        st['reason'] = ('the job stopped before the change was confirmed, and the shaper settings were '
                        'changed since (or could not be compared); choose what to keep')
        st['options'] = {'restore': 'Put back the shaper settings from before the apply (this also '
                                    'undoes the later changes to the shaper and CAKE settings)',
                         'keep': 'Keep the shaper settings as they are now'}
        save_state(st)
    return st['state']


def configd(*args):
    rc, out, err = run([CONFIGCTL] + list(args), timeout=120)
    return rc, (out or err).strip()


def reconfigure():
    """ what Firewall: Shaper "Apply" does (core TrafficShaper ServiceController::reconfigureAction) """
    for tpl in ('OPNsense/Shaper', 'OPNsense/IPFW'):
        rc, out = configd('template', 'reload', tpl)
        # a failed template run would load the previous dnctl.conf/ipfw.rules and still say OK (S13)
        if rc != 0 or out != 'OK':
            return False, 'template %s: %s' % (tpl, out or 'rc %d' % rc)
    for what in ('shaper', 'ipfw'):
        rc, out = configd(what, 'reload')
        if out != 'OK':
            return False, '%s reload: %s' % (what, out)
    return True, ''


def flushreload():
    """ core flushreloadAction: removes everything, then loads the configuration again. Traffic is
    unshaped (not dropped) for the moment between flush and reload """
    configd('ipfw', 'flush')
    return reconfigure()


def kernel_vs_config():
    """ :return: (snapshot, stale, missing, disabled_loaded). stale = loaded but not in the
    configuration at all; missing = enabled but not loaded; disabled_loaded = disabled in the
    configuration but loaded, which core's template does on every reload, so no
    reload can remove it and it is only reported """
    snap = snapshot(0)
    cfg, kern = snap['config'], snap['kernel']['dnctl']
    stale, missing, disabled_loaded = [], [], []
    for kind, objs, loaded in (('pipe', cfg['pipes'], kern['pipes']), ('queue', cfg['queues'], kern['queues'])):
        # a fresh snapshot has int keys, a cached (JSON) one str keys: compare as text
        have = {str(n) for n in loaded}
        allc = {str(o.get('number')) for o in objs}
        enabled = {str(o.get('number')) for o in objs if o.get('enabled') == '1'}
        stale += ['%s %s' % (kind, n) for n in sorted(have - allc)]
        missing += ['%s %s' % (kind, n) for n in sorted(enabled - have)]
        disabled_loaded += ['%s %s' % (kind, n) for n in sorted((have & allc) - enabled)]
    return snap, stale, missing, disabled_loaded


def settle():
    """ every path ends here: compare the kernel with the configuration; when something is
    loaded that the configuration does not have, flush and reload once and compare again
    :return: (ok, detail) """
    snap, stale, missing, _ = kernel_vs_config()
    flushed = False
    if stale or missing:
        flushreload()
        flushed = True
        snap, stale, missing, _ = kernel_vs_config()
    if stale or missing:
        return False, 'after a flush and reload: %s' % '; '.join(
            x for x in ('still loaded: ' + ', '.join(stale) if stale else '',
                        'not loaded: ' + ', '.join(missing) if missing else '') if x)
    return True, 'kernel matches the configuration' + (' (after one flush and reload)' if flushed else '')


def writer(*args):
    rc, out, err = run(WRITER + list(args), timeout=120)
    try:
        res = json.loads(out)
    except ValueError:
        res = {'status': 'failed', 'message': (err or out).strip()[:500]}
    return rc == 0 and res.get('status') == 'ok', res


def job(down, up, mode, token, digest=''):
    from health import links
    from lib.plan import build, priorities_set, plan_digest, link_choices
    st = {'token': token, 'state': 'running', 'started': time.time(), 'steps': [], 'restore': '',
          'deadline': 0, 'rates': [down, up], 'mode': mode, 'created': {}, 'disabled': []}

    def step(name, ok, detail=''):
        st['steps'].append({'name': name, 'ok': ok, 'detail': detail})
        save_state(st)
        progress('%s: %s %s' % ('OK' if ok else 'FAILED', name, detail))
        return ok

    def revert(reason):
        st['state'] = 'reverting'
        st['reason'] = reason
        save_state(st)
        ok, res = writer('restore-sections', st['restore'])
        step('Put the shaper sections back', ok, res.get('message', '') or 'other settings changed meanwhile stay')
        ok2, msg = flushreload()
        step('Reload the shaper', ok2, msg + ' (a flush and reload, so nothing from the attempt stays loaded)')
        ok3, detail = settle()
        step('Compare kernel and configuration', ok3, detail)
        st['state'] = 'reverted' if ok and ok2 and ok3 else 'revert-failed'
        save_state(st)

    save_state(st)
    # the plan first: Apply writes the plan the user previewed, or nothing, and a plan with
    # nothing to write ("Keep mine" on the only link) stops before any restore point (F9)
    det = links()
    snap = snapshot(0)
    plan = build(det, snap, down, up, priorities_set(), link_choices()['rates'], mode)
    if digest and plan_digest(plan) != digest:
        step('Check the plan', False, 'the configuration or the detected links changed since the preview; '
             'preview again')
        st['state'] = 'failed'
        save_state(st)
        return
    objects = [o for lk in plan['links'] if not lk['skipped'] for o in lk['objects']]
    # exactly the rules the preview listed as "switches it off" (N6)
    disable = sorted({c['uuid'] for c in plan['conflicts'] if c.get('action') == 'disable'})
    if not plan['ready'] or not objects:
        blocker = next((p['text'] for p in plan['preflight'] if p.get('level') == 'bad'), '')
        step('Check the plan', False, plan['nothing'] or blocker or ('no link to set up' if not objects else
                                                                     'every link needs both speeds'))
        st['state'] = 'failed'
        save_state(st)
        return
    if os.path.exists(APPLIED):
        os.unlink(APPLIED)      # a previous job's; recover() must never compare against it
    ok, res = writer('snapshot', SECTIONS)
    if not step('Save a restore point', ok, 'shaper and CAKE sections' if ok else res.get('message', '')):
        st['state'] = 'failed'
        save_state(st)
        return
    st['restore'] = SECTIONS
    ok, res = writer('restore-point')
    st['safety_net'] = res.get('file', '') if ok else ''
    step('Save a full backup as a safety net', ok, (st['safety_net'] + ' (not applied automatically; '
         'System: Configuration: History)') if ok else res.get('message', ''))
    save_state(st)
    st['reused'] = {}
    for o in objects:
        if o.get('uuid'):
            st['reused'].setdefault(o['kind'], []).append(o['uuid'])
    step('Check the plan', True, '%d objects, %d of your rules to disable' % (len(objects), len(disable)))
    planfile = os.path.join(CACHE_DIR, 'apply-plan.json')
    _write_text(planfile, json.dumps({'objects': objects, 'disable_rules': disable}))
    refsfile = os.path.join(CACHE_DIR, 'apply-refs.json')
    _write_text(refsfile, '{}')
    names = {'pipe': 'pipes', 'queue': 'queues', 'rule': 'rules', 'cake': 'CAKE entries'}
    for kind in ('pipe', 'queue', 'rule', 'cake'):
        ok, res = writer('write', planfile, kind, refsfile)
        st['created'][kind] = res.get('created', [])
        st['disabled'] += res.get('disabled', [])
        counts = '%d added, %d changed in place' % (len(res.get('created', [])), len(res.get('changed', [])))
        detail = '; '.join(res.get('errors', [])) or res.get('message', '') or res.get('note', '') or counts
        if not step('Write the %s' % names[kind], ok, detail):
            return revert('writing failed')
    st['removed'] = []
    if plan.get('remove'):
        # Shaper Insight's own objects the new plan has no place for: same rules as Remove (by
        # uuid, core objects only while they carry our origin)
        gone = {}
        for o in plan['remove']:
            gone.setdefault(o['kind'], []).append(o['uuid'])
        gonefile = os.path.join(CACHE_DIR, 'apply-remove.json')
        _write_text(gonefile, json.dumps(dict(gone, enable=[])))
        ok, res = writer('remove', gonefile)
        r = res.get('removed', {})
        if not step('Remove what the new plan no longer needs', ok,
                    res.get('message', '') or ', '.join('%d %s' % (r.get(k, 0), names[k]) for k in ('pipe', 'queue', 'rule', 'cake')
                                                        if r.get(k))):
            return revert('removing failed')
        st['removed'] = [u for uuids in gone.values() for u in uuids]
    ok, msg = reconfigure()
    if not step('Apply the shaper', ok, msg):
        return revert('apply failed')
    # what we wrote, for recover(): a later revert only happens while this is still in place
    ok, res = writer('snapshot', APPLIED)
    if not step('Record what was written', ok, '' if ok else res.get('message', '')):
        # without it a later stop could not be told apart from a hand edit (needs-attention
        # instead of the automatic revert), so go back now (S13)
        return revert('could not record what was written')
    snap, stale, missing, disabled_loaded = kernel_vs_config()
    if disabled_loaded:
        step('Note', True, 'loaded although disabled: %s (the core template loads disabled objects too; '
             'no reload removes them)' % ', '.join(disabled_loaded))
    if stale:
        step('Compare kernel and configuration', False, 'loaded but not configured: ' + ', '.join(stale))
        ok, msg = flushreload()
        snap, stale, missing, _ = kernel_vs_config()
        step('Flush and reload once', ok and not stale,
             'stale objects removed; traffic was unshaped for about a second' if not stale
             else 'still loaded: ' + ', '.join(stale))
    if missing:
        step('Compare kernel and configuration', False, 'not loaded: ' + ', '.join(missing))
        return revert('objects missing in the kernel')
    if not stale:
        step('Compare kernel and configuration', True, 'kernel matches the configuration')
    if snap.get('oscake') is not None and os.path.exists(CAKE_DNCTL):
        want = [o['number'] for o in objects if o['kind'] == 'pipe' and o['model'].get('scheduler') == 'fq_codel']
        pipes = {str(k): v for k, v in snap['kernel']['dnctl']['pipes'].items()}
        types = {str(n): pipes.get(str(n), {}).get('type') for n in want}
        bad = [n for n, t in types.items() if t != 'CAKE']
        if not step('Check that CAKE is running', not bad,
                    'type CAKE on pipes ' + ', '.join(types) if not bad else 'not CAKE on pipes ' + ', '.join(bad)):
            return revert('CAKE is not running on the new pipes')
    st['state'] = 'waiting'
    st['deadline'] = time.time() + CONFIRM_SECONDS
    step('Start the confirm countdown', True, '%d s' % CONFIRM_SECONDS)
    confirm_file = os.path.join(CACHE_DIR, 'confirm.' + token)
    while time.time() < st['deadline']:
        if os.path.exists(confirm_file):
            os.unlink(confirm_file)
            ledger = load_ledger()
            # everything this apply wrote, also objects it changed or left as they were: an
            # object an unconfirmed (killed) job left behind and this plan reused is ours too,
            # and Remove must be able to take it
            for kind, uuids in list(st['created'].items()) + list(st.get('reused', {}).items()):
                ledger[kind] = sorted(set(ledger.get(kind, [])) | set(uuids))
            ledger['enable'] = sorted(set(ledger.get('enable', [])) | set(st['disabled']))
            for kind in ('pipe', 'queue', 'rule', 'cake'):
                ledger[kind] = sorted(set(ledger.get(kind, [])) - set(st.get('removed', [])))
            _write_text(LEDGER, json.dumps(ledger))
            st['state'] = 'confirmed'
            st['deadline'] = 0
            step('Confirmed', True)
            ok, detail = settle()
            step('Compare kernel and configuration', ok, detail)
            return
        if revert_marker_for(token, st['started']):
            return revert('not confirmed: the plugin was uninstalled during the countdown')
        time.sleep(0.5)
    revert('not confirmed within %d s' % CONFIRM_SECONDS)


REVERT_NOW = os.path.join(CACHE_DIR, 'revert-now')


def revert_marker_for(token, started):
    """ True (and the marker consumed) only when uninstall wrote it for THIS job, after it started;
    any other marker is stale (an uninstall that gave up) and is removed unread (review S3) """
    try:
        st = os.lstat(REVERT_NOW)
    except OSError:
        return False
    mine = False
    if st.st_mtime >= started - 1:
        mine = secrets.compare_digest(_read_text(REVERT_NOW).strip(), token)
    os.unlink(REVERT_NOW)
    return mine


def _read_text(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as f:
            return f.read(256)
    except OSError:
        return ''


def remove_recorded():
    """ Remove: exactly the uuids recorded when applies were confirmed (caller holds the lock) """
    if not load_ledger():
        return {'status': 'ok', 'removed': {}, 'message': 'nothing recorded to remove'}
    ok, res = writer('remove', LEDGER)
    if not ok:
        return {'status': 'failed', 'message': res.get('message', ''), 'dependants': res.get('dependants', [])}
    keep = set(res.get('kept_uuids', []))
    if keep:
        # what something of yours still uses stays recorded, so a later Remove can take it
        ledger = load_ledger()
        _write_text(LEDGER, json.dumps({k: [u for u in v if u in keep] for k, v in ledger.items() if k != 'enable'}))
    else:
        os.unlink(LEDGER)
    ok2, msg = flushreload()
    ok3, detail = settle()
    st = load_state()
    if st:
        # a new state, not the last apply's: its reason and steps are history (N7)
        st.update({'state': 'removed', 'reason': '', 'steps': [], 'deadline': 0})
        save_state(st)
    return {'status': 'ok' if ok2 and ok3 else 'failed', 'removed': res.get('removed', {}),
            'kept': res.get('kept', []), 'message': msg, 'kernel': detail}


def uninstall():
    """ +PRE_DEINSTALL, while our scripts still exist. Following OPNsense convention a plugin
    leaves its configuration on uninstall, so this NEVER deletes a confirmed setup (review N1:
    opnsense/plugins' "make upgrade" is pkg delete + pkg add, and a Remove here wiped the setup
    on every source update). It only
      - ends a PENDING countdown by reverting it (that change was never accepted),
      - stops a running bufferbloat test,
      - unloads the priority marks (ipfw set 7 and its tables), which need the plugin; the
        shaper itself keeps running, priorities fall back to "everything alike".
    The ledger and the restore points stay in /conf, so a reinstall picks the setup up again
    and Remove still works (review N2/N3). To take the shaping away: Remove before uninstalling.
    :return: status 'failed' (the package script then stops the uninstall) when a running
             apply could not be ended """
    out = {'status': 'ok', 'steps': []}
    pending = lock_held()
    try:
        if pending:
            # for the running job only: it carries that job's token (review S3)
            _write_text(REVERT_NOW, load_state().get('token', '') or '-')
            run([os.path.join(HERE, 'bbtest.py'), 'cancel'])
            for _ in range(240):            # the job reverts and releases the lock within seconds
                if not lock_held():
                    break
                time.sleep(0.5)
        fd = take_lock()
    finally:
        # never left behind, also when uninstall gives up below (review S3)
        if os.path.exists(REVERT_NOW):
            os.unlink(REVERT_NOW)
    if fd is None:
        out['status'] = 'failed'
        out['steps'].append('an apply still runs after 2 minutes; uninstall stopped so it cannot be left '
                            'half done. Wait for it (or confirm it) and uninstall again')
        return out
    try:
        st = load_state()
        if st.get('state') in ('running', 'waiting', 'reverting'):
            out['steps'].append('unconfirmed apply: ' + recover(st))
        elif pending and st.get('state') in ('reverted', 'revert-failed'):
            out['steps'].append('unconfirmed apply: ' + st['state'])
        if load_ledger():
            out['steps'].append('your shaper setup stays (Shaper Insight\'s objects are kept and recorded; '
                                'Remove after a reinstall still takes them away)')
        else:
            out['steps'].append('Shaper Insight has no objects in your shaper (nothing recorded); your own shaper '
                                'stays as it is')
        rc, prio_out, _ = run([os.path.join(HERE, 'prio.py'), 'purge'], timeout=90)
        out['steps'].append('priority marks unloaded: ' + (prio_out.strip() or 'rc %d' % rc))
    finally:
        os.close(fd)
    return out


def main(argv):
    cmd = argv[1] if len(argv) > 1 else 'status'
    cache_dir()
    if cmd == 'start':
        down, up = (argv[2:4] + ['0', '0'])[:2]
        mode = argv[4] if len(argv) > 4 and argv[4] in ('skip', 'disable', 'disable-bulk') else 'skip'
        digest = argv[5] if len(argv) > 5 and len(argv[5]) == 64 and all(c in '0123456789abcdef' for c in argv[5]) else ''
        if not digest:
            return {'status': 'failed', 'message': 'preview the plan first (no plan digest)'}
        if load_state().get('state') == 'needs-attention':
            # its restore point is what the open question is about: never overwrite it (S11)
            return {'status': 'failed', 'message': 'a stopped apply needs your decision first (restore or keep, '
                                                   'on the setup page)'}
        # the lock is taken HERE and handed to the job, so two quick starts cannot both answer
        # "started" (review M4)
        fd = take_lock()
        if fd is None:
            return {'status': 'failed', 'message': BUSY}
        token = secrets.token_hex(16)
        _write_text(PROGRESS, '')
        os.set_inheritable(fd, True)
        rc, out, err = run(['/usr/sbin/daemon', '-f', os.path.join(HERE, 'apply.py'), 'run',
                            str(int(down) if down.isdigit() else 0), str(int(up) if up.isdigit() else 0), mode, token,
                            digest, str(fd)])
        os.close(fd)
        if rc != 0:
            return {'status': 'failed', 'message': err.strip() or 'could not start the job'}
        return {'status': 'started', 'job': job_id(token)}
    if cmd == 'run':
        fd = int(argv[7]) if len(argv) > 7 and argv[7].isdigit() else None
        try:
            # the fd handed over by start already holds the lock; locking it again is a no-op
            if fd is None:
                fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return None
        try:
            job(int(argv[2]), int(argv[3]), argv[4], argv[5], argv[6] if len(argv) > 6 else '')
        except BaseException as exc:       # noqa: B036 - the job must never leave "running" behind
            st = load_state()
            st.setdefault('steps', []).append({'name': 'The job failed', 'ok': False, 'detail': repr(exc)[:300]})
            save_state(st)
            if st.get('state') in ('running', 'waiting', 'reverting'):
                # we hold the lock, so nobody else changed the shaper meanwhile: compare and revert
                recover(st)
        finally:
            progress('***DONE***')
        return None
    if cmd == 'confirm':
        st = load_state()
        # not named "job": that would make job() a local name in all of main()
        given = argv[2] if len(argv) > 2 else ''
        if st.get('state') != 'waiting' or not lock_held():
            return {'status': 'failed', 'message': 'no timer found: nothing is waiting for a confirmation'}
        if not secrets.compare_digest(job_id(st.get('token', '')), given):
            return {'status': 'failed', 'message': 'wrong token: this confirmation belongs to another apply'}
        _write_text(os.path.join(CACHE_DIR, 'confirm.' + st['token']), '')
        for _ in range(10):
            time.sleep(0.5)
            if load_state().get('state') == 'confirmed':
                return {'status': 'ok'}
        return {'status': 'failed', 'message': 'the timer did not take the confirmation'}
    if cmd == 'status':
        st = load_state()
        alive = lock_held()
        if st.get('state') in ('running', 'waiting', 'reverting') and not alive \
                and time.time() - st.get('updated', 0) > 5:
            # the job died without confirming. status is a read (a GET): it never starts the
            # recovery itself (review S5); the page asks for it with a POST (apply/recover), and
            # the boot hook does it at start-up
            st['recovery_pending'] = True
            st['state_note'] = 'the job stopped before it was confirmed; recovery pending'
        st['job'] = job_id(st.pop('token', ''))      # never the token: status is read-only (L3)
        st['alive'] = alive
        st['seconds_left'] = max(0, int(st.get('deadline', 0) - time.time())) if st.get('state') == 'waiting' else 0
        return st
    if cmd == 'resolve':
        # the admin's answer to "needs-attention": restore the pre-apply sections, or keep
        choice = argv[2] if len(argv) > 2 else ''
        if choice not in ('restore', 'keep'):
            return {'status': 'failed', 'message': 'nothing is waiting for a decision'}
        fd = take_lock()
        if fd is None:
            return {'status': 'failed', 'message': BUSY}
        try:
            # read under the lock: an apply that ended meanwhile has replaced the state (S11)
            st = load_state()
            if st.get('state') != 'needs-attention':
                return {'status': 'failed', 'message': 'nothing is waiting for a decision'}
            if choice == 'restore':
                restore(st, 'put back by hand after the job had stopped')
            else:
                st['state'] = 'kept'
                st['reason'] = 'the settings as they were were kept by hand'
                save_state(st)
        finally:
            os.close(fd)
        return {'status': 'ok', 'state': st['state']}
    if cmd == 'check':
        snap, stale, missing, disabled_loaded = kernel_vs_config()
        return {'stale': stale, 'missing': missing, 'disabled_loaded': disabled_loaded}
    if cmd == 'remove':
        # "Remove everything Shaper Insight made": exactly the uuids recorded when applies were
        # confirmed (never a name match); rules it switched off come back on
        # the whole Remove holds the one lock (review M3)
        fd = take_lock()
        if fd is None:
            return {'status': 'failed', 'message': BUSY}
        try:
            return remove_recorded()
        finally:
            os.close(fd)
    if cmd == 'boot':
        # an apply that was waiting or running when the firewall (or the job) stopped
        fd = take_lock()
        if fd is None:
            return {'status': 'ok', 'state': 'a job is running'}
        try:
            st = load_state()
            if st.get('state') in ('running', 'waiting', 'reverting'):
                recover(st)
            return {'status': 'ok', 'state': st.get('state', '')}
        finally:
            os.close(fd)
    if cmd == 'uninstall':
        return uninstall()
    return {'status': 'failed', 'message': 'unknown command'}


if __name__ == '__main__':
    # +PRE_DEINSTALL decides by the exit status, so uninstall fails closed: a crash or a failed
    # result is exit 1 (review S1). Other commands keep exit 0 for configd's script_output; their
    # result says failed in the JSON
    try:
        result = main(sys.argv)
    except BaseException as exc:      # noqa: B036 - report every crash, then fail
        print(json.dumps({'status': 'failed', 'message': 'crashed: %r' % (exc,)}))
        sys.exit(1)
    if result is not None:
        print(json.dumps(result))
        if len(sys.argv) > 1 and sys.argv[1] == 'uninstall' and result.get('status') != 'ok':
            sys.exit(1)
