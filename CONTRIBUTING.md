# Contributing

Contributions are welcome, especially small changes accompanied by focused
negative tests. Read [docs/threat-model.md](docs/threat-model.md) and the direct
or Compose policy document before changing an allowlist or rewrite.

## Local checks

Prepare a user-owned validation environment explicitly. This setup can download
packages; none of the check scripts installs tools. Keep Git hooks disabled and
unused.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --requirement requirements.txt --requirement requirements-dev.txt
```

Make the pinned CLI tools available on `PATH` separately: ShellCheck 0.11.0 and
[Gitleaks 8.30.1](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1).
CI verifies their release archives with SHA-256 before installation. They are
not Python dependencies.

Once those tools are available, run `scripts/check.sh local`. For only the
fake-provider tests and built-in syntax checks, use `scripts/test.sh` and
`scripts/check.sh syntax`.

| Command | Scope |
| --- | --- |
| `scripts/test.sh` | Local unittest discovery with integration flags disabled, temporary home/config/runtime directories, and no inherited credentials; prepared-wheel cases run separately |
| `scripts/check.sh syntax` | Compile all Python sources and four Python launchers without bytecode writes; check each Bash file and generated launcher separately |
| `scripts/lint.sh` | Syntax, ShellCheck, Ruff check/format check, strict application-wide mypy, generated requirements, Bandit, and offline zizmor; no tests |
| `scripts/audit.sh secrets` | Redacted Gitleaks directory scan of the prepared publishable source tree; no Git history |
| `scripts/check.sh local` | Tests, lint, and secrets; this is also the default with no argument |
| `scripts/audit.sh dependencies` | Explicit network audit of runtime and validation requirements in separate resolutions |
| `scripts/check.sh all` | Local checks plus dependency auditing; excludes real integrations |
| `scripts/format.sh` | Explicit Ruff formatting writes |
| `scripts/build.sh` | Wheel and SHA-256 using a separately prepared build environment; no checks or installations |

`scripts/test.sh` clears inherited environment settings before discovery, even
when a real-integration opt-in is set in the calling shell. Tests use fake
providers and temporary installations. Home-path denial cases consult OS account
metadata, as the guard does; they do not read personal files. New nested test
directories need `__init__.py` for Python 3.10 discovery.

The CI wheel job runs all installation scenarios against prepared artifacts using
`python3 -B -m tests.lifecycle.test_wheel`; see the
[wheel instructions](docs/wheel-packaging.md) for its two required inputs.
It uses real offline pip/venv and fake providers, including the original lifecycle
cases for backups, rollback, and SSH restoration. Local discovery reports those
artifact-dependent classes as skipped rather than building or installing tools.
The same CI job tests plain `./install.sh install` and update with
`tests.lifecycle.test_bootstrap`, using a prepared wheel directory and disabled
network access. Local tests always set `PIP_NO_INDEX=1`.

The publishable scan includes named root documents/configuration and source
files under `bin`, `src`, `tools`, `scripts`, `tests`, `docs`, `i18n`,
and `.github`. Its inventory lives in `scripts/validation.py`. Private root
notes, hidden subtrees, caches, and symlinks are excluded. Synthetic test
fixtures remain scanned; there are no blanket test allowlists.
Add new public file formats to the inventory
when introducing them.

Dependency auditing can query package indexes and vulnerability services. The
current requirements contain ranges, so the results describe the scanner's
resolutions, not an exact installed release inventory. Wheel locks and separate
build/installer/runtime environments are described in
[docs/wheel-packaging.md](docs/wheel-packaging.md). Mypy checks the entire application in strict mode, including the dispatcher.
PyYAML stubs belong only to the validation environment. Ruff owns formatting
and lint rules; local Python LSP, Rope, and Pylint can assist development without
becoming runtime dependencies. CI runs tests on Python
3.10/3.14 and uses separate static, secrets, and network-audit jobs, without
repeating tests in the static job.

Implementation owners and import boundaries are mapped in
[docs/architecture.md](docs/architecture.md).

## Policy changes

Put parsing, policy, and process changes in the owners listed in the code map.
Choose an explicit `ViolationCategory` at each rejection; keep diagnostics free
of input values and preserve categories when adding source locations. Shared
fixtures belong in `tests/support`, outside collected test classes.

Every new accepted command, option, Compose field, provider version, or escape
hatch needs:

- an explicit security rationale;
- positive, negative, malformed-input, and ordering tests;
- an assertion that denied values never reach the fake provider;
- output tests using synthetic secret markers; and
- matching updates to the policy documentation.

Do not weaken critical protections through project-local configuration. Use
fictional workspace names such as `example-workspace` and fake image references
such as `example.invalid/image` in unit tests. Never put personal project names,
real credentials, private paths, or executable malicious fixtures in the test suite.

## Translations

The root [README.md](README.md) is the canonical policy summary. When behavior,
compatibility, commands, or security limitations change, update every translated
README under `i18n/` in the same change. Keep the language selector complete and
in the same order in all README files.

## Runtime testing

Compose fake-provider tests explicitly close stdin unless a test supplies a
PTY input. They must behave the same from a terminal and from CI;
capturing stdout/stderr alone does not make a subprocess non-interactive.
Subprocess waits are bounded so a missing response cannot silently hang the
test suite. A regression test runs a non-interactive build test under a parent
PTY without sending input. Ordinary Compose validation must never prompt or
consume the container's stdin.

Fake-provider tests must run before any real Podman test. Run real containers
only in a disposable rootless environment without personal containers, images,
credentials, or a host container-engine socket. Use a standard small image such
as `docker.io/library/alpine:latest`, already present in that environment. The
image must provide `sh` and `sleep`; the suite never pulls one:

```sh
PARANOID_PODMAN_TEST_IMAGE=docker.io/library/alpine:latest \
  scripts/test.sh rootless
```

The suite invokes the guard with a closed standard input so Compose exercises
the fully reviewed non-interactive path used by automation such as DevPod. It
uses unique resource names and removes only containers it created. Do not
extend it with broad host mounts, personal paths, or shared resource names.

The opt-in SSH-agent test creates its own temporary identity and never reads
normal user keys:

```sh
scripts/test.sh ssh-agent
```

Real DevPod and IDE acceptance uses the installed wrapper in a disposable
workspace. In IDE-only mode, verify a non-root terminal in the workspace and
an unset `SSH_AUTH_SOCK` after both initial opening and reconnecting. In
project-key mode, verify exactly the expected fingerprint through a live SSH
session. See [credential modes](docs/devpod-credentials.md) and the remaining
compatibility gates in [TODO.md](TODO.md).

To reproduce the IDE agent-socket lifetime problem without a container, run:

```sh
scripts/test.sh devpod-ssh
```

This opt-in suite requires OpenSSH tools and a real DevPod binary at
`/usr/bin/devpod-cli` (override with `PARANOID_PODMAN_TEST_DEVPOD`). It was tested
with DevPod 0.6.15. It starts
temporary local stdio SSH helpers and an agent with one generated disposable
key. It uses no containers, TCP listeners, installed IDE, or personal SSH keys.
It reproduces the deleted setup socket and tests holding the setup channel open,
including independent sessions, disconnect, and reconnect. These protocol tests
do not establish that an IDE integration works.

The configured real Compose provider has a separate compatibility gate:

```sh
scripts/test.sh compose-provider
```

It runs daily CLI scenarios through the real provider and a recording engine:
build, recreation, one-off tasks, stdin, exec, ports, resource limits, logs,
status, and explicit volume cleanup. A failed one-off build must not launch a
task. This matrix runs in CI on Python 3.10 and 3.14 without starting containers.
The separate snapshot reparse test may query real Podman during initialization;
use a disposable environment with the reviewed provider versions. Direct unittest invocation also requires
`PARANOID_PODMAN_RUN_COMPOSE_PROVIDER_TESTS=1`; the integration runner sets it
only when this mode is explicitly selected. Other direct opt-in flags remain
supported. None of these integration modes is selected by `check.sh all`.
