#!/bin/sh
# Install the manual template updater on a Proxmox host.
set -eu
[ "$(id -u)" -eq 0 ] || { echo 'Run as root on the Proxmox host.' >&2; exit 1; }
command -v pct >/dev/null
command -v pvesh >/dev/null
src=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
dst=/usr/local/libexec/runner-template-update
install -d -m 755 "$dst"
for f in update.py sanitize.py audit.py check-archive.py HOST-README.txt SHARE-README.txt; do
    install -m 644 "$src/host/$f" "$dst/$f"
done
for f in runner-register.py first-boot.sh runner-template-first-boot.service README.txt; do
    install -m 644 "$src/guest/$f" "$dst/$f"
done
install -m 644 "$src/toolchain/install.py" "$dst/toolchain-install.py"
chmod 755 "$dst/update.py"
ln -sfn "$dst/update.py" /usr/local/sbin/runner-template-update
if [ ! -e /etc/runner-template-update.json ]; then
    install -m 600 "$src/host/config.json" /etc/runner-template-update.json
fi
echo 'Installed. Review /etc/runner-template-update.json, then run runner-template-update --check.'
