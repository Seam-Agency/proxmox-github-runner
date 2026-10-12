#!/usr/bin/python3
"""Sanitize an explicitly owned, stopped build copy; never the live source."""
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import argparse
import socket
from datetime import datetime, timezone
parser = argparse.ArgumentParser()
parser.add_argument('--ct', type=int, required=True)
parser.add_argument('--owner', required=True)
parser.add_argument('--source', type=int, required=True)
parser.add_argument('--target', type=int, required=True)
args = parser.parse_args()
TARGET = args.ct
assert TARGET != args.source
ROOT = Path('/var/lib/lxc') / str(TARGET) / 'rootfs'
PAYLOAD = Path(__file__).resolve().parent
cfg_pve = json.loads(subprocess.check_output(['pvesh','get','/nodes/'+socket.gethostname().split('.')[0]+'/lxc/'+str(TARGET)+'/config','--output-format','json'],text=True))
assert args.owner in cfg_pve.get('description',''), 'Container is not owned by this build'
assert not cfg_pve.get('template'), 'Refusing to modify a published template'
assert ROOT.is_mount() and ROOT.stat().st_uid == 100000
assert subprocess.check_output(['pct','status',str(TARGET)],text=True).strip() == 'status: stopped'
assert (ROOT/'opt/runner-toolchain/installed.json').is_file()
UID_BASE = ROOT.stat().st_uid
GID_BASE = ROOT.stat().st_gid

def path(relative):
    p = ROOT / relative.lstrip('/')
    assert p != ROOT and p.parent.resolve().is_relative_to(ROOT), str(p)
    return p

def remove(relative):
    p = path(relative)
    if p.is_symlink() or p.is_file():
        p.unlink()
    elif p.is_dir():
        assert not p.is_mount(), 'Refusing nested mount: ' + str(p)
        shutil.rmtree(p)

def directory(relative, mode=0o755, uid=0, gid=0):
    p = path(relative)
    p.mkdir(parents=True, exist_ok=True)
    os.chown(p, UID_BASE + uid, GID_BASE + gid)
    p.chmod(mode)
    return p

def write(relative, data, mode=0o644, uid=0, gid=0):
    p = path(relative)
    if p.is_symlink():
        p.unlink()
    p.write_text(data)
    p.chmod(mode)
    os.chown(p, UID_BASE + uid, GID_BASE + gid)
    return p

def clear(relative):
    p = path(relative)
    if not p.exists():
        return
    assert p.is_dir() and not p.is_symlink()
    for child in p.iterdir():
        remove(str(child.relative_to(ROOT)))

cfg = json.loads(path('etc/github-runner-cleanup.json').read_text())
assert len(cfg['runners']) == 2
assert {r['uid'] for r in cfg['runners']} == {1000, 1001}
assert all(r['root'].startswith('/opt/') and r['home'].startswith('/home/') for r in cfg['runners'])
print('Removing cloned runner service identities before any container boot', flush=True)
for p in path('etc/systemd/system').rglob('actions.runner.*'):
    remove(str(p.relative_to(ROOT)))
for unit in ['github-runner-cleanup.timer', 'github-runner-workspace-cleanup.path', 'runner-env-setup.service']:
    for wants in path('etc/systemd/system').glob('*.wants'):
        p = wants / unit
        if p.exists() or p.is_symlink():
            remove(str(p.relative_to(ROOT)))

for index, runner in enumerate(cfg['runners'], 1):
    root = path(runner['root'])
    allowed = {'bin', 'externals', 'config.sh', 'env.sh', 'run.sh', 'runsvc.sh',
               'safe_sleep.sh', 'run-helper.sh.template', 'run-helper.cmd.template'}
    for item in root.iterdir():
        if item.name not in allowed:
            remove(str(item.relative_to(ROOT)))
    directory(runner['root'] + '/_work', uid=runner['uid'], gid=runner['gid'])
    directory(runner['root'] + '/_diag', uid=runner['uid'], gid=runner['gid'])
    safe_path = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:' + runner['home'] + '/.local/bin:' + runner['home'] + '/go/bin:' + runner['home'] + '/.cargo/bin\n'
    write(runner['root'] + '/.path', safe_path, 0o600, runner['uid'], runner['gid'])
    write(runner['root'] + '/.env', 'ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/usr/local/libexec/github-runner-cleanup/job-completed.sh\n', 0o600, runner['uid'], runner['gid'])
    home = path(runner['home'])
    for item in home.iterdir():
        if item.name != '.mix':
            remove(str(item.relative_to(ROOT)))
    mix = home / '.mix'
    if mix.is_dir() and not mix.is_symlink():
        for item in mix.iterdir():
            if item.name not in ('archives', 'elixir', 'rebar3'):
                remove(str(item.relative_to(ROOT)))
    for item in ['.bashrc', '.profile', '.bash_logout']:
        skel = path('etc/skel/' + item)
        if skel.is_file():
            write(runner['home'] + '/' + item, skel.read_text(), 0o644, runner['uid'], runner['gid'])
    for relative in ['.local', '.local/bin', 'go', 'go/bin', '.cargo', '.cargo/bin']:
        directory(runner['home'] + '/' + relative, uid=runner['uid'], gid=runner['gid'])
    write(runner['home'] + '/.gitconfig', '[filter "lfs"]\n\tclean = git-lfs clean -- %f\n\tsmudge = git-lfs smudge -- %f\n\tprocess = git-lfs filter-process\n\trequired = true\n', 0o644, runner['uid'], runner['gid'])
    runner.update(name='unregistered-' + str(index).zfill(2), service='actions.runner.unregistered-' + str(index).zfill(2) + '.service')
cfg['cargo_volumes'] = []  # Named build volumes are environment-specific.
write('etc/github-runner-cleanup.json', json.dumps(cfg, indent=2) + '\n')

print('Removing cloned CI workspaces, Docker state, caches, logs and root setup history', flush=True)
clear('root')
for item in ['.bashrc', '.profile']:
    skel = path('etc/skel/' + item)
    if skel.is_file():
        write('root/' + item, skel.read_text())
for relative in ['var/lib/docker', 'var/lib/containerd', 'var/lib/github-runner-cleanup', 'var/lib/github-runner-template',
                 'var/lib/dhcp', 'var/lib/dhcpcd', 'var/lib/systemd/coredump', 'var/lib/systemd/timers',
                 'var/log', 'var/tmp', 'tmp', 'var/cache/apt/archives', 'var/cache/runner-env-setup']:
    clear(relative)
directory('var/lib/github-runner-cleanup')
directory('var/lib/github-runner-cleanup/trash', 0o700)
for runner in cfg['runners']:
    base = 'var/lib/github-runner-cleanup/' + str(runner['uid'])
    directory(base)
    for relative in ['requests', 'results']:
        directory(base + '/' + relative, 0o700, runner['uid'], runner['gid'])

for relative in ['etc/apt/auth.conf', 'etc/apt/auth.conf.d', 'etc/docker/certs.d',
                 'etc/credstore', 'etc/credstore.encrypted',
                 'etc/postfix/sasl_passwd', 'etc/postfix/sasl_passwd.db',
                 'var/lib/waagent', 'var/lib/cloud', 'etc/rancher', 'etc/kubernetes']:
    remove(relative)
directory('etc/apt/auth.conf.d', 0o755)
clear('etc/ssl/private')
for relative in ['var/mail', 'var/spool/postfix/active', 'var/spool/postfix/deferred',
                 'var/spool/postfix/hold', 'var/spool/postfix/incoming', 'var/spool/postfix/maildrop']:
    clear(relative)
# Guest networking will be generated from the new Proxmox CT configuration.
write('etc/hostname', 'github-runner-template\n')
write('etc/hosts', '127.0.0.1 localhost\n::1 localhost ip6-localhost ip6-loopback\n')
write('etc/resolv.conf', '')
write('etc/network/interfaces', 'auto lo\niface lo inet loopback\n\nauto eth0\niface eth0 inet dhcp\n')
print('Resetting clone-specific host identity and password hashes', flush=True)
for p in path('etc/ssh').glob('ssh_host_*'):
    remove(str(p.relative_to(ROOT)))
for relative in ['etc/shadow-', 'etc/gshadow-', 'var/lib/systemd/random-seed', 'var/lib/dbus/machine-id']:
    remove(relative)
for p in path('var/backups').glob('*'):
    if any(word in p.name for word in ('passwd', 'shadow', 'group')):
        remove(str(p.relative_to(ROOT)))
shadow = path('etc/shadow')
rows = []
for line in shadow.read_text().splitlines():
    fields = line.split(':')
    fields[1] = '!'
    rows.append(':'.join(fields))
shadow.write_text('\n'.join(rows) + '\n')
gshadow = path('etc/gshadow')
rows = []
for line in gshadow.read_text().splitlines():
    fields = line.split(':')
    fields[1] = '!'
    rows.append(':'.join(fields))
gshadow.write_text('\n'.join(rows) + '\n')
write('etc/machine-id', '')
dbus = path('var/lib/dbus')
if dbus.is_dir():
    (dbus / 'machine-id').symlink_to('/etc/machine-id')
directory('usr/local/libexec/runner-template')
directory('var/lib/github-runner-template')
directory('etc/systemd/system-preset')
write('etc/systemd/system-preset/00-github-runner-template.preset',
      'disable actions.runner.*\n'
      'disable github-runner-cleanup.timer\n'
      'disable github-runner-workspace-cleanup.path\n'
      'disable runner-env-setup.service\n'
      'enable runner-template-first-boot.service\n')
write('usr/local/libexec/runner-template/runner-register.py', (PAYLOAD / 'runner-register.py').read_text(), 0o755)
write('usr/local/libexec/runner-template/first-boot.sh', (PAYLOAD / 'first-boot.sh').read_text(), 0o755)
write('usr/local/libexec/runner-template/README.txt', (PAYLOAD / 'README.txt').read_text())
write('etc/systemd/system/runner-template-first-boot.service', (PAYLOAD / 'runner-template-first-boot.service').read_text())
link = path('usr/local/sbin/runner-register')
if link.exists() or link.is_symlink():
    link.unlink()
link.symlink_to('/usr/local/libexec/runner-template/runner-register.py')
os.lchown(link, UID_BASE, GID_BASE)
link = path('etc/systemd/system/multi-user.target.wants/runner-template-first-boot.service')
if link.exists() or link.is_symlink():
    link.unlink()
link.symlink_to('/etc/systemd/system/runner-template-first-boot.service')
os.lchown(link, UID_BASE, GID_BASE)
write('etc/motd', '\nGitHub runner template clone: run runner-register to register both runners.\nGuide: /usr/local/libexec/runner-template/README.txt\nHealth check: runner-env-check --smoke\n\n')
write('opt/runner-toolchain/README.txt', (PAYLOAD / 'README.txt').read_text())
# Verification reports and source installation logs are replaced by clone verification later.
for relative in ['opt/runner-toolchain/verification.json', 'usr/local/libexec/runner-env-setup/status.txt']:
    remove(relative)
# Remove installation provenance and old script bytecode before public distribution.
for relative in ['opt/runner-toolchain/installed.json', 'opt/runner-toolchain/manifest.requested.json',
                 'usr/local/libexec/runner-env-setup/manifest.json']:
    p = path(relative)
    if p.is_file():
        data = json.loads(p.read_text())
        data.pop('requirements_sources', None)
        if isinstance(data.get('manifest'), dict):
            data['manifest'].pop('requirements_sources', None)
        write(relative, json.dumps(data, indent=2) + '\n')
for relative in ['usr/local/libexec/runner-env-setup', 'usr/local/libexec/github-runner-cleanup',
                 'usr/local/libexec/runner-template']:
    for p in list(path(relative).rglob('__pycache__')):
        remove(str(p.relative_to(ROOT)))
for relative in ['etc/systemd/system/runner-env-setup.service', 'usr/local/libexec/runner-env-setup/runner-env-setup.service']:
    unit = path(relative)
    if unit.is_file():
        lines = unit.read_text().splitlines()
        write(relative, '\n'.join('Description=Install and verify the GitHub Actions runner toolchain' if line.startswith('Description=') else line for line in lines) + '\n')
# Regenerate generic mail identity; no copied host/domain settings or mail history.
write('etc/mailname', 'localhost\n')
for relative in ['var/lib/postfix', 'var/spool/postfix/etc']:
    clear(relative)
installer = path('usr/local/libexec/runner-env-setup/install.py')
if installer.is_file():
    write('usr/local/libexec/runner-env-setup/install.py', (PAYLOAD/'toolchain-install.py').read_text(), 0o755)

for runner in cfg['runners']:
    assert not path(runner['root'] + '/.runner').exists()
    assert not path(runner['root'] + '/.credentials').exists()
assert not list(path('etc/systemd/system').rglob('actions.runner.*'))
assert not list(path('etc/ssh').glob('ssh_host_*'))
assert path('etc/machine-id').stat().st_size == 0
write('var/lib/github-runner-template/image.json', json.dumps({'project': 'proxmox-github-runner', 'date': datetime.now(timezone.utc).isoformat(), 'runner_count': 2, 'registered': False}, indent=2) + '\n')
for unit in ['github-runner-cleanup.timer', 'github-runner-workspace-cleanup.path']:
    assert not any((d/unit).exists() or (d/unit).is_symlink() for d in path('etc/systemd/system').glob('*.wants'))
    check = subprocess.run(['systemctl', '--root', str(ROOT), 'is-enabled', unit], text=True, capture_output=True)
    assert check.stdout.strip() == 'disabled', (unit, check.stdout, check.stderr)

print('SANITIZED: no runner credentials, no enabled runner service, no cloned Docker data', flush=True)
