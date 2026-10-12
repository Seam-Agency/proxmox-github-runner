# Proxmox GitHub Runner

A portable **Debian 13 / amd64 LXC image** with two independent GitHub Actions runner installations, a shared CI toolchain, and bounded workspace/cache cleanup. Published by [Seam Agency](https://github.com/Seam-Agency).

[Download the latest image](https://github.com/Seam-Agency/proxmox-github-runner/releases/latest) · [Import guide](docs/IMPORT.md) · [Template updates](docs/UPDATES.md) · [Tool inventory](docs/TOOLS.md)

## Start a runner pair

1. Download the `.tar.zst` image and `SHA256SUMS` from Releases, then run `sha256sum -c SHA256SUMS` in the download directory.
2. Upload the image to **Proxmox → storage → CT Templates** and create an **unprivileged** Debian container with **nesting enabled**. Choose your own bridge, DNS, hostname, and disk storage. Suggested capacity: **4 vCPU, 8 GiB RAM, 1 GiB swap, 64 GiB disk**.
3. Start the container, enter its console, and register it with your own GitHub organization or repository:

```sh
runner-register --url https://github.com/YOUR-ORGANIZATION
runner-env-check --smoke
```

The registration command asks for a fresh GitHub runner registration token using hidden input. It creates `<hostname>-01` and `<hostname>-02`, then enables both runner services and cleanup. Use a unique hostname. No GitHub account, registration token, root password, SSH host key, job workspace, or Docker workload is supplied in the image.

## Included environment

| Area | Tools |
| --- | --- |
| Containers and Kubernetes | Docker Engine, Compose, Buildx, Helm 3, kubectl, Kustomize, Argo CD, Talosctl |
| Languages | Go, Node.js 22, npm/pnpm/Yarn, TypeScript, Bun, Rust, Python 3.12 and 3.13, Elixir/Erlang, PowerShell |
| Build and quality | GCC, Clang/LLVM, CMake, Ninja, protobuf, Buf, grpcurl, ShellCheck, actionlint, hadolint, Trivy |
| Utilities | Git/LFS, GitHub CLI, jq/yq, SOPS/age, Skopeo, PostgreSQL/Redis clients, ripgrep, fd, rsync |

The release was imported and booted on **Proxmox VE 8.4.17**, with **61 tool/access checks and 19 execution/build/render checks per runner user**. These checks verify the environment; they do not substitute for your project's CI suite. See [TOOLS.md](docs/TOOLS.md) for versions and scope.

## Refresh your local template

The optional host updater takes a snapshot of a running runner container, sanitizes an independent copy, tests it, backs up the previous template, and replaces it. The live source remains running.

```sh
sudo sh install-host.sh
# Set source/target IDs and staging range in /etc/runner-template-update.json.
sudo runner-template-update --check
sudo runner-template-update             # Refresh the template
sudo runner-template-update --export    # Also create and import-test an archive
```

It supports **unprivileged amd64 containers on LVM-thin storage**, with one root disk and the two-runner layout supplied by this image. Use **Full Clone** for new runners: the updater refuses templates with linked-clone dependencies. [Read the update and recovery guide](docs/UPDATES.md) before your first run.

## Repository layout

- `host/` contains the template updater, sanitizer, and archive/guest audits.
- `guest/` contains first-boot identity generation and runner registration.
- `toolchain/` contains the pinned download manifest, installer, and environment check.
- `cleanup/` contains the job-completed hook, bounded maintenance implementation, and example policy.

The primary installation path is the released image. These sources also explain and maintain its components; the toolchain installer expects the image's existing runner users, Docker installation, and cleanup services. APT packages and the Python 3.12 patch selected by `uv` can change on a future rebuild, so this is not a bit-for-bit reproducible base-image builder.

## Trust and licenses

Run trusted workflows on persistent self-hosted runners. Jobs share the container's Docker daemon and can affect its other jobs; separate users do not provide strong isolation against hostile workflows. Use separate disposable infrastructure for untrusted pull requests. See [GitHub's self-hosted runner guidance](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions#hardening-for-self-hosted-runners).

Project scripts are MIT licensed. Bundled operating-system packages, language runtimes, and tools retain their upstream licenses; see [THIRD_PARTY.md](THIRD_PARTY.md). The image contains client tools, not cloud or registry credentials.
