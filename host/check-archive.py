#!/usr/bin/python3
"""Read-only validation of numeric owners and safe rootfs archive paths."""
import subprocess,sys,tarfile
from pathlib import PurePosixPath
p=subprocess.Popen(['zstd','-q','-d','-c',sys.argv[1]],stdout=subprocess.PIPE)
seen=set()
bad=[]
with tarfile.open(fileobj=p.stdout,mode='r|') as archive:
    for member in archive:
        name=member.name.removeprefix('./').rstrip('/')
        seen.add(name)
        path=PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts:
            bad.append('unsafe archive path: '+name)
        if member.uid >= 65536 or member.gid >= 65536:
            bad.append('host UID/GID found: '+name)
        if name in ['etc/machine-id'] and member.size:
            bad.append('nonempty machine-id')
        if name.startswith('etc/ssh/ssh_host_'):
            bad.append('inherited host key: '+name)
        if PurePosixPath(name).name in ['.runner','.credentials','.credentials_rsaparams','.service']:
            bad.append('runner identity: '+name)
        if member.islnk() and (PurePosixPath(member.linkname).is_absolute() or '..' in PurePosixPath(member.linkname).parts):
            bad.append('unsafe hard link: '+name)
assert p.wait()==0
assert {'etc/os-release','etc/passwd','usr/local/sbin/runner-register'} <= seen
assert not bad, '\n'.join(bad[:20])
print('Archive validated: container UIDs, safe paths, empty machine ID, no runner identity or SSH host keys.',flush=True)
