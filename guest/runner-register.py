#!/usr/bin/python3
"""Register the two clean runner installations in a new LXC clone."""
import argparse
import fcntl
import getpass
import json
import os
import pwd
import re
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

POLICY = Path('/etc/github-runner-cleanup.json')
STANDARD_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
HOOK = '/usr/local/libexec/github-runner-cleanup/job-completed.sh'

def run(args, **kwargs):
    subprocess.run(args, check=True, **kwargs)

def save_policy(value):
    temporary = POLICY.with_suffix('.register.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.chmod(0o644)
    temporary.replace(POLICY)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='Your GitHub organization or repository URL')
    parser.add_argument('--prefix', default=socket.gethostname())
    parser.add_argument('--group', default='Default')
    parser.add_argument('--labels', default='proxmox,debian13,docker')
    parser.add_argument('--plan', action='store_true', help='Show names without registering or changing anything')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run as root inside the new LXC clone')
    target = urlparse(args.url)
    if target.scheme != 'https' or target.netloc != 'github.com' or target.query or target.fragment:
        parser.error('--url must be an HTTPS github.com organization or repository URL')
    if len(target.path.strip('/').split('/')) not in (1, 2) or not target.path.strip('/'):
        parser.error('--url must name an organization or repository')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,49}', args.prefix):
        parser.error('--prefix must be 1-50 letters, digits, dots, underscores or hyphens')
    if args.prefix in ('github-runner-template', 'github-runner-01', 'github-runner-02'):
        parser.error('Use a new clone hostname/prefix so existing runners keep their names')
    cfg = json.loads(POLICY.read_text())
    planned = []
    for index, slot in enumerate(cfg['runners'], 1):
        root = Path(slot['root'])
        name = args.prefix + '-' + str(index).zfill(2)
        existing = json.loads((root / '.runner').read_text()) if (root / '.runner').exists() else None
        if existing and (existing.get('agentName') != name or
                         existing.get('gitHubUrl', '').rstrip('/').lower() != args.url.rstrip('/').lower()):
            raise SystemExit(str(root) + ' is already registered under a different name or URL; leaving it unchanged')
        planned.append((slot, root, name, existing))
        print(name + ' -> ' + args.url + ' (' + pwd.getpwuid(slot['uid']).pw_name + ')', flush=True)
    if args.plan:
        return
    token = None
    for slot, root, name, existing in planned:
        if not existing:
            if token is None:
                token = getpass.getpass('New GitHub runner registration token (hidden): ').strip()
                if not token:
                    raise SystemExit('No token supplied; no registration performed')
            env = {'PATH': STANDARD_PATH, 'LANG': 'C.UTF-8', 'ACTIONS_RUNNER_INPUT_TOKEN': token}
            user = pwd.getpwuid(slot['uid']).pw_name
            run(['runuser', '-u', user, '--', './config.sh', '--unattended', '--url', args.url,
                 '--name', name, '--runnergroup', args.group, '--labels', args.labels,
                 '--work', '_work'], cwd=root, env=env)
        user = pwd.getpwuid(slot['uid']).pw_name
        path = STANDARD_PATH + ':' + slot['home'] + '/.local/bin:' + slot['home'] + '/go/bin:' + slot['home'] + '/.cargo/bin\n'
        for filename, value in [('.path', path), ('.env', 'ACTIONS_RUNNER_HOOK_JOB_COMPLETED=' + HOOK + '\n')]:
            file = root / filename
            file.write_text(value)
            file.chmod(0o600)
            os.chown(file, slot['uid'], slot['gid'])
        service_file = root / '.service'
        if not service_file.exists():
            run(['./svc.sh', 'install', user], cwd=root, env={**os.environ, 'PATH': STANDARD_PATH})
        service = service_file.read_text().strip()
        if not re.fullmatch(r'actions\.runner\.[A-Za-z0-9_.@%-]+\.service', service):
            raise SystemExit('Unexpected runner service name')
        if not (Path('/etc/systemd/system') / service).is_file():
            raise SystemExit('Runner service installation is incomplete: ' + service)
        slot.update(name=name, service=service)
        save_policy(cfg)
    token = None
    # First-boot presets protect portable imports until registration is complete.
    Path('/etc/systemd/system-preset/00-github-runner-template.preset').unlink(missing_ok=True)
    run(['systemctl', 'daemon-reload'])
    for slot in cfg['runners']:
        run(['systemctl', 'enable', '--now', slot['service']])
    state = Path('/var/lib/github-runner-cleanup/activated.json')
    state.write_text(json.dumps({'activated': time.time(), 'version': 1}) + '\n')
    state.chmod(0o600)
    run(['systemctl', 'enable', '--now', 'github-runner-workspace-cleanup.path', 'github-runner-cleanup.timer'])
    run(['systemctl', 'is-active', *[r['service'] for r in cfg['runners']]])
    print('Both runner services and bounded workspace/cache cleanup are enabled.', flush=True)

if __name__ == '__main__':
    with open('/run/runner-register.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        main()
