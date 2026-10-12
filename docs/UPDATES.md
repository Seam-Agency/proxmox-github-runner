# Update a local Proxmox template

Install global tools in your source runner container, finish package operations, then run the updater as root on the **Proxmox host**. Install reusable tools under `/usr/local` or `/opt/runner-toolchain`: user homes, job workspaces, and caches are cleared in exported copies.

```sh
sh install-host.sh
editor /etc/runner-template-update.json
runner-template-update --check
runner-template-update --export
runner-template-update --status
```

The example configuration uses source `100`, template `9000`, and staging IDs `9100–9199`; set these to your environment. The source must have both registered runner services active, the target must already be a template, and both must use the same LVM-thin storage. The updater refuses extra mounts/devices, custom LXC options, protection, locks, privileged guests, and linked clones. Always create independent **Full Clone** runners if you want to replace this template later.

Each run performs the following sequence:

1. Lock the updater, check source services and package-manager locks, and reserve two unused staging IDs.
2. Snapshot the running source, make a full copy, remove the temporary snapshot, and sanitize the stopped copy before any boot.
3. Boot the candidate and run the tool and execution checks as both runner users, then sanitize it again.
4. With `--export`, write an archive using container UID/GID ownership, validate it, and create/boot a fresh test container from it.
5. Back up the previous template with `vzdump`, publish the tested candidate under the target ID, and verify that the source was not restarted and both runner services remain active.

Failures before replacement leave the previous template available. Replacement failures attempt to restore its backup. A failed automatic restore retains the candidate and backup for manual recovery. Temporary containers are removed only when they carry the current build's ownership marker.

Configuration: `/etc/runner-template-update.json`. Reports and rollback archives: `/var/lib/runner-template-update/runs/`. Exports: the configured `export_dir`, normally `/var/lib/vz/template/cache`. Successful exports include a checksum and import README. Old exports and rollback backups are retained; remove obsolete revisions yourself when appropriate. The free-space threshold is 20 GiB by default.

## Recovery

If `runner-template-update --status` reports `rollback_error`, preserve the indicated backup and report. Confirm that the target ID is free before restoring; never overwrite a container created by someone else.

```sh
pct restore TARGET_ID /path/to/rollback/vzdump-lxc-TARGET_ID-....tar.zst \
  --storage YOUR_DISK_STORAGE --unique 1
# Only if the restored configuration is not already a template:
pct template TARGET_ID
```

No scheduled rebuild or remote publishing service is installed. Review custom software/configuration added to the source before publishing an export: the sanitizer knows this image's credential paths and is not a universal scrubber for every application you might later install.
