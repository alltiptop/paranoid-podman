# Security policy

`paranoid-podman` is experimental defense-in-depth software for local rootless
container development. It reduces common, direct host-access paths; it is not
an absolute containment boundary. The guarantees and residual risks in the
[threat model](docs/threat-model.md) always apply.

## Supported versions

The current default branch and the latest published `0.x` release, when one
exists, receive best-effort security fixes. Older snapshots and unsupported
Podman, Compose, Python, DevPod, or operating-system versions are not supported.
See the [compatibility matrix](README.md#compatibility) for the reviewed
versions.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting form in the repository's
**Security** tab. The repository owner should enable this feature before the
project is made public.

If private reporting is unavailable, open a minimal issue asking for a private
contact channel. Do not include exploit details, secrets, private filesystem
paths, or a working proof of concept in a public issue.

Include:

- the affected `paranoid-podman` version;
- the operating system, Python, Podman, Compose provider, and DevPod versions
  involved;
- the smallest command or configuration shape that reproduces the problem;
- the policy result you expected and the result you observed;
- whether the real provider was reached; and
- a fake-provider regression test, when practical.

Use synthetic values and paths. Prefer a fake provider or a disposable
rootless environment; do not test a dangerous proof of concept against a host
that contains personal containers, images, credentials, or engine sockets.

## What to report here

Reports are especially useful when:

- a denied command, option, environment value, or Compose field reaches the
  real provider;
- a broad or sensitive host path is accepted unexpectedly;
- a protected path is writable in a newly created container or its read-only
  submount can be overridden;
- resolved secrets or raw provider output appear in diagnostics;
- a parser ambiguity changes the command or provider that is executed;
- a legacy, foreign, or differently installed container passes an active
  lifecycle provenance check;
- a recognized literal Dockerfile secret or unignored root-level credential
  path reaches the builder;
- a DevPod compatibility path bypasses the common creation policy; or
- a protected DevPod mode exposes the ambient `SSH_AUTH_SOCK`, loads more than
  the selected project identity, rewrites an SSH block outside DevPod's own
  markers, or prints private-key material;
- an IDE starts before its DevPod SSH block is protected, concurrent guarded
  launches lose that protection, or an unrelated PID is signaled as an agent;
- a DevPod option re-enables Git, registry, GPG-agent, or automatic SSH
  signing-key credential injection in a protected mode;
- an unconfigured or unsafe non-interactive DevPod connection proceeds without
  the documented fail-closed result or exact confirmation; or
- installation overwrites unrelated files, or uninstall removes files it does
  not own.

## What normally belongs upstream

Vulnerabilities in the kernel, Podman, the OCI runtime, the Compose provider,
Python dependencies, container images, or build tools should normally be
reported to the responsible upstream project. The same applies to attacks that
require arbitrary code already running as the host user, deliberate invocation
of the real provider, unsupported versions, or unknown and zero-day techniques
outside this wrapper's documented boundary.

If `paranoid-podman` makes an upstream issue materially easier to exploit,
exposes data while handling it, or claims to block the affected path, report
the wrapper interaction here as well.

An SSH-agent report should use a synthetic or disposable project key. A
forwarded agent is a signing capability even when private-key bytes are never
read, so include the number and fingerprints of identities visible to the
workspace without including private key contents.

## Response and disclosure

The maintainer aims to acknowledge a report within seven days and provide a
status update within thirty days. These are best-effort targets, not an SLA.

Please avoid public disclosure until a fix and regression test are available,
or until a disclosure date has been agreed with the maintainer.
