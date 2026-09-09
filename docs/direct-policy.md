# Direct Podman policy

This document describes commands handled directly by the `podman` and
`docker` wrappers. Compose has a separate
[reviewed-snapshot policy](compose-policy.md).

## Contents

- [Command boundary](#command-boundary)
- [Container creation](#container-creation)
- [Runtime policy](#runtime-policy)
- [Bind mounts](#bind-mounts)
- [Protected project files](#protected-project-files)
- [Existing container provenance](#existing-container-provenance)
- [DevPod operations](#devpod-operations)
- [Build boundary](#build-boundary)
- [Actionable denials](#actionable-denials)
- [Limitations](#limitations)

## Command boundary

The wrapper is a small, fail-closed interface rather than a transparent
replacement for the full Podman CLI.

| Command group | Supported forms |
| --- | --- |
| Metadata | no arguments, `help`, `version`, `-h`, `--help`, `-v`, `--version` |
| Container creation | `run`, `create`, `container run`, `container create` |
| Inspection and lifecycle | reviewed forms of `ps`, `inspect`, `info`, `start`, `stop`, `logs`, `exec`, and `rm` |
| DevPod account setup | narrowly reviewed `cp` forms |
| DevPod images and builds | reviewed forms of `pull`, `push`, `tag`, `build`, `buildx build`, and `buildx version` |
| Compose | dispatched to the local Compose guard |
| Everything else | denied with exit code 125 |

Podman global options are denied for guarded commands. Use the verified real
Podman binary deliberately for host administration. The wrapper constructs an
argument vector directly and never invokes a shell to reinterpret it.

Before the provider runs, known secret variables and Podman connection,
remote-routing, storage, configuration, and temporary-directory overrides are
removed from its process environment.

## Container creation

Supported creation forms are:

```text
podman run [OPTIONS] IMAGE [COMMAND ...]
podman create [OPTIONS] IMAGE [COMMAND ...]
podman container run [OPTIONS] IMAGE [COMMAND ...]
podman container create [OPTIONS] IMAGE [COMMAND ...]
```

The first non-option argument is the image boundary. It must be a conventional
image name, tag, SHA-256 digest, or image ID. File and URL transports are
denied. Arguments after the image belong to the in-container command and are
not parsed as runtime options.

An unknown, malformed, ambiguous, or incorrectly ordered runtime option is
rejected before Podman executes.

Every created container receives two reserved labels:

- `io.github.paranoid-podman.policy=1` identifies the policy generation; and
- `io.github.paranoid-podman.installation=<local-id>` identifies the user-level
  installation that created it.

The installer generates a random 256-bit installation identifier and keeps it
in owner-only lifecycle metadata and the release launcher. User input cannot
set either label or reserved Compose-management labels.

## Runtime policy

| Area | Policy |
| --- | --- |
| Privilege | `--privileged` may only be explicitly false; capability additions and devices are not supported |
| Capabilities | only `--cap-drop=ALL` is accepted |
| Security options | only `no-new-privileges` is accepted |
| Container user | explicit non-root users are accepted; explicit root users or groups are denied |
| User namespace | `keep-id`, including validated `uid`, `gid`, and `size` options |
| PID, IPC, UTS, and cgroup namespaces | only private namespaces are accepted |
| Network | `bridge`, `none`, `pasta`, or `slirp4netns`, without custom options |
| Host mapping | `host.docker.internal:host-gateway` and `host.containers.internal:host-gateway` |
| Published ports | TCP/UDP mappings, IPv4/IPv6 addresses, port ranges, automatic host ports, and `-P`/`--publish-all`; addresses are preserved |
| Environment | explicit `NAME=value`; implicit host inheritance is denied |
| Sensitive environment names | replaced with `--unsetenv=NAME` |
| Images | guarded creation uses `--pull=never` |
| Restart policy | only `no` or `none` |
| PID limit | defaults to 512; an explicit value from 1 through 32768 is accepted |

Ordinary reviewed process and resource options are supported, including
terminal attachment, CPU and memory limits, entrypoint, hostname, working
directory, read-only root filesystems, stop settings, `nofile`/`nproc`
limits, and explicit labels.

DevPod's workspace label supplies a readable default hostname for new containers;
explicit hostnames are preserved. Shortened or normalized names get a hash suffix.

An explicit environment file must be an existing regular non-symlink file no
larger than 1 MiB and cannot come from a broad or sensitive host location. Its
contents are a deliberate user input and are not scanned. A literal value in a
secret-like container label or build argument is rejected.

Unless an accepted value is already present, creation adds:

- `--userns=keep-id` and `--cap-drop=ALL` when no explicit user is selected;
- `--security-opt=no-new-privileges`;
- `--pids-limit=512`;
- `--http-proxy=false`;
- `--pull=never`;
- `--restart=no`; and
- `--unsetenv=NAME` for known sensitive host variables.

An explicit non-root container user suppresses the compatibility-sensitive
`keep-id` and automatic capability-drop defaults. It does not disable
`no-new-privileges` or the remaining mandatory defaults.

## Bind mounts

A bind source must:

- already exist as a regular file or directory;
- use an absolute or explicit relative path;
- resolve without a source symlink;
- avoid ambiguous comma, colon, and control-character forms; and
- remain outside broad or sensitive host locations.

The filesystem root, the complete user home, broad system trees, paths broader
than the current project, sensitive credential/runtime directories, special
files, and named volumes are denied. Individual ordinary paths outside the
project are allowed. Anonymous volumes and narrowly formed tmpfs mounts are
allowed.

Bind options that can change host ownership or labels, including `U`,
`idmap`, `z`, `Z`, and arbitrary relabeling, are denied. A mount cannot
target the container root, duplicate another target, or override a generated
protected target.

## Protected project files

When a directory is mounted writable, the wrapper recursively finds existing:

- `.devcontainer`, `.devcontainer.json`, and `devcontainer.json`;
- `.dockerignore`, `.containerignore`, and live dotenv files;
- common credential configuration files; and
- `.git`, `.gitmodules`, and `.git-credentials`.

These paths are mounted again read-only. Binding one directly also forces it
read-only. Missing paths are not created, and a later nested mount cannot
override the protection.

Discovery skips directories that deny access and belong to another UID, such
as rootless database storage. Their bind access mode and host permissions are
preserved; files inside them do not receive automatic read-only submounts.
Unreadable user-owned directories and other filesystem errors still cause a
denial. Known protected directories such as `.git` are mounted read-only as a
whole even when their contents cannot be listed.

`.git` is protected by default. For one invocation only:

```sh
PODMAN_GUARD_PROTECT_GIT=0 podman run ...
```

This setting affects only `.git`; Dev Container configuration remains read-only.
Dockerfile, Containerfile, and Compose files outside `.devcontainer` remain
writable unless their bind was explicitly requested read-only. The outside-project
confirmation described in the [Compose policy](compose-policy.md#mounts-and-protected-files)
applies to Compose `up` and `run`, not direct Podman commands.

## Existing container provenance

Commands that can activate code or modify an active container must not inherit
mounts and privileges created outside the current policy. Before `start` or
`exec`, the wrapper asks the verified real Podman for both reserved provenance
labels. A missing, malformed, old, foreign, or unreadable value fails closed.

Read-only `ps`, `inspect`, and `logs`, plus cleanup-oriented `stop` and `rm`, do
not require provenance. This allows an old container to be examined, stopped,
and removed without first trusting it. DevPod's narrow account-file `cp` flow
does require current provenance.

`rm` and `container rm` accept one or more explicit names or IDs, with
`-f`/`--force`, `-i`/`--ignore`, `-v`/`--volumes`, and `-t`/`--time`.
Flags may appear before or after names; short flags may be combined, such as
`-fv`. Timeout values accept non-negative seconds or `-1` for an infinite wait;
Podman requires `--force` when using a timeout. `--volumes` removes associated
anonymous volumes only. Implicit selection through `--all`, `--latest`, filters,
ID files, or dependent containers remains outside this command boundary.

The pair prevents an image from satisfying provenance by predeclaring only the
public policy label. It is still a local compatibility marker, not a signature,
and does not make an already running container safe. A host process with the
user's permissions can read the installation identifier, bypass the wrapper,
or create both labels deliberately. Direct source-tree use without an explicit
installation ID has only a deterministic development fallback.

## DevPod operations

The Docker-driver command flow was tested with DevPod 0.6.15. Lifecycle
handlers validate container/image references and only reviewed option forms.

`exec` accepts explicit users, workdirs, terminal flags, and non-sensitive
`NAME=value` entries. It cannot add mounts, namespaces, or container
privileges. `rm` supports the explicit cleanup options described above.
`pull`, `push`, and `tag` accept only conventional image
references.

The `cp` handler is not general-purpose: it accepts only DevPod's
`/etc/passwd` or `/etc/group` exchange with a private, user-owned temporary
file matching the expected DevPod name.

Existing containers retain their original mount layout and may lack the current
provenance pair. Recreate an older or pre-reinstall DevPod workspace before
`start`, `exec`, or the account-file copy phase. `stop` and `rm` remain
available for cleanup.

The optional user-level DevPod wrapper separately constrains SSH credentials
for `up`, `build`, direct `ssh`, generated OpenSSH hosts, and IDE launches. It
does not weaken this Podman policy. See
[DevPod credential isolation](devpod-credentials.md).

## Build boundary

`build` and `buildx build` have reviewed forms for DevPod. The main context must
be an ordinary directory outside broad or sensitive host trees. The Dockerfile
must be a regular file inside the main context.

Additional filesystem contexts use the same path and credential checks whether
written as `assets=assets`, `assets=./assets`, or an absolute path. Checked paths
are forwarded as absolute paths. Image contexts require `container-image://`,
`docker://`, or `docker-image://` followed by a valid image reference, for example
`--build-context base=container-image://example.invalid/base:latest`.
URL, archive, and other unreviewed transports are denied.

Supported options cover tags, targets, platforms, labels, build arguments,
registry-backed cache references, and the `load`, `pull`, `push`, and
`no-cache` flags. File-backed cache destinations, secret mounts, SSH
forwarding, entitlements, and unknown build features are denied.

Before the builder runs, the wrapper:

- rejects direct literal values assigned to recognized secret-like names in
  Dockerfile `ARG`, `ENV`, `LABEL`, and straightforward `RUN` assignments;
- permits variable-only references such as `${API_TOKEN}`;
- checks the root of every filesystem build context for live dotenv and common
  credential paths; and
- requires those paths to be excluded by `.containerignore` or
  `.dockerignore`.

For a selected Dockerfile, `<Dockerfile>.dockerignore` takes precedence over
`<Dockerfile>.containerignore`, then the context's `.containerignore` and
`.dockerignore`, matching Podman 6.1.x. Additional contexts use their root ignore
files. Ignore files must be regular files, not symlinks.

The check requires complete exclusion of each recognized sensitive root path.
An exception such as `!.ssh/project-key` invalidates the exclusion of `.ssh`,
even if that descendant is absent or separately excluded later. Finish with
`.ssh` to exclude the entire tree again. Ordinary `*`, `?`, `**/`, and reviewed
character classes are supported; unreviewed syntax cannot prove exclusion.
When exceptions exist, a bounded metadata-only check also rejects newline-bearing
names, symlinked sensitive roots, and unreadable or oversized sensitive trees.
This accounts for the builder's exception traversal; no credential contents are
read and directory symlinks are not followed. Without exceptions, excluded trees
need no traversal. The Dockerfile lint is not a complete parser or secret scanner.
Other instructions and every
non-ignored context file remain available to the rootless builder.

Build contexts are copied into the builder rather than mounted with the runtime
workspace policy. Consequently live dotenv files and detected credential paths
must be excluded from the build context. `.git` itself is allowed there for
development compatibility and remains protected by a read-only submount when
it is exposed through a runtime workspace bind. A rejection reports all
detected unignored root paths in one message.

The management CLI can apply only this standard ignore-file remediation:

```sh
paranoid-podman build-context audit .
paranoid-podman build-context protect .
```

It updates the active `.containerignore` or `.dockerignore` atomically and
rechecks the context. These management commands inspect root ignore files;
when using Dockerfile-specific rules, add the reported exclusions to that file.
It does not edit Dockerfiles, acknowledge literal-secret
findings, or bypass mount, privilege, namespace, socket, or provenance policy.

## Actionable denials

Every policy rejection exits with code 125 and emits two parts: the value-free
reason and a `podman-guard: next step:` line. Guidance is specialized for
common categories such as mounts, networking, privileges, secrets, build
contexts, and container provenance. Guidance uses explicit violation categories;
the general policy category explains that the operation was not forwarded.

Guidance never weakens a policy automatically. When an operation is outside
the developer interface, remove it or add a narrowly reviewed policy with
tests. Use the verified real Podman directly only for deliberate host
administration and cleanup.

## Limitations

- Ordinary outbound container and build networking is not isolated.
- Allowed individual binds remain writable except for protected submounts.
- Canonicalized paths and filesystem contents can change before Podman uses
  them.
- Read-only submounts protect only paths that are actually mounted.
- They do not prohibit Git commands, hooks, reads, or direct host changes.
- The build checks can miss renamed, nested, encoded, split, or generated
  secrets and cannot restrict ordinary builder networking.
- A host process running as the user can bypass the wrapper and invoke the real
  provider.

See the [threat model](threat-model.md) for trust boundaries and residual risks.
