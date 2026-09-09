# Remaining work

The refactoring is complete.
This backlog covers release readiness, additional policy tests, and compatibility.
Implemented behavior belongs in the [README](README.md), [code map](docs/architecture.md),
and [changelog](CHANGELOG.md).

## Policy and test coverage

- [x] Validate bare additional-context paths and require explicit image transports.
- [x] Close descendant ignore exceptions, honor Dockerfile-specific rules, and
  check sensitive-tree names when exceptions exist; verified with Podman 6.1.1.
- [ ] Complete table-driven positive, negative, malformed, and reordered cases
  for every supported argument form; denied inputs must never reach the provider.
- [ ] Expand mount coverage for submodules, worktrees, multiple-project parents,
  separate file/subdirectory binds, sensitive runtime paths, sockets, symlinks,
  missing sources, delimiter characters, and unusual regular-file layouts.
- [ ] Review the accepted filesystem races between validation and execution;
  mitigate them where practical and keep residual risks explicit.
- [ ] Extend synthetic-secret redaction coverage across stdout, stderr, provider
  failures, debug output, and CI logs.
- [ ] Add remaining EOF and signal-propagation cases; preserve exact argv,
  provider exit status, and stdout/stderr separation across launchers.
- [ ] Expand lifecycle tests for broken launcher/metadata symlinks and
  interruption at operating-system signal boundaries.
- [ ] Add property or fuzz tests beyond deterministic unsafe-option insertion.
- [ ] Establish a useful coverage threshold for parser and policy code.

## Runtime and provider compatibility

- [ ] Verify every supported option against the declared Podman/Compose version
  matrix. Keep Docker Compose v2 and unreviewed provider series unsupported.
- [ ] Add a disposable rootless CI matrix with a digest-pinned local test image,
  no personal containers or credentials, and no host engine socket.
- [ ] Verify the complete workflow in a clean VM: installation, PATH precedence,
  run/create, aliases, Compose review and denial, changed-payload update, status,
  and removal of all recorded releases. Automated wheel update and rollback
  checks already pass; this item is the separate manual end-to-end check.
- [ ] Run every documented example with a clean test user and local test inputs.

## DevPod compatibility

- [x] Keep DevPod 0.6.15 as the tested version without rejecting other versions during installation or use.

Codium IDE-only opening/reconnect and project-key terminal access were verified
locally. Full IDE/version coverage and recovery remain separate checks.

- [ ] Verify a full Compose-driver workspace and stable workspace/context mapping
  through creation, stop/start, recreation, and reconnection.
- [ ] Verify other IDEs and their generated OpenSSH IdentityAgent/ProxyCommand
  behavior, including reconnecting to an existing IDE server.
- [ ] Verify project-key setup when multiple synthetic identities are loaded in
  the ambient agent: expose only the selected identity, keep private key files
  outside the workspace, and preserve the selected socket through `up` and SSH.
- [ ] Verify forge-enforced deploy-key scope with disposable repositories:
  intended repository access, denial elsewhere, and the chosen read/write limits.
- [ ] Verify agent recovery after expiry and host-session restart, including
  the eight-hour identity lifetime and interactive encrypted-key reload.
- [ ] Verify IDE/SSH failure messages when setup is missing or a key needs
  refreshing, without reading protocol stdin or using the ambient agent.
- [ ] Verify long-lived tunnel shutdown, signals, concurrent sessions, disconnect,
  mode changes, and revocation. Investigate the late proxy-tunnel diagnostic.
- [ ] Verify IDE-only absence of SSH-agent, HTTPS Git, and registry credentials
  across the remaining workspace/IDE combinations.
- [ ] Verify manual uninstall/reinstall restores DevPod SSH entries, preserves
  project-key selection, and safely recreates containers with new provenance.
- [ ] Deferred: revisit Codium project-key support after an upstream fix or a
  supported native integration; see [DEV-001](KNOWN_ISSUES.md#dev-001-vscode-loses-the-project-ssh-agent-socket).
  Maintaining or distributing a patched IDE extension is outside this project.

## CLI improvements

- [ ] Add an optional periodic CVE check with cached advisories and deduplicated warnings; account for distribution fixes, keep launches independent of network access, and never update providers automatically.
- [x] Confirm outside-project Compose binds with full paths and source lines; allow editing Dockerfile and Compose files while keeping Dev Container configuration read-only.
- [x] Support explicit container cleanup with `rm -f`, ignore, anonymous-volume removal, and timeout options.
- [ ] Preserve existing Compose project names and volume namespaces when enabling the wrapper; avoid fixed container-name collisions.
- [ ] Add a value-free `--explain` or dry-run mode for runtime policy decisions.
- [ ] Add a management CLI version command independent of provider versions.

## Publication

- [ ] Confirm GitHub-hosted CI, including CodeQL, after publication.
- [ ] Arrange an independent security review of parser bypasses and mount policy
  before expanding security or compatibility claims.
- [ ] Review remaining limitations before each experimental snapshot.
- [x] Prepare release notes, the supported-version matrix, and artifact checksums for v0.1.0.
- [x] Use v0.1.0 as the installation baseline; remove prerelease migration code and its archived fixtures.
- [ ] Enable private vulnerability reporting before publication.
- [ ] The project owner performs Git, tagging, and release publication operations.
