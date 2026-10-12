#!/usr/bin/python3
"""Verify runner tools using the same saved PATH as the Actions services."""
import argparse,json,os,pwd,subprocess,tempfile
from pathlib import Path
CHECKS={
'helm':['helm','version','--short'],'kubectl':['kubectl','version','--client=true','-o','json'],
'kustomize':['kustomize','version'],'yq':['yq','--version'],'jq':['jq','--version'],
'go':['go','version'],'node':['node','--version'],'npm':['npm','--version'],'pnpm':['pnpm','--version'],
'yarn':['yarn','--version'],'corepack':['corepack','--version'],'tsc':['tsc','--version'],'bun':['bun','--version'],
'rustc':['rustc','--version'],'cargo':['cargo','--version'],'rustfmt':['rustfmt','--version'],
'clippy':['cargo','clippy','--version'],'python3':['python3','--version'],'python3.12':['python3.12','--version'],
'pip':['python3','-m','pip','--version'],'uv':['uv','--version'],'pipx':['pipx','--version'],
'elixir':['elixir','--version'],'mix':['mix','--version'],'hex':['mix','hex.info'],'pwsh':['pwsh','-NoLogo','-NoProfile','-Command','$PSVersionTable.PSVersion.ToString()'],
'protoc':['protoc','--version'],'buf':['buf','--version'],'protoc-gen-go':['protoc-gen-go','--version'],
'protoc-gen-go-grpc':['protoc-gen-go-grpc','--version'],'grpcurl':['grpcurl','--version'],
'gh':['gh','--version'],'sops':['sops','--version'],'age':['age','--version'],
'argocd':['argocd','version','--client','--short'],'talosctl':['talosctl','version','--client'],
'shellcheck':['shellcheck','--version'],'shfmt':['shfmt','--version'],'actionlint':['actionlint','--version'],
'hadolint':['hadolint','--version'],'yamllint':['yamllint','--version'],'trivy':['trivy','--version'],
'git':['git','--version'],'git-lfs':['git','lfs','version'],'git-filter-repo':['git','filter-repo','--version'],
'cmake':['cmake','--version'],'clang':['clang','--version'],'llvm':['llvm-config','--version'],
'gcc':['gcc','--version'],'make':['make','--version'],'ninja':['ninja','--version'],
'psql':['psql','--version'],'redis-cli':['redis-cli','--version'],'skopeo':['skopeo','--version'],
'rg':['rg','--version'],'fd':['fd','--version'],'rsync':['rsync','--version'],'zstd':['zstd','--version'],
'docker':['docker','version','--format','{{.Client.Version}} / {{.Server.Version}}'],
'buildx':['docker','buildx','version'],'compose':['docker','compose','version']}
def run_user(user, path, args, cwd='/tmp', timeout=45, extra=None):
    env={**os.environ,'PATH':path,'LC_ALL':'C.UTF-8','LANG':'C.UTF-8','ERL_FLAGS':'+S 2:2',**(extra or {})}
    return subprocess.run(['runuser','-u',user,'--',*args],cwd=cwd,env=env,text=True,capture_output=True,timeout=timeout)
def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--smoke',action='store_true'); parser.add_argument('--output'); args=parser.parse_args()
    report={'users':{},'failures':[]}
    cfg=json.loads(Path('/etc/github-runner-cleanup.json').read_text())
    for runner in cfg['runners']:
        user=pwd.getpwuid(runner['uid']).pw_name; path=(Path(runner['root'])/'.path').read_text().strip()
        results={}
        for name,command in CHECKS.items():
            p=run_user(user,path,command)
            output=(p.stdout+'\n'+p.stderr).strip()
            results[name]={'ok':p.returncode==0,'version':output[:450]}
            if p.returncode: report['failures'].append(user+': '+name+' -> '+output[:160])
        print(user+': '+str(sum(x['ok'] for x in results.values()))+'/'+str(len(results))+' tool checks passed',flush=True)
        if args.smoke:
            with tempfile.TemporaryDirectory(prefix='runner-env-smoke-',dir='/tmp') as name:
                root=Path(name); os.chown(root,runner['uid'],runner['gid']); root.chmod(0o700)
                files={
                'main.go':'package main\nimport "fmt"\nfunc main(){fmt.Println("go-ok")}\n',
                'main.rs':'fn main() { println!("rust-ok"); }\n',
                'main.c':'#include <stdio.h>\nint main(void){puts("c-ok");return 0;}\n',
                'chart/Chart.yaml':'apiVersion: v2\nname: runner-smoke\nversion: 0.1.0\n',
                'chart/templates/config.yaml':'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: {{ .Release.Name }}\ndata:\n  ready: "true"\n',
                'kustomization.yaml':'resources:\n- config.yaml\n',
                'config.yaml':'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: runner-smoke\ndata:\n  ready: "true"\n',
                'smoke.proto':'syntax = "proto3";\noption go_package="example.com/runner/smoke";\nmessage Check { string value = 1; }\n',
                'compose.yaml':'services:\n  verify:\n    image: hello-world:latest\n',
                'smoke.sh':'#!/bin/sh\nprintf "%s\\n" ok\n',
                'smoke.ts':'const answer: number = 42; console.log(answer);\n'}
                for relative,content in files.items():
                    p=root/relative; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(content); os.chown(p,runner['uid'],runner['gid'])
                smoke=[
                    ('go compile/run',['go','run','main.go']),
                    ('rust compile',['rustc','main.rs','-o','rust-smoke']),
                    ('rust run',['./rust-smoke']),
                    ('C compile',['cc','main.c','-o','c-smoke']),('C run',['./c-smoke']),
                    ('node execute',['node','-e','console.log("node-ok")']),
                    ('typescript compile',['tsc','--skipLibCheck','smoke.ts']),
                    ('bun execute',['bun','-e','console.log("bun-ok")']),
                    ('python venv',['python3','-m','venv','.venv']),
                    ('python venv execute',['.venv/bin/python','-c','print("python-ok")']),
                    ('python3.12 execute',['python3.12','-c','print("python312-ok")']),
                    ('python yaml/pytest/build',['python3','-c','import yaml,pytest,build;print("python-tools-ok")']),
                    ('Elixir execute',['elixir','-e','IO.puts("elixir-ok")']),
                    ('Helm render',['helm','template','smoke','chart']),('Kustomize render',['kustomize','build','.']),
                    ('YAML parse',['yq','.metadata.name','config.yaml']),
                    ('protobuf generation',['protoc','--go_out=.','--go_opt=paths=source_relative','smoke.proto']),
                    ('shell lint',['shellcheck','smoke.sh']),('Compose parse',['docker','compose','config','--quiet'])]
                for label,cmd in smoke:
                    p=run_user(user,path,cmd,cwd=name,timeout=120,extra={'GOCACHE':str(root/'go-cache'),'GOTOOLCHAIN':'local'})
                    if p.returncode: report['failures'].append(user+': '+label+' -> '+(p.stdout+p.stderr)[-600:])
                results['smoke']={'checks':len(smoke),'ok':not any(x.startswith(user+':') for x in report['failures'])}
                print(user+': '+str(len(smoke))+' execution/build/render checks '+('passed' if results['smoke']['ok'] else 'FAILED'),flush=True)
        report['users'][user]={'path':path,'checks':results}
    for item in report['failures']: print('FAIL',item,flush=True)
    if args.output: Path(args.output).write_text(json.dumps(report,indent=2)+'\n')
    raise SystemExit(bool(report['failures']))
if __name__=='__main__': main()
