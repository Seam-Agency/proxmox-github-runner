# Third-party software

The MIT license in this repository applies to the project scripts and documentation. It does not relicense software bundled in the LXC image.

Debian package copyright and license notices are available inside the image under `/usr/share/doc/<package>/copyright`. Runtime/tool distributions retain their included notices under `/opt/runner-toolchain` and `/opt/actions-runner*/`. The image keeps these files during sanitization.

Upstream binary download URLs and checksums are listed in [`toolchain/manifest.json`](toolchain/manifest.json). Principal upstream projects include [Debian](https://www.debian.org/), [GitHub Actions runner](https://github.com/actions/runner), [Docker Engine](https://github.com/moby/moby), [Go](https://go.dev/), [Node.js](https://nodejs.org/), [Rust](https://www.rust-lang.org/), [Python](https://www.python.org/), [Helm](https://helm.sh/), and [Kubernetes](https://kubernetes.io/).

Consult the relevant upstream license and source distribution when redistributing or modifying an included component. This project does not claim ownership of upstream binaries or trademarks.
