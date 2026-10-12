# Import the image

Download the release image and `SHA256SUMS`, keep their filenames unchanged, and verify:

```sh
sha256sum -c SHA256SUMS
```

The `.tar.zst` is a root-filesystem **CT template**, not a `vzdump` backup. Upload it in the destination storage's **CT Templates** view, then select it in **Create CT**. Use an unprivileged container, enable **Options → Features → Nesting**, and choose the destination's own network and DNS settings.

For the CLI, replace every uppercase placeholder before executing:

```sh
pct create FREE_ID local:vztmpl/IMAGE.tar.zst \
  --hostname runner-team-a --rootfs DISK_STORAGE:64 \
  --cores 4 --memory 8192 --swap 1024 \
  --unprivileged 1 --features nesting=1 --ostype debian --arch amd64 \
  --net0 name=eth0,bridge=YOUR_BRIDGE,ip=dhcp --nameserver YOUR_DNS \
  --cmode shell --onboot 0
pct start FREE_ID
pct enter FREE_ID
runner-register --url https://github.com/YOUR-ORGANIZATION
runner-env-check --smoke
```

Use DHCP only on a network that provides it; otherwise configure a suitable static address and gateway. The root account has a locked password in the archive. `pct enter` works without an image password, or you can set a new password through Create CT. First boot generates a new machine identity and SSH host keys.

Create a short-lived **runner registration token** in the destination organization/repository's **Settings → Actions → Runners → New self-hosted runner**. The helper accepts organization and repository URLs, prompts without echoing the token, and registers two services with the default group and labels `proxmox,debian13,docker`. Use `--group`, `--labels`, and `--prefix` to override them, or `--plan` to inspect names without registering.

Cleanup remains disabled until registration completes. Workspaces are cleared after completed jobs, and regenerable caches are maintained under the configured capacity/age limits. Global tools in `/opt/runner-toolchain` remain installed. Inspect `/etc/github-runner-cleanup.json` for the exact policy; its `cargo_volumes` list is empty so project-specific volumes require an explicit local policy.

Tested with Proxmox VE 8.4.17, Debian 13, amd64, and unprivileged LXC with nesting. Verify other host/kernel combinations with `runner-env-check --smoke`. When preparing another reusable image, use the sanitizer/updater; turning a previously registered runner directly into a template would retain its credentials.
