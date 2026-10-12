#!/bin/sh
set -eu
ssh-keygen -A
install -d -m 0755 /var/lib/github-runner-template
printf '%s\n' "$(date -u +%FT%TZ)" > /var/lib/github-runner-template/initialized
