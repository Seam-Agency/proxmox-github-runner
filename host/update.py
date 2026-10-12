#!/usr/bin/python3
"""Build, verify and publish a clean Proxmox runner template from the live source."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

LIB = Path(__file__).resolve().parent
STATE = Path('/var/lib/runner-template-update')
CONFIG = Path('/etc/runner-template-update.json')
NODE = socket.gethostname().split('.')[0]

def log(message):
    print(time.strftime('%H:%M:%S ') + message, flush=True)

def command(args, capture=False, timeout=3600):
    args = [str(x) for x in args]
    if not capture:
        log(' '.join(args))
    p = subprocess.run(args, check=False, text=True, stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE if capture else None, timeout=timeout)
    if p.returncode:
        detail = (p.stderr or p.stdout or '').strip()[-2000:] if capture else ''
        raise RuntimeError(' '.join(args[:5]) + f' failed with exit {p.returncode}' + (': '+detail if detail else ''))
    return p.stdout.strip() if capture else ''

def cfg(ct):
    return json.loads(command(['pvesh','get',f'/nodes/{NODE}/lxc/{ct}/config','--output-format','json'], True))

def guest(ct, *args, capture=True, timeout=600):
    return command(['pct','exec',ct,'--',*args], capture, timeout)

def exists(ct):
    return Path(f'/etc/pve/lxc/{ct}.conf').exists()

def save(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.chmod(0o600)
    tmp.replace(path)

def hash_file(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(8*1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

def disk_volume(c):
    return c['rootfs'].split(',')[0]

def assert_independent(c):
    volume = disk_volume(c)
    storage, name = volume.split(':', 1)
    stores = json.loads(command(['pvesh','get','/storage','--output-format','json'], True))
    record = next(x for x in stores if x['storage'] == storage)
    if record['type'] != 'lvmthin':
        raise RuntimeError('This updater currently supports the configured LVM-thin storage only.')
    rows = json.loads(command(['lvs','--reportformat','json','-o','vg_name,lv_name,origin'], True))['report'][0]['lv']
    descendants = {name}
    while True:
        found = {r['lv_name'] for r in rows if r['vg_name'] == record['vgname'] and r.get('origin','').strip('[]') in descendants}
        if found.issubset(descendants):
            break
        descendants.update(found)
    descendants.remove(name)
    if descendants:
        raise RuntimeError('Template has linked clones/snapshots: ' + ', '.join(sorted(descendants)) +
                           '. Convert those guests to independent full copies before updating; nothing was replaced.')

def safe_config(c, template=False):
    if c.get('unprivileged') != 1 or c.get('arch','amd64') != 'amd64':
        raise RuntimeError('Expected an unprivileged amd64 container.')
    forbidden = [k for k in c if re.match(r'^(mp\d+|dev\d+|unused\d+|lxc(?:\.|$)|hookscript)', k)]
    if forbidden or c.get('lock') or c.get('protection'):
        raise RuntimeError('Unsupported mount/device/custom LXC option, lock or protection: ' + str(forbidden))
    if bool(c.get('template')) != template:
        raise RuntimeError('Source must be a container and target must be a template.')

def guest_audit(ct):
    code = (LIB/'audit.py').read_text()
    return json.loads(guest(ct, '/usr/bin/python3','-c',code))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', action='store_true', help='Also build and import-test a portable CT template archive')
    parser.add_argument('--check', action='store_true', help='Validate preconditions without changing containers')
    parser.add_argument('--status', action='store_true', help='Show the latest build status')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run on the Proxmox host as root.')
    settings = json.loads(CONFIG.read_text())
    if args.status:
        print((STATE/'latest.json').read_text() if (STATE/'latest.json').exists() else 'No builds yet.')
        return
    STATE.mkdir(mode=0o711, parents=True, exist_ok=True)
    STATE.chmod(0o711)
    lock = open('/run/runner-template-update.lock','w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Another template update is running; use --status.')
    source, target = settings['source'], settings['target']
    assert source != target and source >= 100 and target >= 100
    source_cfg, target_cfg = cfg(source), cfg(target)
    safe_config(source_cfg)
    safe_config(target_cfg, template=True)
    assert_independent(target_cfg)
    if command(['pct','status',source],True) != 'status: running':
        raise RuntimeError('The source must be running for runner and tool checks.')
    source_start = guest(source,'cat','/etc/machine-id')
    source_boot = guest(source,'cat','/proc/1/stat').rsplit(')',1)[1].split()[19]
    source_policy = json.loads(guest(source,'cat','/etc/github-runner-cleanup.json'))
    services = [r['service'] for r in source_policy['runners']]
    if guest(source,'systemctl','is-active',*services).splitlines() != ['active','active']:
        raise RuntimeError('Both source runners must be active before starting.')
    guest(source,'/usr/bin/python3','-c',
          "import fcntl; f=open('/var/lib/dpkg/lock','w'); fcntl.lockf(f,fcntl.LOCK_EX|fcntl.LOCK_NB); "
          "g=open('/var/lib/dpkg/lock-frontend','w'); fcntl.lockf(g,fcntl.LOCK_EX|fcntl.LOCK_NB)")
    if shutil.disk_usage(settings['export_dir']).free < settings['minimum_free_gib'] * 1024**3:
        raise RuntimeError('Insufficient host disk space for the export and rollback archives.')
    inventory = json.loads(command(['pvesh','get','/cluster/resources','--type','vm','--output-format','json'],True))
    taken = {x['vmid'] for x in inventory}
    free = [x for x in range(settings['staging_start'],settings['staging_end']+1) if x not in taken]
    if len(free) < 2:
        raise RuntimeError('Need two unused staging IDs in the configured range.')
    stage, test = free[:2]
    storage = disk_volume(source_cfg).split(':',1)[0]
    if disk_volume(target_cfg).split(':',1)[0] != storage:
        raise RuntimeError('Source and target must use the same configured LVM-thin storage.')
    log(f'Preflight passed: source {source}, target {target}, staging {stage}/{test}, storage {storage}.')
    if args.check:
        return
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    owner = 'runner-template-build-' + stamp + '-' + uuid.uuid4().hex[:8]
    snapshot_name = 'rtu-' + stamp + '-' + uuid.uuid4().hex[:6]
    run_dir = STATE/'runs'/stamp
    run_dir.mkdir(parents=True, mode=0o711)
    # vzdump reads temporary metadata inside the container's user namespace.
    # Traversal is allowed; build reports/configuration files remain root-only.
    run_dir.parent.chmod(0o711)
    save(run_dir/'source-config.json',source_cfg)
    save(run_dir/'previous-config.json',target_cfg)
    progress = {'state':'running','build':owner,'source':source,'target':target,'started':stamp,
                'run_dir':str(run_dir),'staging_ids':[stage,test],'export_requested':args.export}
    created = set()
    mounted = set()
    snapshot = False
    replaced = False
    published = False
    backup = None
    export_path = None
    archive_partial = None
    def step(name):
        progress['phase'] = name
        save(STATE/'latest.json',progress)
        save(run_dir/'result.json',progress)
        log(name)
    def owned(ct):
        return exists(ct) and owner in cfg(ct).get('description','')
    def stop_owned(ct):
        if ct == source or not owned(ct):
            raise RuntimeError('Refusing to stop a container not owned by this build.')
        if command(['pct','status',ct],True) == 'status: stopped':
            return
        try:
            command(['pct','shutdown',ct,'--timeout','30'],timeout=120)
        except (RuntimeError,subprocess.TimeoutExpired):
            # These are unregistered, disposable validation copies, with no CI jobs.
            log(f'Graceful shutdown timed out for owned test CT {ct}; stopping it.')
            if command(['pct','status',ct],True) != 'status: stopped':
                try:
                    command(['pct','stop',ct],timeout=180)
                except RuntimeError:
                    if command(['pct','status',ct],True) != 'status: stopped':
                        raise
        if command(['pct','status',ct],True) != 'status: stopped':
            raise RuntimeError('Owned test container did not stop: '+str(ct))
    def delete_owned(ct):
        if not owned(ct):
            return
        stop_owned(ct)
        command(['pct','destroy',ct])
        created.discard(ct)
    @contextlib.contextmanager
    def mount(ct):
        command(['pct','mount',ct])
        mounted.add(ct)
        try:
            yield Path(f'/var/lib/lxc/{ct}/rootfs')
        finally:
            command(['pct','unmount',ct])
            mounted.discard(ct)
    def clean(ct):
        with mount(ct):
            command(['/usr/bin/python3',LIB/'sanitize.py','--ct',ct,'--owner',owner,'--source',source,'--target',target])
            command(['fstrim',f'/var/lib/lxc/{ct}/rootfs'])
    def verify(ct, smoke):
        command(['pct','start',ct])
        deadline = time.monotonic()+120
        while time.monotonic() < deadline:
            try:
                if guest(ct,'systemctl','is-active','docker',timeout=20) == 'active':
                    break
            except (subprocess.CalledProcessError, RuntimeError):
                pass
            time.sleep(2)
        else:
            raise RuntimeError('Docker did not become ready in the test container.')
        arguments = ['/usr/local/bin/runner-env-check','--output','/tmp/template-check.json']
        if smoke:
            arguments.append('--smoke')
        guest(ct,*arguments,capture=False,timeout=1200)
        report = json.loads(guest(ct,'cat','/tmp/template-check.json'))
        assert not report['failures'] and len(report['users']) == 2
        assert all(all(x['ok'] for x in u['checks'].values()) for u in report['users'].values())
        audit = guest_audit(ct)
        assert guest(ct,'cat','/etc/machine-id') != source_start
        save(run_dir/f'verification-{ct}.json',{'tools':report,'audit':audit})
        stop_owned(ct)
        return report
    def clone(a,b,hostname,snap=None):
        params = ['pct','clone',a,b,'--full','1','--hostname',hostname,'--bwlimit',settings['copy_limit_kib'],
                  '--description',owner]
        if snap:
            params += ['--snapname',snap]
        created.add(b)
        command(params)
        command(['pct','set',b,'--onboot','0','--tags','no-auto-tags;template-build'])
    def export(root):
        nonlocal archive_partial, export_path
        export_path = Path(settings['export_dir'])/f'github-runner-debian13-{stamp}_amd64.tar.zst'
        archive_partial = run_dir/'portable.tar.zst.partial'
        tar_cmd = ['lxc-usernsexec','-m','u:0:100000:65536','-m','g:0:100000:65536','--',
                   'tar','cpf','-','--one-file-system','--numeric-owner','--acls','--xattrs',
                   '--xattrs-include=user.*','--xattrs-include=security.capability',
                   '--sparse','--exclude=./lost+found','--exclude=./dev/*','--exclude=./proc/*',
                   '--exclude=./sys/*','--exclude=./run/*','-C',str(root),'.']
        with archive_partial.open('xb') as output:
            tar = subprocess.Popen(tar_cmd,stdout=subprocess.PIPE)
            compressor = subprocess.Popen(['zstd','-T2','-6','-q','-c'],stdin=tar.stdout,stdout=output)
            tar.stdout.close()
            crc = compressor.wait()
            trc = tar.wait()
            if crc or trc:
                raise RuntimeError(f'Archive failed: tar={trc}, zstd={crc}')
        command(['zstd','-q','-t',archive_partial])
        # Owner and archive-path validation also catches accidental host UID exports.
        command(['/usr/bin/python3',LIB/'check-archive.py',archive_partial])
        archive_partial.replace(export_path)
        export_path.chmod(0o600)
        checksum = hash_file(export_path)
        (Path(str(export_path)+'.sha256')).write_text(checksum+'  '+export_path.name+'\n')
        progress['export'] = {'path':str(export_path),'sha256':checksum,'bytes':export_path.stat().st_size}
        log(f'Portable archive created: {export_path.name}')
    def target_description(report):
        count = len(next(iter(report['users'].values()))['checks'])-1
        return (f'# GitHub runner template\n\nUpdated from CT {source} at {stamp}. Build: {owner}\n'
                f'Each runner user passed {count} tool checks and 19 compile/render/execute checks.\n\n'
                '## Create a runner pair\n'
                'Clone > Full Clone, choose a new hostname, start the clone, then run:\n'
                '`runner-register --url https://github.com/YOUR-ORGANIZATION`\n'
                'Paste a fresh registration token at the hidden prompt. Two independent runner services and bounded cleanup are enabled.\n\n'
                '## Refresh this template\n'
                'On the Proxmox host: `runner-template-update`\n'
                'To also create and import-test a shareable archive: `runner-template-update --export`\n'
                'Status: `runner-template-update --status`\n'
                'Guide: `/usr/local/libexec/runner-template-update/HOST-README.txt`\n'
                f'Build reports and rollback backup: `{run_dir}`\n\n'
                'Global installed tools are copied; runner registrations, workspaces, Docker data, user credentials, passwords and machine identities are removed. '
                'Cleanup stays disabled until runner-register completes. Use Full Clone; updates refuse linked-clone dependencies.\n' +
                (f'\nPortable CT template: `{export_path}` (import-tested).\n' if export_path else ''))
    try:
        step('Snapshot and full copy of the running source')
        command(['pct','snapshot',source,snapshot_name,'--description','Temporary copy for runner template update'])
        snapshot = True
        clone(source,stage,'runner-template-build',snapshot_name)
        command(['pct','delsnapshot',source,snapshot_name])
        snapshot = False
        step('Remove copied registrations, job data and machine identity before the first boot')
        clean(stage)
        step('Boot the candidate and verify both runner users')
        report = verify(stage,True)
        step('Reseal the tested candidate')
        clean(stage)
        with mount(stage) as root:
            rp = root/'opt/runner-toolchain/template-verification.json'
            rp.write_text(json.dumps(report,indent=2)+'\n')
            os.chown(rp,100000,100000)
            if args.export:
                step('Build portable LXC template with container UID/GID ownership')
                export(root)
        if args.export:
            step('Create a fresh container from the portable archive and verify it')
            net = ','.join(x for x in source_cfg['net0'].split(',') if not x.startswith('hwaddr='))
            params = ['pct','create',test,export_path,'--hostname','runner-template-import-test',
                      '--rootfs',f'{storage}:64','--unprivileged','1','--arch','amd64','--ostype','debian',
                      '--cores',str(source_cfg.get('cores',4)),'--memory',str(source_cfg.get('memory',8192)),
                      '--swap',str(source_cfg.get('swap',1024)),'--features',source_cfg.get('features','nesting=1'),
                      '--net0',net,'--nameserver',source_cfg['nameserver'],'--cmode','shell',
                      '--onboot','0','--tags','no-auto-tags;template-build','--description',owner]
            created.add(test)
            command(params)
            verify(test,False)
            delete_owned(test)
            shutil.copyfile(LIB/'SHARE-README.txt',str(export_path)+'.README.txt')
            progress['export']['import_tested'] = True
        step('Back up the previous template before replacement')
        assert_independent(cfg(target))
        if cfg(target)['digest'] != target_cfg['digest']:
            raise RuntimeError('Target configuration changed during the build; previous template retained.')
        backup_dir = run_dir/'rollback'
        backup_dir.mkdir()
        command(['vzdump',target,'--mode','stop','--compress','zstd','--dumpdir',backup_dir,
                 '--remove','0','--quiet','1'])
        backups = list(backup_dir.glob('vzdump-lxc-*.tar.zst'))
        assert len(backups) == 1 and backups[0].stat().st_size > 0
        backup = backups[0]
        command(['zstd','-q','-t',backup])
        progress['rollback_backup'] = str(backup)
        step('Publish the verified candidate as template ' + str(target))
        assert_independent(cfg(target))
        # The supported pct operation rejects incompatible locks; never override protection.
        command(['pct','destroy',target])
        replaced = True
        clone(stage,target,target_cfg.get('hostname','github-runner-template'))
        clean(target)
        description = target_description(report)
        tags = ';'.join(x for x in target_cfg.get('tags','debian13;github-runner;ci-template').split(';')
                        if not re.fullmatch(r'nat-\d+\.\d+\.\d+\.\d+',x))
        command(['pct','set',target,'--onboot','0','--tags',tags,'--description',description])
        command(['pct','template',target])
        final_cfg = cfg(target)
        assert final_cfg.get('template') == 1 and final_cfg.get('onboot') == 0
        assert final_cfg.get('nameserver') == source_cfg.get('nameserver')
        assert guest(source,'cat','/etc/machine-id') == source_start
        assert guest(source,'cat','/proc/1/stat').rsplit(')',1)[1].split()[19] == source_boot
        assert guest(source,'systemctl','is-active',*services).splitlines() == ['active','active']
        published = True
        created.discard(target)
        save(run_dir/'published-config.json',final_cfg)
        progress.update(state='success',finished=datetime.now(timezone.utc).isoformat(),
                        source_runners_active=True,source_not_restarted=True,template_id=target,
                        tool_checks_per_user=len(next(iter(report['users'].values()))['checks'])-1,
                        smoke_checks_per_user=19)
        step('SUCCESS: template refreshed; existing source runners are active')
    except BaseException as error:
        progress.update(state='failed',error=str(error))
        log('FAILED: '+str(error))
        if replaced and not published and backup:
            try:
                if exists(target):
                    if not owned(target):
                        raise RuntimeError('Target is not owned by this build; retaining backup for manual recovery.')
                    delete_owned(target)
                command(['pct','restore',target,backup,'--storage',storage,'--unique','1'])
                if not cfg(target).get('template'):
                    command(['pct','template',target])
                progress['rollback_restored'] = True
                log('The previous template was restored from the rollback archive.')
            except BaseException as recovery:
                progress['rollback_error'] = str(recovery)
                log('Automatic recovery needs attention; backup and candidate retained: '+str(backup))
        save(STATE/'latest.json',progress)
        save(run_dir/'result.json',progress)
        raise
    finally:
        for ct in list(mounted):
            try:
                command(['pct','unmount',ct])
            except Exception as error:
                log('Unmount cleanup: '+str(error))
        if snapshot:
            try:
                command(['pct','delsnapshot',source,snapshot_name])
            except Exception as error:
                log('Source snapshot cleanup needs attention: '+str(error))
        for ct in list(created):
            if ct == target or progress.get('rollback_error'):
                continue
            try:
                delete_owned(ct)
            except Exception as error:
                log('Owned staging cleanup needs attention: '+str(error))
        if archive_partial and archive_partial.exists():
            archive_partial.unlink()
        if export_path and export_path.exists() and not progress.get('export',{}).get('import_tested'):
            export_path.unlink()
            Path(str(export_path)+'.sha256').unlink(missing_ok=True)
        save(STATE/'latest.json',progress)
        save(run_dir/'result.json',progress)
    if export_path:
        log('SHARE '+str(export_path))
    log('ROLLBACK '+str(backup))

if __name__ == '__main__':
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt('Terminated')))
    try:
        main()
    except (Exception,KeyboardInterrupt) as error:
        print('ERROR: '+str(error),file=sys.stderr,flush=True)
        sys.exit(1)
