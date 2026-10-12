Reusable GitHub Actions runner container

Debian 13 amd64 with two independent runner users and the installed global CI toolchain.
This image is unregistered. Passwords are locked. Use the Proxmox host's pct enter command
or the Proxmox shell console to perform first-time registration.

1. Give the new container a unique hostname and configure networking for your own host.
2. Run: runner-register --url https://github.com/YOUR-ORGANIZATION
   A repository URL is also supported. For the original organization, use
   runner-register --url https://github.com/YOUR-ORGANIZATION
3. Paste a freshly generated GitHub runner registration token at the hidden prompt.
   The helper registers <hostname>-01 and <hostname>-02 without replacing existing runners.
4. Run: runner-env-check --smoke
5. Verify both runners in GitHub; enable CT start-at-boot if desired.

runner-register --plan --url https://github.com/YOUR-ORGANIZATION previews the names.
Optional flags: --prefix unique-name --group Default --labels proxmox,debian13,docker

Installed tooling includes Helm, kubectl, Docker/Compose/Buildx, Go, Node/npm/pnpm/Yarn,
Bun/TypeScript, Rust/Cargo, Python/uv, Elixir/Erlang, PowerShell, compilers, protobuf/Buf,
Kustomize, SOPS/age, Argo CD, Talos and CI lint/security tools. Exact installed versions
are in /opt/runner-toolchain/installed.json; the built image was tested with both users.
Future globally installed packages and tools are captured by runner-template-update.
Install persistent tools system-wide under /usr/local or /opt/runner-toolchain, rather
than in job workspaces, runner homes or caches; those locations are sanitized.

Runner registrations, workspaces, user credential stores, Docker images/containers/volumes,
logs, caches, password hashes, machine ID and SSH host keys were removed from the image.
Every new guest generates its own identity. No runner credentials are embedded.
Only register the image with your intended organization or repository and token.

Workspace and cache cleanup stays disabled until registration succeeds. Registration
activates job-completion cleanup and the five-minute cache timer. The current cleanup
policy targets 24 GiB of shared caches and at least 16 GiB free space; global tools are kept.

4 vCPU, 8 GiB RAM, 1 GiB swap and 64 GiB disk are the original tested resources.
Use unprivileged LXC with nesting=1 for Docker. Additional workload-specific privileges,
cloud access, Kubernetes contexts, registry logins and project secrets are not included.
