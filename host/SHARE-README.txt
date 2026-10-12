Portable GitHub runner LXC template (Debian 13, amd64)

Share the .tar.zst file together with its .sha256 and this README. The archive is a
root filesystem CT template, not a vzdump backup. Verify it with:
  sha256sum -c github-runner-debian13-<BUILD>_amd64.tar.zst.sha256

On another Proxmox host, upload the .tar.zst under a storage's CT Templates view
(or copy it into /var/lib/vz/template/cache), then use Create CT and choose this image.
Choose your own hostname, disk storage, bridge and DNS. Enable Unprivileged container
and nesting=1. Recommended resources: 4 vCPU, 8192 MiB RAM, 1024 MiB swap, 64 GiB disk.
The archive contains no source-host network configuration. Configure the new CT's network
for your environment, using DHCP only if that network provides a DHCP server.

Example on the destination Proxmox host (replace all angle-bracket values):
  pct create <FREE-ID> local:vztmpl/<FILE>.tar.zst --hostname runner-team-a \
    --rootfs <DISK-STORAGE>:64 --cores 4 --memory 8192 --swap 1024 \
    --unprivileged 1 --features nesting=1 --ostype debian --arch amd64 \
    --net0 name=eth0,bridge=<YOUR-BRIDGE>,ip=dhcp --nameserver <YOUR-DNS> \
    --cmode shell --onboot 0
  pct start <FREE-ID>
  pct enter <FREE-ID>
  runner-register --url https://github.com/<YOUR-ORGANIZATION>

The registration token is requested with hidden input. Two runners named
<hostname>-01 and <hostname>-02 are created. A repository URL is also supported.
If you use the GUI, set a new local root password in Create CT or use pct enter;
password hashes are deliberately locked in the image. No password is supplied here.
Use runner-env-check --smoke to verify the destination's environment.

The produced archive was actually imported and boot-tested on Proxmox VE 8.4.17 with
Debian 13 amd64, unprivileged LXC and nesting enabled. Other host/kernel combinations
should be verified with the same command. Existing GitHub registrations, job data,
Docker data, user credentials and host keys are not included. Configure your own
cloud credentials, registry logins and project secrets after import.

To turn your clean, still-unregistered destination CT into a reusable local template,
shut it down and run pct template <ID>. Prefer Full Clone for independent new runners.
