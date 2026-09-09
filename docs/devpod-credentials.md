# DevPod credential isolation

This document describes the optional DevPod integration, tested with 0.6.15. It narrows SSH
credentials available to development workspaces without replacing DevPod's own
workspace transport or the Podman creation policy.

DevPod selects the IDE using its normal defaults, workspace settings, or `--ide`.
The wrapper protects that connection flow without choosing an editor for you.

## Contents

- [Why this exists](#why-this-exists)
- [Reviewed DevPod behavior](#reviewed-devpod-behavior)
- [Access modes](#access-modes)
- [Project keys and agents](#project-keys-and-agents)
- [Commands](#commands)
- [Open a local workspace](#open-a-local-workspace)
- [Codium agent socket troubleshooting](#codium-agent-socket-troubleshooting)
- [Persistent and runtime state](#persistent-and-runtime-state)
- [Limitations](#limitations)

## Why this exists

An SSH agent does not reveal private-key bytes, but any process that can reach
its socket can request signatures. Forwarding a normal host agent can therefore
give workspace code the effective use of every loaded identity.

The integration offers either no Git credential socket or a dedicated agent
containing one project identity. Repository scope is enforced by registering
that public key as a deploy key at the Git forge.

## Reviewed DevPod behavior

DevPod 0.6.15 is the tested version. Other versions are accepted, with the same
credential and command checks, but their compatibility has not been verified.
Upstream source review and compatibility tests of 0.6.15 established that:

- DevPod's host-to-workspace transport key is separate from Git identities;
- `SSH_ADD_PRIVATE_KEYS=false` stops automatic key-file discovery but does not
  hide identities already loaded in an ambient agent;
- direct `devpod ssh` consults the context forwarding setting; and
- generated `WORKSPACE.devpod` blocks contain `ForwardAgent yes` and a
  `ProxyCommand` pointing at the running DevPod executable.

The wrapper therefore controls both the child process environment and the
marker-delimited OpenSSH block. DevPod writes that block before it opens an IDE.
The wrapper prevents the resulting race by forcing the creation phase to use
`--open-ide=false`, protecting the block, and then opening the IDE through a
second non-recreate `up` with `--configure-ssh=false`. The second process also
receives only the selected protected environment.

Upstream references:

- [context options](https://github.com/loft-sh/devpod/blob/v0.6.15/pkg/config/context.go)
- [generated SSH configuration](https://github.com/loft-sh/devpod/blob/v0.6.15/pkg/ssh/config.go)
- [`up` startup order](https://github.com/loft-sh/devpod/blob/v0.6.15/cmd/up.go)
- [`ssh` forwarding behavior](https://github.com/loft-sh/devpod/blob/v0.6.15/cmd/ssh.go)
- [pflag command-line syntax](https://github.com/spf13/pflag#command-line-flag-syntax)

Boolean options follow DevPod's syntax: use `--flag=false` to disable one.
`--flag false` enables the flag and leaves `false` as a positional argument.
The wrapper respects `--` and does not interpret string-option values as flags.

## Access modes

### Project key

Create an Ed25519 key or select an existing project-only private key. The
wrapper starts a dedicated agent, loads exactly that identity, verifies its
SHA-256 public-key fingerprint, and supplies only that socket to DevPod.

This works in the verified terminal SSH paths. IDE forwarding also depends on
the client's agent-socket lifecycle. The observed Codium/Open Remote - SSH 0.1.2
failure is tracked in [DEV-001](../KNOWN_ISSUES.md#dev-001-vscode-loses-the-project-ssh-agent-socket);
it has not been established for every IDE.

### IDE only

The wrapper removes `SSH_AUTH_SOCK` and `SSH_AGENT_PID`, writes
`ForwardAgent no` and `IdentityAgent none`, and leaves DevPod's separate
workspace transport intact. IDE and terminal access should remain available;
Git operations that need host SSH or HTTPS credentials may fail.

### Unsafe ambient agent

The normal host agent is available for one invocation only after an exact
interactive confirmation. The choice is never stored and a `DANGER` message is
shown again next time. A non-interactive unconfigured connection fails closed.

### Cancel

DevPod is not executed and the wrapper exits with policy code 125.
Closing interactive input (Ctrl-D) also cancels; it never selects a default or
starts key generation.

## Project keys and agents

Generated and selected identities are represented under:

```text
~/.ssh/paranoid-podman/<context>/<workspace>/
```

Components include a short hash to avoid collisions. Generated private keys
stay there; a manually selected private key is referenced by a symlink and is
never copied. A manual key directly under `~/.ssh`, such as
`~/.ssh/id_ed25519`, is rejected because it is normally an account-wide
identity rather than a project deploy key.

Agent sockets and PID files live under:

```text
$XDG_RUNTIME_DIR/paranoid-podman/ssh-agent/
```

Directories and private files must be owned by the current user and private.
The wrapper invokes `ssh-keygen`, `ssh-agent`, and `ssh-add` with argument
arrays and never evaluates their output as shell code. Agent identities have
an eight-hour lifetime and are reloaded only by a later guarded invocation.
Agent replacement and SSH-config updates use private cross-process runtime
locks. Before signaling a recorded PID, the wrapper also checks its owner,
process name, and exact `-a` socket argument.

A ProxyCommand or any other non-interactive invocation never asks for a
passphrase or reads protocol/automation stdin. If an encrypted key must be
reloaded after its agent expires, the connection fails with the exact
interactive `paranoid-podman devpod configure WORKSPACE` command.
Internal context-option updates are captured so their output cannot corrupt SSH
protocol stdout. This also applies to attached flags such as `--stdio=true`.

After key creation, register the public key with the repository and prefer
read-only access unless pushes are required:

- [GitHub deploy keys](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys)
- [GitLab deploy keys](https://docs.gitlab.com/user/project/deploy_keys/)
- [Gitea repository deploy-key API](https://docs.gitea.com/api/next/operations/repo-create-key/)

## Commands

Install DevPod and OpenSSH client tools, then run from this repository:

```bash
./install.sh install
```

The installer discovers `devpod` or `devpod-cli` and includes the wrapper
automatically. Use `--devpod /absolute/path/to/devpod` only when its executable
is outside `PATH`. `--without-devpod` disables this integration. See
[installation](../README.md#install) for prerequisites and PATH setup.

If paranoid-podman was installed without DevPod, uninstall it and install again
after DevPod is available. An update preserves the installed integration choices.
Uninstall restores saved command files; reinstalling creates a new installation
identity, so existing guarded containers need recreation.

Then use DevPod normally. `up`, `build`, and `ssh` are intercepted; reviewed
administrative and inspection subcommands pass through. Self-upgrade through
the wrapper is disabled to preserve saved command backups. Update DevPod using
its original installation method, then run `./install.sh update`.

An unconfigured `up` or interactive `ssh` asks for a credential mode. A
standalone `build` instead defaults to IDE-only isolation without prompting and
does not forward host credentials into a workspace. If a project key was
configured previously, the build reuses its dedicated one-key agent.

Explicit management commands are:

```bash
paranoid-podman devpod audit WORKSPACE
paranoid-podman devpod configure WORKSPACE
paranoid-podman devpod key show WORKSPACE
paranoid-podman devpod key stop WORKSPACE
```

Use `--context`, `--devpod-home`, or `--ssh-config` when the corresponding
DevPod invocation uses a non-default value. An explicit `--id` is recommended
when a new workspace source would not produce an obvious local workspace name.

For a new workspace without a DevPod-managed SSH block, select IDE-only mode
during the first interactive `devpod up`. Running `configure` beforehand can
apply the safe context baseline but cannot save that per-workspace choice yet;
it reports this explicitly and exits with code 125. A project-key selection can
be saved before the block exists because its key layout records the choice.

## Open a local workspace

Ensure the installed command directory is first on `PATH`; see
[installation](../README.md#command-locations). For a new DevPod Docker provider,
route it through the installed `docker` wrapper. With the default binary directory:

```bash
pp_bin="${XDG_BIN_HOME:-$HOME/.local/bin}"
devpod provider add docker --option "DOCKER_PATH=$pp_bin/docker"
```

Use your selected `--bindir` for `pp_bin` if it differs. An existing provider
should already point to that wrapper; inspect its configuration before changing it.

From an existing project directory with a devcontainer configuration and a
reviewed image, run:

```bash
devpod up "$PWD" --id my-project
```

`my-project` is the new workspace ID. Leave `--ide` unset for DevPod's normal
IDE selection, or supply that option to choose a different IDE. The local source
must exist; do not pass an example `/absolute/path/...` unchanged, because DevPod may treat it as a
repository URL. Choose **2 — IDE only** at the prompt. Enter selects project-key
mode instead. A new IDE-only choice must be made during this first `up`, after
which DevPod's SSH block records it.

In your IDE's workspace terminal, check the user, workspace directory, and agent:

```bash
id
pwd
test -z "${SSH_AUTH_SOCK:-}" && echo "PASS: no forwarded agent"
```

Expect a non-root user, the workspace directory, and the PASS message. Close
the IDE, reconnect from the host, and repeat the terminal check:

```bash
devpod up my-project
paranoid-podman devpod audit my-project
```

Keep `--context` and `--devpod-home` consistent when using non-default DevPod
state. A separate DevPod home does not automatically select a separate SSH config.

## Codium agent socket troubleshooting

Status: **Deferred**. See [DEV-001](../KNOWN_ISSUES.md#dev-001-vscode-loses-the-project-ssh-agent-socket)
for the supported alternatives and conditions for revisiting this issue.

A known compatibility failure affects the tested combination of DevPod 0.6.15
and Open Remote - SSH 0.1.2. Project-key forwarding worked through fresh
OpenSSH and direct `devpod ssh` connections, but Codium's integrated terminal
reported `Error connecting to agent: No such file or directory`, including
after the requested reconnect and new-terminal check.

DevPod creates `/tmp/auth-agent*/listener.sock` for an SSH session and removes
it when that session's command ends. Open Remote - SSH captures `SSH_AUTH_SOCK`
from its server-setup command and supplies the value to the extension host and
terminals after setup completes. This socket-lifetime mismatch explains why
the variable can be set while its socket no longer exists. See the
[DevPod session handler](https://github.com/loft-sh/devpod/blob/v0.6.15/pkg/ssh/server/ssh.go)
and [Open Remote - SSH setup](https://github.com/jeanp413/open-remote-ssh/blob/v0.1.2/src/serverSetup.ts).

Container recreation does not fix this lifetime mismatch. There is no verified
IDE workaround in the wrapper yet. Real-helper protocol tests verify that holding
the setup channel open preserves its agent socket. Desktop IDE acceptance remains
unverified; building or distributing a patched IDE extension is outside the
wrapper's scope.
For temporary terminal-only access, open
`devpod ssh WORKSPACE` on the host and run `ssh-add -l` inside that live session;
it must list exactly the selected project identity. The owner verified this
interactive terminal path on 2026-09-08. This does not repair the
IDE's Git integration. Do not copy private keys into the container, select an
arbitrary socket, or enable ambient-agent forwarding to work around the error.

## Persistent and runtime state

There is no separate paranoid-podman policy file. Durable mode is inferred
from the DevPod-managed SSH block and the project key layout. The wrapper sets
these DevPod context options for protected modes:

```text
SSH_ADD_PRIVATE_KEYS=false
SSH_AGENT_FORWARDING=true
SSH_INJECT_DOCKER_CREDENTIALS=false
SSH_INJECT_GIT_CREDENTIALS=false
GPG_AGENT_FORWARDING=false
GIT_SSH_SIGNATURE_FORWARDING=false
```

The forwarding option remains enabled only as a transport for the selected
socket. IDE-only mode passes no socket; project mode passes the verified
one-key socket. Context options are context-wide, so the safe automatic
credential-injection baseline affects every workspace in that DevPod context.
Changes are serialized and read back before workspace access. Direct DevPod CLI
flags cannot re-enable GPG-agent or automatic SSH signing-key forwarding in a
protected invocation.

Uninstall restores a backed-up `devpod` entry point and replaces wrapper-owned
DevPod `ProxyCommand` paths without changing unrelated hosts. It discovers the
default `~/.ssh/config` and custom `SSH_CONFIG_PATH` values from contexts in the
default DevPod home. If another `DEVPOD_HOME` was used, every custom path must
be provided explicitly with repeatable `--devpod-ssh-config PATH` options.

## Limitations

- A one-key agent can still sign requests with that key while it is running.
- The Git forge, not the wrapper, decides which repositories and operations a
  deploy key can access.
- Other processes running as the same host user can reach user-owned keys,
  agents, wrapper files, and the real DevPod binary.
- The owner verified Codium opening and reconnecting with no `SSH_AUTH_SOCK`
  in its integrated terminal in IDE-only mode on 2026-09-08. In project-key mode,
  fresh OpenSSH and direct DevPod connections exposed exactly the expected identity,
  but Codium's integrated terminal reported a missing agent socket. The
  [socket-lifetime failure](#codium-agent-socket-troubleshooting) remains
  unresolved. Other IDEs and OpenSSH reconnection after a reboot
  also remain manual release checks; fake-provider tests cannot prove desktop
  integration.
- An encrypted project key may require an interactive reload after its agent
  expires; a ProxyCommand fails closed instead of opening a passphrase prompt.
- Other DevPod versions are accepted; only 0.6.15 has been tested.
