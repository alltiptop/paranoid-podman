# Changelog

## v0.1.1

- Support Compose local-driver bind volumes with `type: none`, `o: bind`, and
  an absolute directory path. Convert service references to reviewed direct
  binds, preserving read-only restrictions, protected mounts, and external-path
  confirmation without creating or modifying engine volumes.
- Add the opt-in `seccomp=chromium` profile for sandboxed Chromium. Require an
  explicit non-root user, `cap-drop=ALL`, and `no-new-privileges` in both direct
  Podman commands and Compose.
- Support explicit host-loopback TCP forwarding with `pasta:-T,PORT` in direct
  commands and Compose. Accept one caller-selected port from 1 through 65535;
  automatic forwarding, additional networks, and broader host-access options
  remain denied. Ordinary containers keep their existing network defaults.
- Package the integrity-checked Chromium seccomp profile and its Apache-2.0
  license, and update package license metadata.
- Extend policy and Compose-provider regression coverage. Add opt-in container
  tests for Chromium's sandbox, software WebGL rendering, PNG screenshots, and
  isolation of unselected host-loopback ports, with a self-contained minimal
  browser fixture and usage documentation.

## v0.1.0

Initial alpha release.

- Lightweight wrappers for rootless Podman, Compose, and DevPod.
- Guardrails against unsafe host access and privilege expansion.
- Read-only project configuration and per-project SSH credentials.
