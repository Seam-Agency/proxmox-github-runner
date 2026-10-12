#!/usr/bin/python3
"""Bounded cache retention and job-completed workspace cleanup for CT 100.

Only configured paths are eligible. The listener is paused before the final
worker check, closing the race between an idle check and a new assignment.
Workers are never signalled. systemd ExecStopPost resumes listeners on timeout.
"""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid

CONFIG = Path('/etc/github-runner-cleanup.json')
BASE = Path('/var/lib/github-runner-cleanup')
GIB = 1024 ** 3
MIB = 1024 ** 2


def log(message):
    print(time.strftime('%Y-%m-%dT%H:%M:%S%z'), message, flush=True)


def config():
    return json.loads(CONFIG.read_text())


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def atomic_json(path, data, mode=0o600):
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, 'w') as out:
        json.dump(data, out)
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, path)


def proc(pid):
    try:
        p = Path('/proc') / str(pid)
        raw = (p / 'stat').read_text()
        tail = raw[raw.rfind(')') + 2:].split()
        return {'pid': int(pid), 'uid': p.stat().st_uid,
                'comm': raw[raw.find('(') + 1:raw.rfind(')')],
                'state': tail[0], 'ppid': int(tail[1]),
                'start': int(tail[19]), 'boot': boot_id()}
    except (FileNotFoundError, ProcessLookupError):
        return None


def processes():
    return {int(p.name): item for p in Path('/proc').iterdir()
            if p.name.isdigit() and (item := proc(int(p.name))) is not None}


def same_process(record):
    current = proc(record['pid'])
    return current is not None and all(current[k] == record[k]
                                       for k in ('uid', 'start', 'boot'))


def workers(table, uid=None):
    return [p for p in table.values() if p['comm'] == 'Runner.Worker'
            and (uid is None or p['uid'] == uid)]


def listener(runner, table):
    found = [p for p in table.values()
             if p['uid'] == runner['uid'] and p['comm'] == 'Runner.Listener']
    if len(found) != 1:
        raise RuntimeError(f"{runner['name']}: expected one listener, found {len(found)}")
    p = found[0]
    exe = Path(os.readlink(f"/proc/{p['pid']}/exe"))
    if not exe.is_relative_to(Path(runner['root'])) or exe.name != 'Runner.Listener':
        raise RuntimeError('Listener executable is outside the configured installation')
    return p


def descendants(table, pid):
    found = set()
    frontier = {pid}
    while frontier:
        frontier = {p['pid'] for p in table.values()
                    if p['ppid'] in frontier and p['pid'] not in found}
        found.update(frontier)
    return found


def paused_file(scope):
    if scope not in ('maintenance', 'requests', 'activate', 'test'):
        raise ValueError('Invalid pause scope')
    return BASE / ('paused-' + scope + '.json')


def resume(scope):
    path = paused_file(scope)
    if not path.exists():
        return
    for record in json.loads(path.read_text()):
        if same_process(record):
            os.kill(record['pid'], signal.SIGCONT)
    path.unlink(missing_ok=True)


@contextlib.contextmanager
def pause_listeners(runners, scope):
    records = []
    try:
        for runner in runners:
            p = listener(runner, processes())
            if p['state'] in ('T', 't'):
                raise RuntimeError('Listener was already paused; leaving it unchanged')
            records.append(p)
            atomic_json(paused_file(scope), records)
            os.kill(p['pid'], signal.SIGSTOP)
            deadline = time.monotonic() + 2
            while True:
                if not same_process(p):
                    raise RuntimeError('Listener exited while entering maintenance')
                tasks = list(Path(f"/proc/{p['pid']}/task").iterdir())
                states = [x.joinpath('stat').read_text().rsplit(')', 1)[1].split()[0]
                          for x in tasks]
                if states and all(s in ('T', 't') for s in states):
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError('Listener did not pause promptly')
                time.sleep(0.01)
        yield records
    finally:
        resume(scope)


def try_drain_runner(runner, records, scope):
    """Pause only a listener with no worker or pre-exec child, then recheck."""
    table = processes()
    p = listener(runner, table)
    if workers(table, runner['uid']) or descendants(table, p['pid']):
        return False
    if p['state'] in ('T', 't'):
        raise RuntimeError('Listener was already paused outside this maintenance')
    records.append(p)
    atomic_json(paused_file(scope), records)
    os.kill(p['pid'], signal.SIGSTOP)
    deadline = time.monotonic() + 2
    while True:
        if not same_process(p):
            raise RuntimeError('Listener exited while entering maintenance')
        tasks = list(Path(f"/proc/{p['pid']}/task").iterdir())
        states = [x.joinpath('stat').read_text().rsplit(')', 1)[1].split()[0] for x in tasks]
        if states and all(s in ('T', 't') for s in states):
            break
        if time.monotonic() > deadline:
            raise RuntimeError('Listener did not pause promptly')
        time.sleep(0.01)
    table = processes()
    if workers(table, runner['uid']) or descendants(table, p['pid']):
        os.kill(p['pid'], signal.SIGCONT)
        records.remove(p)
        atomic_json(paused_file(scope), records)
        return False
    log(f"{runner['name']}: current job finished; new assignments waiting for maintenance")
    return True


@contextlib.contextmanager
def drain_runners(cfg):
    records = []
    deadline = time.monotonic() + cfg.get('drain_timeout_seconds', 1800)
    try:
        while len(records) < len(cfg['runners']):
            # Never hold this lock while waiting for jobs: their completion
            # hooks need it to clean their own workspaces and finish normally.
            with root_lock():
                for runner in cfg['runners']:
                    if any(p['uid'] == runner['uid'] for p in records):
                        continue
                    try_drain_runner(runner, records, 'maintenance')
            if any(not same_process(p) for p in records):
                raise RuntimeError('A drained listener exited unexpectedly')
            if len(records) == len(cfg['runners']):
                break
            if time.monotonic() > deadline:
                raise RuntimeError('Maintenance deferred: current jobs exceeded the drain window')
            time.sleep(0.2)
        yield records
    finally:
        resume('maintenance')


@contextlib.contextmanager
def root_lock(block=True):
    with (BASE / 'maintenance.lock').open('a') as gate:
        fcntl.flock(gate, fcntl.LOCK_EX | (0 if block else fcntl.LOCK_NB))
        yield


def require_idle(records, table):
    if workers(table) or any(descendants(table, p['pid']) for p in records):
        raise RuntimeError('Busy: a worker or listener child is present')


def run(args, timeout=30, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f'{args[0]} {args[1]} failed: {result.stderr.strip()[:300]}')
    return result.stdout.strip()


def allocated(path):
    if not path.exists():
        return 0
    return int(run(['du', '-sx', '-B1', str(path)], timeout=20).split()[0])


def mounted_below(path):
    prefix = str(path) + '/'
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        mount = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4])
        if mount == str(path) or mount.startswith(prefix):
            return True
    return False


def safe_directory(path):
    if not path.is_absolute() or path == Path('/'):
        raise RuntimeError('Refusing invalid cleanup root')
    if path.is_symlink() or (path.exists() and path.resolve() != path):
        raise RuntimeError(f'Refusing symlinked cleanup root: {path}')
    if path.exists() and (not path.is_dir() or mounted_below(path)):
        raise RuntimeError(f'Refusing mounted or non-directory cleanup root: {path}')


def remove_entry(entry):
    if entry.is_symlink() or not entry.is_dir():
        entry.unlink(missing_ok=True)
    else:
        if mounted_below(entry):
            raise RuntimeError(f'Refusing to cross a mount: {entry}')
        shutil.rmtree(entry)


def clear_directory(path, preserve=None):
    safe_directory(path)
    if not path.exists():
        return 0
    count = 0
    for child in path.iterdir():
        if preserve and preserve(child):
            continue
        remove_entry(child)
        count += 1
    return count


def reset_cache(path):
    """Detach a whole cache atomically so interruption cannot expose half a module."""
    safe_directory(path)
    if not path.exists():
        return
    trash = BASE / 'trash'
    trash.mkdir(mode=0o700, exist_ok=True)
    safe_directory(trash)
    meta = path.stat()
    if meta.st_dev != trash.stat().st_dev:
        raise RuntimeError('Cache must be on the runner root filesystem')
    detached = trash / uuid.uuid4().hex
    os.rename(path, detached)
    try:
        path.mkdir(mode=stat.S_IMODE(meta.st_mode))
        os.chown(path, meta.st_uid, meta.st_gid)
        path.chmod(stat.S_IMODE(meta.st_mode))
    except BaseException:
        if not path.exists():
            os.rename(detached, path)
        raise
    shutil.rmtree(detached)


def docker_running():
    return run(['docker', 'ps', '-q']).splitlines()


def workspace_bound_to_container(work):
    for ident in docker_running():
        mounts = json.loads(run(['docker', 'inspect', '--format', '{{json .Mounts}}', ident]))
        for mount in mounts:
            source = Path(mount.get('Source', '/'))
            if source == work or source.is_relative_to(work):
                return True
    return False


def clean_workspace(runner):
    work = Path(runner['root']) / '_work'
    if workspace_bound_to_container(work):
        raise RuntimeError('Workspace is mounted by a running container; deferred')
    # _temp and all runner-reserved directories stay intact during the final hook.
    count = clear_directory(work, preserve=lambda p: p.name.startswith('_'))
    log(f"{runner['name']}: removed {count} workspace directories/entries")
    return count


def completed_parent():
    pid = os.getppid()
    while pid > 1:
        p = proc(pid)
        if p is None:
            break
        if p['comm'] == 'Runner.Worker':
            return p
        pid = p['ppid']
    return None


def job_completed(cfg):
    runner = next(r for r in cfg['runners'] if r['uid'] == os.geteuid())
    root = BASE / str(runner['uid'])
    nonce = uuid.uuid4().hex
    request = {'worker': completed_parent(), 'created': time.time(), 'boot': boot_id()}
    # Stage outside the watched directory: .path must only see complete requests.
    staged = root / 'results' / (nonce + '.request')
    atomic_json(staged, request)
    os.replace(staged, root / 'requests' / (nonce + '.json'))
    result_path = root / 'results' / (nonce + '.json')
    deadline = time.monotonic() + 50
    while time.monotonic() < deadline:
        if result_path.exists():
            result = json.loads(result_path.read_text())
            result_path.unlink(missing_ok=True)
            log('Workspace cleanup: ' + result['status'])
            return
        time.sleep(0.2)
    # A cleanup failure must not change a successful workflow into a failed job.
    log('Workspace cleanup deferred; idle maintenance will retry')


def authorize_request(runner, request, table, records):
    expected = request.get('worker')
    active = workers(table, runner['uid'])
    if expected and same_process(expected):
        if expected['uid'] != runner['uid'] or expected['comm'] != 'Runner.Worker':
            raise RuntimeError('Invalid worker identity')
        if {p['pid'] for p in active} != {expected['pid']}:
            raise RuntimeError('A different job is active; deferred')
        allowed = descendants(table, expected['pid']) | {expected['pid']}
        if any(descendants(table, p['pid']) - allowed for p in records):
            raise RuntimeError('Another listener child is active; deferred')
    elif active or any(descendants(table, p['pid']) for p in records):
        raise RuntimeError('The original job ended and a new job is active; deferred')


def handle_requests(cfg):
    with root_lock():
        for runner in cfg['runners']:
            root = BASE / str(runner['uid'])
            for path in sorted((root / 'requests').glob('*.json')):
                status = 'deferred'
                try:
                    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(fd) as src:
                        meta = os.fstat(src.fileno())
                        if meta.st_uid != runner['uid'] or not stat.S_ISREG(meta.st_mode) or meta.st_size > 4096:
                            raise RuntimeError('Invalid request owner, type or size')
                        request = json.load(src)
                    if request['boot'] != boot_id() or time.time() - request['created'] > 3600:
                        raise RuntimeError('Expired cleanup request')
                    with pause_listeners([runner], 'requests') as records:
                        authorize_request(runner, request, processes(), records)
                        count = clean_workspace(runner)
                        status = f'complete ({count} entries removed)'
                except Exception as exc:
                    log(f"{runner['name']}: cleanup deferred: {exc}")
                finally:
                    path.unlink(missing_ok=True)
                    result = root / 'results' / path.name
                    atomic_json(result, {'status': status}, 0o644)


def cache_paths(cfg):
    targets = []
    for runner in cfg['runners']:
        home = Path(runner['home'])
        work = Path(runner['root']) / '_work'
        for rel, mib, days, priority in cfg['home_caches']:
            targets.append({'path': home / rel, 'mib': mib, 'days': days,
                            'key': runner['name'] + ':' + rel, 'priority': priority})
        for rel, mib, days, priority in cfg['work_caches']:
            targets.append({'path': work / rel, 'mib': mib, 'days': days,
                            'key': runner['name'] + ':' + rel, 'priority': priority})
    for name, mib in cfg['cargo_volumes']:
        raw = run(['docker', 'volume', 'inspect', '--format',
                   '{{json .}}', name], check=False)
        if not raw:
            continue
        volume = json.loads(raw)
        expected = Path('/var/lib/docker/volumes') / name / '_data'
        if volume['Driver'] != 'local' or volume.get('Options') or Path(volume['Mountpoint']) != expected:
            raise RuntimeError('Unexpected cache volume configuration: ' + name)
        targets.append({'path': expected, 'mib': mib, 'days': 7,
                        'key': name, 'priority': 1 if name.endswith('target') else 3})
    return targets


def parse_bytes(value):
    match = re.fullmatch(r'([0-9.]+)\s*([A-Za-z]+)', value)
    if not match:
        raise ValueError('Unrecognized size: ' + value)
    unit = match[2].lower()
    scale = {'b': 1, 'kb': 1000, 'mb': 1000**2, 'gb': 1000**3,
             'tb': 1000**4, 'kib': 1024, 'mib': MIB, 'gib': GIB}
    return int(float(match[1]) * scale[unit])


def cache_total(targets):
    # Modern Docker stores images/build layers in containerd. Include classic
    # paths too, but count the three named volumes only through the allowlist.
    docker_paths = ('/var/lib/containerd', '/var/lib/docker/buildkit',
                    '/var/lib/docker/overlay2', '/var/lib/docker/image')
    return sum(allocated(t['path']) for t in targets) + sum(allocated(Path(p)) for p in docker_paths)


def prune_caches(cfg):
    state_path = BASE / 'retention.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    now = time.time()
    targets = cache_paths(cfg)
    for item in targets:
        safe_directory(item['path'])
        if not item['path'].exists():
            continue
        last = state.setdefault(item['key'], now)
        size = allocated(item['path'])
        if size > item['mib'] * MIB or now - last >= item['days'] * 86400:
            reset_cache(item['path'])
            state[item['key']] = now
            log(f"Reset {item['key']} ({size / GIB:.2f} GiB; age/size retention)")
    log(run(['docker', 'buildx', 'prune', '--builder', 'default', '--all', '--force',
             '--filter', 'until=168h'], timeout=40))
    log(run(['docker', 'buildx', 'prune', '--builder', 'default', '--all', '--force',
             '--max-used-space', str(cfg['docker_build_mib'] * MIB)], timeout=40))
    rows = [json.loads(line) for line in run(['docker', 'system', 'df', '--format', 'json']).splitlines()]
    image_size = next(parse_bytes(row['Size']) for row in rows if row['Type'] == 'Images')
    image_last = state.setdefault('docker-images', now)
    if image_size > cfg['docker_images_mib'] * MIB or now - image_last >= 7 * 86400:
        log(run(['docker', 'image', 'prune', '--all', '--force'], timeout=40)[-250:])
        state['docker-images'] = now
    total = cache_total(targets)
    pressure = shutil.disk_usage('/').free < cfg['min_free_mib'] * MIB
    if total > cfg['total_cache_mib'] * MIB or pressure:
        # Unused images can be downloaded again; avoid destroying warm compiler
        # caches until Docker's unused images have been reclaimed.
        log(run(['docker', 'image', 'prune', '--all', '--force'], timeout=40)[-250:])
        state['docker-images'] = now
        total = cache_total(targets)
        for item in sorted(targets, key=lambda t: (t['priority'], state.get(t['key'], 0))):
            if total <= cfg['total_cache_mib'] * MIB and shutil.disk_usage('/').free >= cfg['min_free_mib'] * MIB:
                break
            size = allocated(item['path'])
            if size:
                reset_cache(item['path'])
                state[item['key']] = now
                total = cache_total(targets)
                log(f"Reset {item['key']} ({size / GIB:.2f} GiB; combined cache budget)")
    atomic_json(state_path, state)
    log(f'Cache total: {total / GIB:.2f} GiB; target <= {cfg["total_cache_mib"] / 1024:.0f} GiB')


def clean_diagnostics(runner):
    root = Path(runner['root']) / '_diag'
    safe_directory(root)
    logs = sorted(root.glob('*.log'), key=lambda p: p.stat().st_mtime)
    protected = set()
    for prefix in ('Runner_', 'Worker_'):
        matches = [p for p in logs if p.name.startswith(prefix)]
        if matches:
            protected.add(matches[-1])
    total = sum(p.stat().st_size for p in logs)
    for p in logs:
        if p not in protected and (time.time() - p.stat().st_mtime > 14 * 86400 or total > 128 * MIB):
            total -= p.stat().st_size
            p.unlink()


def activate(cfg, drained=None):
    """Load the completion hook only after both listeners have no children."""
    if workers(processes()):
        log('Activation deferred: jobs are running')
        return False
    units = [r['service'] for r in cfg['runners']]
    stop_requested = False
    try:
        pause_context = contextlib.nullcontext(drained) if drained is not None else pause_listeners(cfg['runners'], 'activate')
        with pause_context as records:
            require_idle(records, processes())
            # Queue shutdown while listeners cannot accept another assignment.
            stop_requested = True
            atomic_json(BASE / 'activation-in-progress.json', {'units': units})
            run(['systemctl', 'stop', '--no-block', *units])
            deadline = time.monotonic() + 5
            while True:
                pending = []
                for record in records:
                    if not same_process(record):
                        pending.append(True)
                        continue
                    fields = dict(line.split(':', 1) for line in Path(f"/proc/{record['pid']}/status").read_text().splitlines() if ':' in line)
                    signals = int(fields['SigPnd'].strip(), 16) | int(fields['ShdPnd'].strip(), 16)
                    pending.append(bool(signals & (1 << (signal.SIGINT - 1))))
                if all(pending):
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError('Service shutdown did not reach the idle listeners')
                time.sleep(0.05)
        # A caller may already own the paused listeners through the drain scope.
        if drained is not None:
            resume('maintenance')
        run(['systemctl', 'stop', *units], timeout=40)
        for runner in cfg['runners']:
            path = Path(runner['root']) / '.env'
            old = path.read_text() if path.exists() else ''
            backup = BASE / (runner['name'] + '.env.before-cleanup')
            if not backup.exists():
                backup.write_text(old)
                backup.chmod(0o600)
            lines = [line for line in old.splitlines() if not line.startswith('ACTIONS_RUNNER_HOOK_JOB_COMPLETED=')]
            lines.append('ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/usr/local/libexec/github-runner-cleanup/job-completed.sh')
            path.write_text('\n'.join(lines) + '\n')
            os.chown(path, runner['uid'], runner['gid'])
    finally:
        if stop_requested:
            run(['systemctl', 'start', *units], timeout=40)
            (BASE / 'activation-in-progress.json').unlink(missing_ok=True)
    atomic_json(BASE / 'activated.json', {'activated': time.time(), 'version': 1})
    log('Completion hooks activated; both runner services started')
    return True


def recover_activation():
    resume('activate')
    path = BASE / 'activation-in-progress.json'
    if path.exists():
        units = [r['service'] for r in config()['runners']]
        run(['systemctl', 'start', *units], timeout=40)
        path.unlink(missing_ok=True)


def maintain(cfg):
    # The service instance is unique; its file lock also excludes manual runs.
    with (BASE / 'cycle.lock').open('a') as cycle:
        try:
            fcntl.flock(cycle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log('Skipped: another maintenance cycle is already running')
            return
        log('Waiting for each current job to finish before shared-cache maintenance')
        with drain_runners(cfg) as records:
            with root_lock():
                require_idle(records, processes())
                if docker_running():
                    log('Skipped: a Docker container is still running')
                    return
                before = shutil.disk_usage('/').used
                clear_directory(BASE / 'trash')
                for runner in cfg['runners']:
                    clean_workspace(runner)
                    clear_directory(Path(runner['root']) / '_work' / '_temp')
                    clean_diagnostics(runner)
                    results = BASE / str(runner['uid']) / 'results'
                    for p in results.glob('*.json'):
                        if time.time() - p.stat().st_mtime > 86400:
                            p.unlink()
                prune_caches(cfg)
                after = shutil.disk_usage('/').used
                log(f'Idle maintenance complete; reclaimed {(before - after) / GIB:.2f} GiB')
                if not (BASE / 'activated.json').exists():
                    activate(cfg, drained=records)


def terminate(signum, frame):
    raise RuntimeError(f'Cleanup interrupted by signal {signum}')


def main():
    mode = sys.argv[1]
    if mode == 'job-completed':
        try:
            job_completed(config())
        except Exception as exc:
            log(f'Workspace cleanup deferred: {type(exc).__name__}')
        return
    if os.geteuid() != 0:
        raise SystemExit('This command requires root')
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    if mode == 'resume':
        resume(sys.argv[2])
    elif mode == 'recover-activation':
        recover_activation()
    elif mode == 'requests':
        handle_requests(config())
    elif mode == 'maintain':
        maintain(config())
    else:
        raise SystemExit('Unknown mode')


if __name__ == '__main__':
    main()
