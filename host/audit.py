import json,pathlib,subprocess
p=pathlib.Path
cfg=json.loads(p('/etc/github-runner-cleanup.json').read_text())
for slot in cfg['runners']:
    for name in ['.runner','.credentials','.credentials_rsaparams','.service']:
        assert not (p(slot['root'])/name).exists(),str(p(slot['root'])/name)
assert not list(p('/etc/systemd/system').rglob('actions.runner.*'))
for unit in ['github-runner-cleanup.timer','github-runner-workspace-cleanup.path']:
    state = subprocess.run(['systemctl','is-enabled',unit],text=True,capture_output=True).stdout.strip()
    assert state=='disabled',(unit,state)
for args in [['docker','ps','-aq'],['docker','images','-aq'],['docker','volume','ls','-q']]:
    assert not subprocess.check_output(args,text=True).strip()
assert all(line.split(':')[1] in ('!','*') for line in p('/etc/shadow').read_text().splitlines()),'Unexpected unlocked account or retained password hash'
assert p('/var/lib/github-runner-template/initialized').is_file()
for name in ['var/mail','var/spool/postfix/active','var/spool/postfix/deferred',
             'var/spool/postfix/hold','var/spool/postfix/incoming','var/spool/postfix/maildrop']:
    assert not any(x.is_file() for x in p('/'+name).rglob('*')),name
for name in ['etc/postfix/sasl_passwd','etc/postfix/sasl_passwd.db','etc/apt/auth.conf']:
    assert not p('/'+name).exists(),name
for home in [p('/root'),*[p(r['home']) for r in cfg['runners']]]:
    assert not (home/'.ssh').exists() or not any(x.is_file() and x.stat().st_size for x in (home/'.ssh').rglob('*'))
    for name in ['.kube','.aws','.azure','.git-credentials','.netrc','.npmrc','.docker/config.json']:
        assert not (home/name).exists(),str(home/name)
print(json.dumps({'runner_credentials_removed':True,'docker_state_empty':True,'passwords_locked':True,
                  'cleanup_disabled_until_registration':True,'first_boot_complete':True,'user_credentials_removed':True,'mail_queue_empty':True}))
