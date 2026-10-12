# Release v1.0.0 tool inventory

| Component | Version |
| --- | --- |
| Debian | 13 |
| Docker / Compose / Buildx | 29.9.0 / 5.6.0 / 0.38.0 |
| GitHub Actions runner | 2.338.0 |
| Helm / kubectl / Kustomize | 3.22.0 / 1.35.8 / 5.8.3 |
| Go | 1.25.14 |
| Node.js / npm | 22.22.2 / 10.9.7 |
| pnpm / Yarn / Corepack | 12.11.2 / 1.22.22 / 0.36.0 |
| TypeScript / Bun | 7.0.2 / 1.4.3 |
| Rust | 1.95.0 with Cargo, rustfmt, and Clippy |
| Python | Debian 3.13.5 and uv-managed 3.12.15 |
| uv | 0.13.0 |
| Erlang / Elixir | OTP 27 / 1.18.3, with Hex and Rebar |
| PowerShell | 7.6.6 |
| yq / Buf / grpcurl | 4.54.1 / 1.73.0 / 1.9.4 |
| SOPS / Argo CD / Talosctl | 3.13.3 / 3.5.5 / 1.12.12 |
| actionlint / shfmt / hadolint / Trivy | 1.7.12 / 3.14.1 / 2.15.1 / 0.75.0 |
| protoc-gen-go / protoc-gen-go-grpc | 1.36.12 / 1.6.2 |

The complete APT package/version list is retained at `/opt/runner-toolchain/packages.installed.txt`. The tool artifact URLs and SHA-256 values are in `toolchain/manifest.json`; installed metadata is in `/opt/runner-toolchain/installed.json`.

Both runner users have `/usr/local/bin` in the PATH used by their Actions services. Tools installed globally remain available after workspace cleanup. Workflow setup actions may intentionally select a different version than the image default.

The 61 environment checks include versions, both users' Docker daemon access, and client availability. The 19 smoke checks compile or execute Go, Rust, C, Node, TypeScript, Bun, Python, and Elixir; render Helm/Kustomize; generate Go protobuf code; parse YAML/Compose; and lint shell code. Run them with `runner-env-check --smoke` as root inside a clone.
