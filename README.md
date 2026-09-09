[en English](README.md) · [ru Русский](i18n/README.ru.md) · [es Español](i18n/README.es.md) · [pl Polski](i18n/README.pl.md) · [uk Українська](i18n/README.uk.md) · [de Deutsch](i18n/README.de.md) · [fr Français](i18n/README.fr.md) · [zh-CN 简体中文](i18n/README.zh-CN.md) · [ar العربية](i18n/README.ar.md) · [he עברית](i18n/README.he.md)

# paranoid-podman

`paranoid-podman` is an experimental user-level wrapper around rootless Podman
and `podman-compose`. It is intended for developers who sometimes run
container commands from projects they do not fully trust.

The project adds simple, direct guardrails against common container escape and
host-access mistakes while preserving ordinary local development workflows.
It is not an antivirus, an enterprise security product, or a complete sandbox.

> [!WARNING]
> This is defense in depth, not absolute protection. It may not stop zero-day
> exploits, unknown attack techniques, kernel or runtime vulnerabilities, or a
> process that already has access to your host user account.

## Contents

- [About](#about)
- [What it protects](#what-it-protects)
- [Install](#install)
- [Usage](#usage)
- [DevPod](#devpod)
- [Compatibility](#compatibility)
- [Limitations](#limitations)
- [Development](#development)
- [Security](#security)
- [License](#license)

## About

The command flow is:

```text
podman / compose command -> parser -> policy -> verified real provider
```

Safe arguments are preserved. Dangerous host access is rejected, selected
hardening defaults are added, and protected project files are mounted read-only
inside new containers.

The design is deliberately straightforward:

- block the most direct privilege and host-access paths;
- keep normal development working when it stays inside the project boundary;
- prefer explicit, understandable mount restrictions over content scanners;
- fail closed when a security-relevant command form is unknown; and
- document the remaining boundary honestly.

## What it protects

| Input or behavior | Action |
| --- | --- |
| Privileged mode, host or joined namespaces, added capabilities, devices, arbitrary security options | Deny |
| Podman/Docker engine sockets and sensitive host runtime paths | Deny |
| The filesystem root, the complete user home, broad system trees, or a bind broader than the project | Deny |
| Explicit individual regular files and directories | Allow and canonicalize |
| Host ownership or label-changing bind options such as `U`, `idmap`, or unsafe relabeling | Deny |
| Published ports | Preserve normal TCP/UDP mappings, including wildcard and LAN addresses |
| Ambient credentials, desktop/session endpoints, and provider-routing variables | Remove or deny |
| Existing protected project configuration under writable binds | Add read-only submounts |
| `start`, `exec`, or active Compose lifecycle on an old or foreign container | Deny until it is recreated by the current installation and policy |
| Compose configuration | Render, validate, rewrite, and execute a private reviewed snapshot |
| Obvious literal secrets in Compose or Dockerfile assignments | Reject with values hidden |
| Root-level build-context credentials not covered by an ignore file | Deny before the builder runs |

Direct `run` and `create` commands also receive compatible defaults such as
`no-new-privileges`, a PID limit, disabled automatic proxy forwarding, a
non-persistent restart policy, and no implicit image pull. Unknown runtime
options are not passed through.

### Protected project configuration

Discovered paths matching the following names are read-only inside newly
created containers:

- `.devcontainer`, `.devcontainer.json`, `devcontainer.json`;
- `.dockerignore`, `.containerignore`;
- live dotenv files and common credential configuration files; and
- `.git`, `.gitmodules`, and `.git-credentials`.

Missing paths are not created. Devcontainer contents are not scanned or
rewritten: this protection is a filesystem mount rule. Dockerfiles receive
only the small literal-secret check described below.

Dockerfile, Containerfile, and Compose files outside `.devcontainer` remain
editable inside the container. Everything inside `.devcontainer` stays read-only.
Recreate existing containers to apply changed mount permissions.

Unreadable directories owned by another UID, such as rootless database data,
are mounted without scanning their contents or changing their permissions.
Files inside those directories are outside the automatic read-only protection.

`.git` is read-only by default. It can be left writable for one invocation
without weakening the other protected paths:

```bash
PODMAN_GUARD_PROTECT_GIT=0 podman compose up
```

### Compose review

The Compose adapter:

1. preflights the source files and renders the resolved configuration;
2. validates host access, namespaces, mounts, resources, and supported fields;
3. writes a private mode-`0600` snapshot with interpolation already resolved; and
4. runs only that reviewed snapshot.

Project-local mounts pass silently. Before `up` or `run` grants access to a host
file or directory outside the project, an orange `[warning]` shows its full path,
service, access mode, and source file with line number. Type exactly `y` and Enter
to allow the listed mounts for that command. Enter, another answer, or EOF cancels;
without terminal input, the operation is blocked without reading stdin. The choice
is not saved. Inspection, cleanup, and commands on existing containers do not prompt.
System paths, credentials, sockets, and other denied mounts cannot be approved.
`PODMAN_GUARD_DEBUG=1` enables a redacted summary and source locations.

Compose diagnostics do not print resolved environment values or raw provider
errors. The literal-secret check is intentionally small and name-based; it is
not a general secret scanner.

New direct and Compose containers receive two reserved provenance labels: the
policy generation and a random identifier created for the local installation.
Operations that can start, resume, or execute code require both labels to
match before using an existing container. This prevents an image from passing
the check merely by declaring the public policy label itself. Read-only
inspection and cleanup remain available for old containers so they can be
diagnosed and removed safely.

For the exact interfaces, see
[Direct Podman policy](docs/direct-policy.md),
[Compose policy](docs/compose-policy.md), and the
[threat model](docs/threat-model.md).

## Install

You need Linux with rootless Podman 6.1.x, `podman-compose` 1.6.x, Python 3.10
or newer with virtual-environment support, and Bash. If you use DevPod, install
DevPod and the OpenSSH client tools too. DevPod 0.6.15 is the tested version;
other versions are accepted.

From this repository, run as your normal user:

```bash
./install.sh install
```

Or run `./install.sh` without arguments to see the current status and a menu:
`install` for a new installation, or `update` / `uninstall` for an existing one.
Press Enter to exit without changes. Without an interactive terminal, it only
displays status and available actions; scripts should specify an action explicitly.

The installer finds Podman, Compose, and DevPod if present. It downloads Python
setup tools and dependencies, builds this checkout, and installs the application
in a private environment. You do not need to prepare wheels, checksums, or Python
environments yourself. Existing command files are backed up only after you
confirm; uninstall restores them.

To preview without downloads or changes:

```bash
./install.sh install --dry-run
```

### Command locations

The default command directory is `${XDG_BIN_HOME:-$HOME/.local/bin}`. Put it
first on `PATH` so your shell uses the guarded commands:

```bash
export PATH="${XDG_BIN_HOME:-$HOME/.local/bin}:$PATH"
command -v podman docker compose-guard
```

Add the export line to your shell configuration if this directory is not
already first. The private application lives in
`${XDG_DATA_HOME:-$HOME/.local/share}/paranoid-podman`.

### Update or uninstall

Run from the checkout containing the version you want to install:

```bash
./install.sh update
./install.sh status
```

Remove the installation and restore saved commands:

```bash
./install.sh uninstall
```

For a status check and an `uninstall` / `Exit` menu, run `./uninstall.sh`.
Enter exits without changes; without an interactive terminal it only displays
information. Use `./uninstall.sh --help` for removal options, or
`./uninstall.sh --dry-run` to preview removal after selecting it in the menu.

`--dry-run` also works with update and uninstall. Modified installation files
are reported rather than deleted.
To add DevPod to an installation that did not include it, uninstall and install
again after DevPod is available; existing guarded containers then need recreation.

### Optional settings

`./install.sh --help` describes every option and groups them by action.
Provider and wheel options apply to `install` / `update`; `--without-devpod`
and `--backup-existing` apply only to `install`, and `--devpod-ssh-config` only
to `uninstall`. All options are optional for a normal installation.

Use `--without-devpod` to install only Podman and Compose guards, or `--devpod PATH`
for a DevPod executable outside `PATH`. `--podman` and `--compose-provider` override
automatic discovery. `--backup-existing` allows backups without an interactive
confirmation. Use matching `--bindir` and `--libdir` for custom install locations.

SSH configs in the default DevPod home are discovered and restored automatically.
For contexts under another `DEVPOD_HOME`, pass each custom SSH-config path:

```bash
./install.sh uninstall --devpod-ssh-config /absolute/path/to/ssh-config
```

For a prepared offline installation, see the [advanced wheel guide](docs/wheel-packaging.md).
Those packaging options are optional; the normal install command handles preparation.

## Usage

Run commands from an existing project directory. Direct runs use
`--pull=never`, so choose an image already in local storage. In this example,
replace `localhost/my-dev-image:latest` with that image. The Compose commands
require a Compose file and a service named `app`:

```bash
podman run --rm -v .:/workspace localhost/my-dev-image:latest
podman compose up
podman compose ps
podman compose exec app sh
podman compose run --rm --build app
podman compose down
```

`docker`, `podman-compose`, and `docker-compose` aliases are installed for
compatible workflows. Commands outside the reviewed interface are denied; use
the real Podman binary deliberately for host administration.

Direct Podman and Compose rejections exit with code 125. A highlighted `BLOCKED`,
`UNSUPPORTED`, or `ERROR` heading distinguishes policy, compatibility, and input
failures, followed by the reason and a value-free `next step:` line.
Compose identifies the affected service when available. Build-context errors list all detected paths at
once. Exclude live dotenv and common credential files from copied build inputs
with `.containerignore` or `.dockerignore`, or apply only that mechanical fix:

```bash
paranoid-podman build-context audit .
paranoid-podman build-context protect .
```

The second command offers a multi-selection in a terminal; `--all` is the
explicit non-interactive form. It never bypasses a policy denial. `.git` is
allowed in the build context and remains available in a runtime workspace,
where the guard protects it with a read-only submount by default.

Image pulls and builds also have narrowly reviewed command forms.

One-off Compose tasks support profiles, `--no-deps`, environment/user/workdir
overrides, and normal port options. `run --build` stops if the build fails.
Recreation with `up --force-recreate` and an explicit data reset with
`down --volumes --remove-orphans` are supported; the latter deletes project volumes.
See the [Compose policy](docs/compose-policy.md) for supported options and provider limits.

## DevPod

The wrapper handles reviewed Docker and Compose driver commands from DevPod 0.6.15,
including:

- container discovery, inspect, start, stop, logs, exec, and removal;
- image inspect, pull, build, tag, and push;
- the private `/etc/passwd` and `/etc/group` setup copy flow;
- Compose project lookup, project names, project `.env`, and generated
  override files; and
- Compose build, up, stop, and down operations.

New DevPod containers use the workspace name as their hostname, making shell
prompts readable. An explicit hostname is preserved; names requiring shortening
or normalization receive a short hash suffix. Existing containers need recreation
to acquire the new hostname.

DevPod does not bypass the common creation policy. A workspace requesting
privileged mode, host namespaces, engine sockets, dangerous capabilities, or a
broad host mount is still denied.

Read-only submounts and provenance labels apply when a container is created.
After installing this mechanism, recreate an older DevPod workspace before
starting or entering it through the wrapper. The same applies after a fresh
reinstall, because it receives a new installation identifier. Cleanup commands
remain available. To keep `.git` writable, start the DevPod process with
`PODMAN_GUARD_PROTECT_GIT=0`.

### SSH credential isolation

IDE selection stays with DevPod: use its default/configured IDE or its `--ide`
option. The wrapper does not require a particular editor. Manual IDE-only
opening and reconnecting were tested with Codium; other IDE combinations and
the full Compose-driver workspace still need acceptance checks. A project-agent
socket lifetime failure was observed with Codium and Open Remote - SSH 0.1.2;
see [DEV-001](KNOWN_ISSUES.md#dev-001-vscode-loses-the-project-ssh-agent-socket).
Fresh OpenSSH and direct `devpod ssh` sessions were verified with a project key.

When DevPod is included in the installation, the user-level `devpod` command also protects
the SSH paths used by `up`, `build`, direct `devpod ssh`, generated
`WORKSPACE.devpod` hosts, and IDE launches. DevPod's own workspace transport
key keeps IDE and terminal access working; it is separate from Git credentials.

On the first interactive guarded `up` or `ssh`, choose one of these options:

1. create a new project-only Ed25519 key;
2. keep IDE/SSH access but expose no host Git, SSH, or registry credentials;
3. select an existing project-only key outside the root of `~/.ssh`;
4. expose the full ambient host agent for this run only, with a `DANGER`
   confirmation that is never remembered; or
5. cancel without running DevPod.

Pressing Enter selects option 1. For IDE access without host Git credentials,
choose **2 — IDE only** explicitly.

Project-key mode starts a dedicated user `ssh-agent`, loads exactly one
verified identity, and points only the DevPod-managed OpenSSH block at its
socket. The private key file is never mounted or copied into the container.
Register the public key as a repository-scoped deploy key and grant write
access only when pushes are required.

The socket is still a credential capability: code in the workspace can ask
that one agent to sign data while it is available. Isolation reduces the blast
radius; it does not make agent forwarding harmless.

Protected modes disable automatic private-key discovery, HTTPS Git and registry
credential injection, GPG forwarding, and SSH signing-key forwarding in the
selected DevPod context. These context settings also affect its other workspaces.
The wrapper protects DevPod's generated SSH block before opening the IDE.
An unconfigured standalone `devpod build` proceeds without a prompt and without
forwarding host credentials. The [credential guide](docs/devpod-credentials.md)
explains the transport and saved state.

Inspect or configure an existing workspace before using its old
`WORKSPACE.devpod` SSH entry:

```bash
paranoid-podman devpod audit WORKSPACE
paranoid-podman devpod configure WORKSPACE
paranoid-podman devpod key show WORKSPACE
paranoid-podman devpod key stop WORKSPACE
```

Configuration is inferred from the DevPod-owned SSH block and the saved key
under `~/.ssh/paranoid-podman/`; no separate policy configuration file is
created. Replace `WORKSPACE` with an existing workspace ID. Use matching
`--context`, `--devpod-home`, and `--ssh-config` options when needed.

For a new local project, pass an existing directory to `devpod up`; a nonexistent
path may be interpreted as a repository URL. Select IDE-only mode during that
first interactive `up`: it cannot be saved with `configure` until DevPod has
created the workspace's SSH block. See the [local workspace example](docs/devpod-credentials.md#open-a-local-workspace).

This integration was reviewed against the
[DevPod 0.6.15 context options](https://github.com/loft-sh/devpod/blob/v0.6.15/pkg/config/context.go),
[generated SSH configuration](https://github.com/loft-sh/devpod/blob/v0.6.15/pkg/ssh/config.go),
and [workspace startup order](https://github.com/loft-sh/devpod/blob/v0.6.15/cmd/up.go).
See [DevPod credential isolation](docs/devpod-credentials.md) for the full
behavior and remaining acceptance gates.

## Compatibility

| Component | Compatibility |
| --- | --- |
| Operating system | Linux |
| Python | 3.10 and newer |
| Podman | Rootless 6.1.x |
| Compose provider | `podman-compose` 1.6.x through `podman compose` |
| DevPod | Tested with 0.6.15; other versions accepted; reviewed Docker/Compose command forms and IDE limits above |
| Docker Compose v2 | Not supported |

The installer rejects an unreviewed Podman or Compose major/minor series.
DevPod versions are not restricted to 0.6.15; compatibility with other versions
has not been verified. Credential and command checks still apply.

## Limitations

- A wrapper on `PATH` is not a sandbox. A host process running as your user can
  invoke the real Podman binary or access the same files directly.
- The wrapper cannot protect against kernel, Podman, OCI runtime, image, parser,
  or zero-day vulnerabilities.
- Ordinary container networking is not an outbound network sandbox.
- A forwarded SSH agent can use its loaded identity without exposing the
  private-key bytes. Project-key mode limits the available identity; repository
  scope must still be enforced by the Git forge.
- Builds can execute arbitrary image instructions and read every context path
  not excluded by `.containerignore` or `.dockerignore`. The guard checks only
  common root-level credential paths and obvious literal assignments. Its
  ignore check requires complete exclusion of sensitive root paths: descendant
  exceptions or unreviewed patterns may require a final explicit exclusion.
  Additional image contexts require an explicit transport prefix. See the
  [build limits](docs/direct-policy.md#build-boundary).
- Previously created or already running containers do not gain new mount
  protections. Active guarded lifecycle commands reject containers without the
  current policy and installation provenance.
- Provenance labels are local compatibility markers, not cryptographic
  signatures. A process already running as the host user can read, copy, or
  bypass them. Running guards directly from the source tree without an explicit
  installation ID uses a deterministic development fallback; use the installer
  for installation-specific provenance.
- Files can change between validation and provider execution.
- The supported interface is intentionally smaller than the complete Podman and
  Compose CLIs.

Use a disposable VM or a separate low-privilege account when stronger isolation
is required.

## Development

Fast local checks:

```bash
scripts/test.sh
scripts/check.sh syntax
```

The local tests use fake providers and temporary home/config directories;
inherited integration opt-ins are disabled. `scripts/check.sh` defaults to
`local`, adding Ruff, strict mypy, Bandit, ShellCheck, offline `zizmor`, and a redacted
Gitleaks source scan. `scripts/audit.sh dependencies` explicitly runs the network
audit; `scripts/check.sh all` adds that audit but still excludes real integrations.
`scripts/format.sh` applies formatting. Prepare validation tools separately as
described in [CONTRIBUTING.md](CONTRIBUTING.md). CI separates tests, static
checks, secrets, and dependency auditing.

Implementation lives under `src/paranoid_podman`. See the
[code map](docs/architecture.md) and [wheel build and offline installation instructions](docs/wheel-packaging.md).

| Directory | Purpose |
| --- | --- |
| `src/paranoid_podman/` | Application code |
| `bin/` | Thin launchers for running the checkout and tests |
| `dist/` | Generated wheel and checksum for installation |

Installed commands are generated separately in the selected `--bindir` and run
the private wheel environment. Keep `bin/` in the source tree; `dist/` is build output.

`scripts/test.sh compose-provider` checks daily workflows with the real Compose
provider and a recording engine. It also reparses a snapshot using the configured
Podman; that check may query Podman but does not start containers. Use a disposable
environment with the reviewed provider versions. CI runs the scenario tests too.

The opt-in runtime suite requires an existing local image with `sh` and
`sleep`. It never pulls an image and uses only uniquely named disposable
containers:

```bash
PARANOID_PODMAN_TEST_IMAGE=docker.io/library/alpine:latest \
  scripts/test.sh rootless
```

Run it only with the reviewed Podman and Compose versions in a disposable
rootless account without valuable containers or credentials.

The real-agent test creates an ephemeral key under its temporary directory and
a dedicated agent socket. It does not read normal SSH keys:

```bash
scripts/test.sh ssh-agent
```

See [TODO.md](TODO.md) for remaining runtime and compatibility work and
[CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidance.

## Security

Report suspected vulnerabilities using the process in
[SECURITY.md](SECURITY.md). Do not publish live credentials, private project
data, or exploit details in a public issue.

## License

[MIT](LICENSE)
