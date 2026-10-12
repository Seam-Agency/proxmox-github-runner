Runner template update on the Proxmox host

After installing or updating global tools in source CT 100, run as host root:
  runner-template-update
To update CT template 9000 and create a portable, import-tested LXC archive:
  runner-template-update --export
Read-only preflight: runner-template-update --check
Last/current status: runner-template-update --status

Configuration: /etc/runner-template-update.json
Implementation: /usr/local/libexec/runner-template-update/
Build history and rollback backups: /var/lib/runner-template-update/runs/
Portable exports: /var/lib/vz/template/cache/github-runner-debian13-*_amd64.tar.zst
Each successful export has a SHA-256 file and a sharing/import README next to it.
No network sharing or public download endpoint is created by this command.

A host lock prevents simultaneous updates. Two unused IDs from 9100-9199 are selected.
The source remains running: a temporary snapshot is copied, then immediately removed.
The independent copy is sanitized offline before any boot. Both runner users must pass
all baseline tool checks and compile/render/execute checks before the target is replaced.
With --export the archive is also imported into a fresh CT and checked before publication.
The old 9000 is backed up using vzdump before replacement; failures during replacement
attempt to restore it automatically. Failed recovery retains the candidate and backup.
Only staging containers bearing this build's ownership marker are removed.

The tool requires the current unprivileged amd64, LVM-thin, single-root-disk setup.
It refuses extra mounts/devices, custom LXC options, locks, protected templates and
linked-clone dependencies. Use Full Clone for new runners so future updates can replace
9000 independently. Run after package installation finishes. Per-user tool directories,
workspaces and caches are cleared; install reusable tools globally in /usr/local or
/opt/runner-toolchain. This is a manual command, not a periodic rebuild service.

Rollback copies and exports are kept so no user's downloaded/shared revision is silently
removed. Their paths and sizes are in each result.json. Delete obsolete revisions after
you no longer need them; preflight refuses when the host has less than 20 GiB free.

Manual recovery if the command reports rollback_error:
1. Read latest.json and preserve the indicated rollback archive and build report.
2. Confirm ID 9000 is free (do not overwrite a container created by someone else).
3. pct restore 9000 <rollback/vzdump-lxc-9000-....tar.zst> --storage <DISK-STORAGE> --unique 1
4. If it was not restored as a template: pct template 9000
The source CT 100 is never a rollback or deletion target.
