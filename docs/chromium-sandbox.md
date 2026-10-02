# Chromium sandbox compatibility

Use the explicit `seccomp=chromium` selector for a Chromium process that keeps
its browser sandbox enabled. Ordinary containers keep the existing policy.
Arbitrary profile filenames, `seccomp=unconfined`, additional capabilities, and
privileged containers remain denied.

Build the repository's [minimal Chromium fixture](../tests/fixtures/chromium/README.md)
first, then run its standalone example:

```sh
bin/podman run --rm --init --network=none --user pwuser \
  --security-opt seccomp=chromium \
  --shm-size=256m \
  localhost/paranoid-podman-chromium-test:dev
```

The example renders inline HTML and prints a small result without writing files.
Replace any project-supplied `seccomp=./.../seccomp_profile.json` argument with
the selector; do not supply both. The explicit runtime user is required even
if the image already declares `USER pwuser`.

Compose uses the same selector:

```yaml
services:
  browser:
    image: localhost/paranoid-podman-chromium-test:dev
    network_mode: none
    user: pwuser
    init: true
    security_opt:
      - seccomp=chromium
    shm_size: 256m
```

Both entry points enforce `cap-drop=ALL` and `no-new-privileges` for this
profile. Compose writes the trusted absolute profile path into its reviewed
snapshot. Direct run/create resolves the selector after validating the full
request. The packaged profile is checked against a pinned SHA-256; missing,
modified, or symlinked profile files fail before provider execution. Its
directory belongs to the trusted guard installation, like the Python policy
code itself, and must not be exposed as an untrusted writable host mount.

## Access to a host-local test server

Use the generic [host loopback option](host-loopback.md) to reach a deliberately
selected host service. The `scripts/test.sh loopback` integration test creates
its own temporary HTTP servers and uses this same fixture image; no application
frontend, API, or fixed host port is required.

## Why this exception exists

Chromium's user-namespace sandbox calls `chroot("/proc/self/fdinfo/")` to
restrict its filesystem view. Podman's default seccomp rules allow `chroot`
only when `CAP_SYS_CHROOT` was retained in the container configuration.
Dropping all capabilities makes the outer seccomp filter reject this syscall,
even when Chromium has the required capability in a nested user namespace.
The browser can then abort with `Check failed: sys_chroot(...) == 0`.

The bundled profile removes the two capability-conditional `chroot` rules
from the reviewed baseline and adds one unconditional seccomp allow for
`chroot`. Every other rule, architecture mapping, and default action is
preserved. This does not grant a capability: the kernel still checks the
caller's namespace-scoped permissions. It permits the syscall for all
processes in the selected container, not just Chromium or this specific path.

This deliberately expands the selected container's allowed syscall surface;
it is not a claim of identical security. The browser's own sandbox must remain
enabled. Do not compensate for further failures by disabling seccomp or the
browser sandbox. Host namespace/LSM restrictions and future Chromium changes
can still prevent startup; passing policy tests alone is not browser proof.

## Profile provenance and maintenance

`src/paranoid_podman/profiles/chromium.json` derives from the system
`/usr/share/containers/seccomp.json` supplied by `containers-common 1:0.69.1-1`
on the validation host, based on the
[containers/common seccomp profile](https://github.com/containers/common/blob/v0.69.1/pkg/seccomp/seccomp.json).
The baseline file's SHA-256 is
`9b755202516aee4b45d9d411ab800c20fe4f7af97166a93b7f07d5b16c1a4ecd`.
The bundled derivative's SHA-256 is
`b974a98ecb25b56323f00c67437aa87b5e64beb3698f706062a03877218c1f94`.

The upstream profile is Apache-2.0 licensed; the license is shipped alongside
it as `profiles/LICENSE-APACHE`. The change is limited to the `chroot` rules
described above. The profile is a reviewed snapshot, not a dynamic copy of a
host's current defaults or custom `containers.conf` profile. New upstream
restrictions must be reviewed and incorporated explicitly. The regression
test fingerprints all non-`chroot` rules so an update cannot silently expand
the exception.

After building the [minimal Chromium fixture](../tests/fixtures/chromium/README.md),
run `scripts/test.sh chromium` (default image:
`localhost/paranoid-podman-chromium-test:dev`; override with
`PARANOID_PODMAN_CHROMIUM_IMAGE`). This explicit integration test starts one
temporary container through the source guard with no network, host mounts,
or published ports. It checks zero capabilities, `no-new-privileges`, seccomp,
Chromium's additional renderer filter and PID namespace, software WebGL2, and
a PNG screenshot. It does not fetch an image or require an application service.

References: [Chromium sandbox source](https://chromium.googlesource.com/chromium/src/+/lkgr/sandbox/linux/services/credentials.cc),
[Linux user namespaces](https://man7.org/linux/man-pages/man7/user_namespaces.7.html),
[chroot permission checks](https://man7.org/linux/man-pages/man2/chroot.2.html).
