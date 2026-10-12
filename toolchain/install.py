#!/usr/bin/python3
"""Install the verified CI toolchain without interrupting active Actions jobs."""
import concurrent.futures,fcntl,hashlib,json,os,pwd,re,runpy,shutil,signal,subprocess,tarfile,time,zipfile
from pathlib import Path
HERE=Path('/usr/local/libexec/runner-env-setup')
BASE=Path('/opt/runner-toolchain')
CACHE=Path('/var/cache/runner-env-setup')
BACKUP=Path('/root/runner-env-setup-'+time.strftime('%Y%m%dT%H%M%S'))
MANIFEST=json.loads((HERE/'manifest.json').read_text())
STANDARD_PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
os.environ.update(PATH=STANDARD_PATH,DEBIAN_FRONTEND='noninteractive',NEEDRESTART_MODE='l',LANG='C.UTF-8',LC_ALL='C.UTF-8',ERL_FLAGS='+S 2:2')
def log(message):
    print(time.strftime('%Y-%m-%dT%H:%M:%S%z')+' '+message,flush=True)
    (HERE/'status.txt').write_text(message+'\n')
def run(args,timeout=600,**kwargs):
    return subprocess.run([str(x) for x in args],check=True,text=True,timeout=timeout,**kwargs)
def output(args,timeout=60):
    return subprocess.check_output([str(x) for x in args],text=True,timeout=timeout).strip()
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()
def download(item):
    path=CACHE/(item['name']+'-'+item['version'].replace('/','_')+'.download')
    if not path.exists() or sha(path)!=item['sha256']:
        temporary=path.with_suffix('.partial')
        run(['curl','--fail','--silent','--show-error','--location','--proto','=https','--tlsv1.2','--retry','3','--connect-timeout','20','--max-time','900','--output',temporary,item['url']],timeout=950)
        assert sha(temporary)==item['sha256'],'Checksum mismatch: '+item['name']
        temporary.replace(path)
    return item['name'],path

def link(command,target):
    target=target.resolve(); assert target.is_file(),str(target)
    target.chmod(target.stat().st_mode|0o755)
    dest=Path('/usr/local/bin')/command
    if dest.is_symlink() and dest.resolve()==target:return
    previous=BACKUP/'bin'/command
    if (dest.exists() or dest.is_symlink()) and not previous.exists() and not previous.is_symlink():
        previous.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(dest,previous,follow_symlinks=False)
    temporary=dest.with_name(dest.name+'.runner-env.tmp'); temporary.unlink(missing_ok=True)
    temporary.symlink_to(target); temporary.replace(dest)

def install_artifact(item,archive):
    root=BASE/(item['name']+'-'+item['version'].replace('/','_'))
    done=root/'.installed-sha256'
    if not done.exists() or done.read_text().strip()!=item['sha256']:
        staging=root.with_name(root.name+'.staging')
        if staging.exists():shutil.rmtree(staging)
        staging.mkdir(mode=0o755)
        if item['kind']=='binary':shutil.copy2(archive,staging/item['name'])
        elif item['kind']=='zip':
            with zipfile.ZipFile(archive) as package:
                for member in package.infolist():
                    assert not Path(member.filename).is_absolute() and '..' not in Path(member.filename).parts
                package.extractall(staging)
        else:
            with tarfile.open(archive) as package:package.extractall(staging,filter='data')
        if item['kind']=='rust-installer':
            source=next(staging.glob('rust-*-x86_64-unknown-linux-gnu'))
            root.mkdir(exist_ok=True)
            run(['bash',source/'install.sh','--prefix='+str(root),'--disable-ldconfig','--components=rustc,rust-std-x86_64-unknown-linux-gnu,cargo,rustfmt-preview,clippy-preview'],cwd=source,timeout=300)
            shutil.rmtree(staging)
        else:
            assert not root.exists(),'Unmarked installation directory already exists: '+str(root)
            staging.replace(root)
        done.write_text(item['sha256']+'\n')
    for command,relative in item['binaries'].items():link(command,root/(relative or item['name']))
    log('Installed '+item['name']+' '+item['version'])

def prepare_user_path(runner):
    user=pwd.getpwuid(runner['uid']).pw_name
    root=Path(runner['root']); path=root/'.path'
    backup=BACKUP/(user+'.path.before')
    if not backup.exists():shutil.copy2(path,backup)
    entries=STANDARD_PATH.split(':')+path.read_text().strip().split(':')+[runner['home']+'/.local/bin',runner['home']+'/go/bin',runner['home']+'/.cargo/bin']
    content=':'.join(dict.fromkeys(x for x in entries if x))+'\n'
    temporary=path.with_name('.path.runner-env.tmp');temporary.write_text(content);temporary.chmod(0o600);os.chown(temporary,runner['uid'],runner['gid']);temporary.replace(path)
    for relative in ['.local/bin','.cargo/bin','go/bin']:
        folder=Path(runner['home'])/relative;folder.mkdir(parents=True,exist_ok=True)
        current=folder
        while str(current)!=runner['home']:
            os.chown(current,runner['uid'],runner['gid']);current=current.parent

def main():
    assert os.geteuid()==0
    assert shutil.disk_usage('/').free>10*1024**3,'At least 10 GiB free space is required'
    BASE.mkdir(mode=0o755,exist_ok=True);CACHE.mkdir(mode=0o700,exist_ok=True);BACKUP.mkdir(mode=0o700,exist_ok=True)
    if not (BACKUP/'packages.before.txt').exists():
        (BACKUP/'packages.before.txt').write_text(output(['dpkg-query','-W','-f=${binary:Package}\t${Version}\n']))
    shutil.copy2(HERE/'manifest.json',BASE/'manifest.requested.json')
    log('Downloading 19 pinned tool archives and verifying SHA-256 checksums')
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        downloads=dict(pool.map(download,MANIFEST['artifacts']))
    log('All archive checksums verified; waiting for active jobs to finish')
    clean=runpy.run_path('/usr/local/libexec/github-runner-cleanup/cleanup.py',run_name='runner_env_cleanup')
    cfg=clean['config']()
    run(['systemctl','stop','github-runner-cleanup.timer'])
    try:
        while output(['systemctl','show','github-runner-cleanup.service','-p','ActiveState','--value']) in ('activating','active','deactivating'):
            time.sleep(2)
        with clean['drain_runners'](cfg) as records:
            with clean['root_lock']():
                clean['require_idle'](records,clean['processes']())
                log('Both runners are idle; installing build dependencies')
                run(['apt-get','update','-qq'],timeout=300)
                run(['apt-get','install','--no-upgrade','--no-install-recommends','-y',*MANIFEST['apt_packages']],timeout=1200)
                for item in MANIFEST['artifacts']:install_artifact(item,downloads[item['name']])
                link('fd',Path('/usr/bin/fdfind'))
                log('Installing pinned Node package managers and TypeScript')
                npmroot=BASE/'npm-tools';npmroot.mkdir(exist_ok=True)
                run(['npm','install','--global','--prefix',npmroot,'--no-audit','--no-fund',*[k+'@'+v for k,v in MANIFEST['npm_packages'].items() if k!='corepack']],timeout=300)
                for name in ['pnpm','pnpx','yarn','yarnpkg','tsc']:link(name,npmroot/'bin'/name)
                corepackroot=BASE/'corepack'
                run(['npm','install','--global','--prefix',corepackroot,'--no-audit','--no-fund','corepack@'+MANIFEST['npm_packages']['corepack']],timeout=300)
                link('corepack',corepackroot/'bin/corepack')
                log('Installing Python 3.12 alongside the Debian Python interpreter')
                pyroot=BASE/'python'
                run(['uv','python','install','3.12','--install-dir',pyroot,'--no-bin'],timeout=300)
                interpreters=list(pyroot.glob('cpython-3.12.*-linux-x86_64-gnu/bin/python3.12'))
                assert len(interpreters)==1,interpreters
                link('python3.12',interpreters[0])
                log('Installing the protobuf generator pinned protobuf generator versions')
                gobin=BASE/'protoc-plugins'/'bin';gobin.mkdir(parents=True,exist_ok=True)
                goenv={**os.environ,'GOBIN':str(gobin),'GOPATH':str(CACHE/'go-path'),'GOCACHE':str(CACHE/'go-build'),'GOMODCACHE':str(CACHE/'go-mod'),'GOTOOLCHAIN':'local'}
                run(['go','install','google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.12'],env=goenv,timeout=300)
                run(['go','install','google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.6.2'],env=goenv,timeout=300)
                for name in ['protoc-gen-go','protoc-gen-go-grpc']:link(name,gobin/name)
                for runner in cfg['runners']:
                    prepare_user_path(runner)
                    user=pwd.getpwuid(runner['uid']).pw_name
                    log('Preparing Hex and Rebar for '+user)
                    run(['runuser','-u',user,'--','mix','local.hex','--force'],cwd='/tmp',timeout=180)
                    run(['runuser','-u',user,'--','mix','local.rebar','--force'],cwd='/tmp',timeout=180)
                log('Verifying every tool and compiling/rendering samples as both runner users')
                run([HERE/'check.py','--smoke','--output',BASE/'verification.json'],timeout=600)
                # Newly available tools keep regenerable caches under the existing shared limit.
                policy=Path('/etc/github-runner-cleanup.json')
                if not (BACKUP/'cleanup-policy.before.json').exists():shutil.copy2(policy,BACKUP/'cleanup-policy.before.json')
                actual=json.loads(policy.read_text());known={x[0] for x in actual['home_caches']}
                additions=[['.local/share/pnpm/store',2048,7,2],['.cache/yarn',1024,7,2],['.cache/node/corepack',512,30,3],['.bun/install/cache',1024,7,2],['.cache/trivy',1024,7,2],['.cache/buf',512,7,2],['.cache/helm',256,7,2],['.cargo/git',1024,7,3]]
                actual['home_caches'] += [x for x in additions if x[0] not in known]
                temp=policy.with_suffix('.runner-env.tmp');temp.write_text(json.dumps(actual,indent=2)+'\n');temp.chmod(policy.stat().st_mode&0o777);temp.replace(policy)
                log('Activating the permanent PATH at an idle job boundary')
                clean['activate'](cfg,drained=records)
        (BASE/'packages.installed.txt').write_text(output(['dpkg-query','-W','-f=${binary:Package}\t${Version}\n']))
        (BASE/'npm.installed.json').write_text(output(['npm','list','--global','--prefix',BASE/'npm-tools','--depth=0','--json'])+'\n')
        (BASE/'installed.json').write_text(json.dumps({'installed_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'manifest':MANIFEST,'python312':output(['/usr/local/bin/python3.12','--version']),'verification':str(BASE/'verification.json')},indent=2)+'\n')
        link('runner-env-check',HERE/'check.py')
        for runner in cfg['runners']:assert output(['systemctl','is-active',runner['service']])=='active'
        shutil.rmtree(CACHE)
        checks=runpy.run_path(str(HERE/'check.py'),run_name='runner_env_check')['CHECKS']
        log('COMPLETE: both runners active; '+str(len(checks))+' tools and 19 execution checks verified for each user')
    finally:
        clean['resume']('maintenance')
        run(['systemctl','start','github-runner-cleanup.timer'])
if __name__=='__main__':
    with open('/run/runner-env-setup.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        main()
