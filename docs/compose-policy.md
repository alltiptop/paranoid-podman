# Compose policy

The Compose adapter validates a resolved project and executes a private
reviewed snapshot. It is intentionally smaller than the full Compose CLI:
unknown commands, options, and fields fail closed with exit code 125.

## Contents

- [Command boundary](#command-boundary)
- [Review and execution](#review-and-execution)
- [Configuration policy](#configuration-policy)
- [Environment and secret handling](#environment-and-secret-handling)
- [Mounts and protected files](#mounts-and-protected-files)
- [Build policy](#build-policy)
- [DevPod compatibility](#devpod-compatibility)
- [Actionable denials](#actionable-denials)
- [Limitations](#limitations)

## Command boundary

| Command | Behavior |
| --- | --- |
| `help`, `version` | forwarded without loading a project |
| narrow `ls` JSON query | forwarded only for DevPod project-file recovery |
| `config --quiet` | validates without printing resolved values |
| `config --services` | validates and prints service names only |
| `ps`, `logs`, `images`, `port`, `wait` | run against a reviewed snapshot |
| `up`, `build`, `down`, `start`, `stop`, `restart`, `pause`, `unpause`, `pull`, `exec`, `run` | validate and execute a private snapshot |
| every other command | denied |

Supported global inputs are:

- repeated `-f`/`--file`;
- repeated validated `--profile`;
- one validated `-p`/`--project-name`;
- one explicit project file named `.env`;
- `--no-ansi`, `--dry-run`; and
- a `--parallel` value from 1 through 64.

Standard-input Compose files, project-directory overrides, other global env
files, provider paths, raw provider arguments, and unreviewed global options
are denied.

The supported provider is `podman-compose` 1.6.x through `podman compose`.
The adapter supplies the verified real Podman path explicitly. Docker Compose
v2 and other providers are not supported.

## Review and execution

For each project command, the adapter:

1. resolves regular non-symlink Compose inputs and their safe common project
   directory;
2. treats the parent of `.devcontainer` as the workspace root when
   appropriate;
3. preflights YAML, duplicate keys, source indirection, dotenv files, and the
   literal-secret lint;
4. asks the configured provider for normalized `config` output using a
   filtered private project env file;
5. parses and validates the resolved structured model;
6. rewrites protected mounts, the provenance labels, and mandatory defaults;
7. verifies current provenance for existing containers before actions that can
   start, resume, or execute code;
8. writes mode-`0600` Compose and service-env snapshots; and
9. executes only the private snapshot.

Interpolation is completed during the first render. Dollar signs are escaped
before the second provider pass so values cannot be expanded again from a
different environment.

Provider output and errors from automatic rendering are hidden because they
may contain interpolated secrets. Failures use fixed categories and bounded,
value-hidden `file:line` structural locations instead.

Successful validation is silent for project-local mounts. `up` and `run` require
terminal confirmation for outside-project bind sources, as described below.
`PODMAN_GUARD_DEBUG=1` prints a redacted summary and source locations.
TTY and non-TTY invocations receive the same validation.

`exec` accepts detach/TTY selection, one validated user, one absolute workdir,
an instance index from 1 through 10, and explicit non-sensitive `NAME=value`
entries. Arguments after the service and command boundary are preserved for the
in-container process. `--privileged`, implicit host environment, and unknown
exec options are denied.

`run` supports a service's default command or an explicit command with opaque
arguments. Accepted options are `--rm`, `--build`, `--no-deps`, `-d`/`--detach`,
`-T`, `--name`, `--entrypoint`, `-u`/`--user`, `-w`/`--workdir`, explicit
non-sensitive `-e`/`--env` assignments, `-p`/`--publish`, and `--service-ports`.
Short values may be attached, such as `-eMODE=test`. Service ports remain
unpublished unless requested. Put volumes and labels in the Compose file so
they participate in mount validation and protected submount generation.

For `run --build`, the adapter builds the selected service from the same snapshot
first and stops on failure, before dependencies or the task can start. This
compensates for podman-compose 1.6.x ignoring that failure inside its run handler.

`up` also supports `--force-recreate`, `--always-recreate-deps`,
`--renew-anon-volumes`/`-V`, and `--remove-orphans`. `down --volumes`/`-v` and
`--remove-orphans` retain their normal explicit cleanup semantics, including
deletion of project volumes when requested.

To recreate project containers, use `podman compose up --force-recreate`.
Podman's `--replace` hint applies to direct container commands;
podman-compose 1.6.x has no `up --replace` option.

If a fixed `container_name` is already in use, check the existing container's
Compose project. The wrapper's default project name includes a path hash;
use `-p NAME` consistently to select an existing project's original name.
Containers created outside the guard need a guarded `down` followed by `up`
under that same project name. Omit `--volumes` to retain named volumes.
The wrapper does not automatically remove or adopt conflicting containers.

## Configuration policy

The resolved top level may contain only `name`, `version`, `services`,
`volumes`, and `networks`. Generic `x-*` extensions may help author the
source but are removed from the execution snapshot.

| Area | Allowed | Denied |
| --- | --- | --- |
| Services | conventional images and project-local builds, including `build` without `image` | malformed or unknown service fields |
| Privilege | non-privileged containers, capability drops | privileged mode, capability additions, devices, GPUs, custom runtimes |
| Security | `no-new-privileges` | other security options, sysctls, custom cgroups |
| Namespaces | bridge or no network; `auto`, `keep-id` including UID/GID/size options, or private user namespace | host/joined PID, IPC, UTS, or network namespaces |
| Resources | CPU/memory limits, `shm_size`, bounded `nofile`/`nproc` ulimits; PID limit 1-32768, default 512; scale 0-10 | deploy rules and unreviewed resource controls |
| Ports | normal TCP/UDP bindings, including wildcard, LAN, IPv6, ranges, and automatic host ports | malformed addresses or port numbers |
| Volumes | anonymous or declared project-local volumes | external resources, custom drivers, inherited volumes |
| Networks | declared bridge networks, optionally internal and explicitly named | external networks, custom drivers, and reserved namespace names |
| Environment | explicit values and validated project env files | implicit host inheritance |
| Lifecycle | validated restart and pull policies | malformed policies |
| Logging | `journald`, `json-file`, `k8s-file`, or `none` with bounded options | host-targeting paths and custom drivers |

Compose secrets/configs, engine sockets, links, arbitrary host mappings,
`volumes_from`, credential specifications, automatic host synchronization,
provider-specific `x-podman*` extensions, and unknown settings are denied.

The adapter always writes `security_opt: [no-new-privileges]`. It adds
`pids_limit: 512` when absent and preserves an explicit value through 32768.
Capability drops, restart policy, pull policy, rootless user namespace, and
ordinary proxy behavior are otherwise preserved.

`extra_hosts` accepts `host.docker.internal:host-gateway` and
`host.containers.internal:host-gateway`, in list or mapping form.
CPU and memory fields are preserved. The supported provider currently ignores
`memswap_limit`; do not rely on it to enforce a swap limit. Other resource
limits also depend on the rootless runtime and host cgroup configuration.

Published ports retain their configured address; the adapter does not force
loopback or restrict ordinary outbound connections. Binding to `127.0.0.1`
remains available when a service should be reachable only from the host.

An explicit network `name` is preserved, so applications can share a named
bridge. Before `up`, `run`, `start`, `restart`, `unpause`, or `exec`, an existing
explicitly named network must use the bridge driver and match the declared
`internal` setting. A shared bridge allows its attached services to communicate;
the wrapper does not provide network isolation between those services.

Every service receives `io.github.paranoid-podman.policy=1` and the current
`io.github.paranoid-podman.installation` value. Source files cannot set these
reserved labels or Podman/Docker Compose-management labels.

Before `up`, `run`, `start`, `restart`, `unpause`, or `exec`, the adapter enumerates
containers with the selected Compose project label and requires each to carry
both current provenance values. This prevents an image from passing by
predeclaring the public policy label alone. `stop`, `pause`, and `down` remain
available to clean up older project containers.

## Environment and secret handling

The normal or explicitly selected project `.env` is parsed into a private
mode-`0600` copy. Compose and Podman provider-control keys are removed before
rendering. Known secret and provider-routing variables are also removed from
the provider process environment.

Project-local service `env_file` entries must be regular non-symlink files no
larger than 1 MiB. They are parsed and copied beside the temporary snapshot;
the original path is not used during execution.

Before interpolation, the source lint rejects a direct value assigned to a
recognized secret-like name in:

- service `environment`;
- build `args`; and
- environment-style `x-*` mappings.

Variable references such as `${API_TOKEN:?required}` and values supplied by a
validated project env file are allowed. This is a small name-and-syntax lint,
not an entropy scanner. It can miss renamed, encoded, split, or disguised
secrets. Devcontainer contents are not inspected.

## Mounts and protected files

A bind source must already exist as a regular non-symlink file or directory.
Individual ordinary paths may be outside the project. The following are denied:

- the filesystem root, the complete user home, and broad system trees;
- a source broader than the selected project;
- known credential and container-runtime directories;
- sockets, devices, FIFOs, missing paths, and symlinked sources;
- host-path creation and shared propagation; and
- ownership-changing `U`/`idmap` options.

SELinux `z` or `Z` is allowed only for a source inside the selected project.
External relabeling and arbitrary relabel forms are denied.

Before `up` or `run`, outside-project binds require one confirmation for that
command. An orange `[warning]` lists each full host path, service, container path,
read/write mode, and original Compose file with line number. Both files and
directories, including read-only binds, require confirmation. Only selected
services and dependencies started by the command are included; `--no-deps` is
respected. Automatically added protected submounts do not produce extra warnings.

Type exactly `y` and Enter to continue. Any other answer, EOF, or Ctrl-C cancels.
Without terminal stdin, the command fails without reading pipe or protocol input.
Approval is not saved and never overrides the denials above. `config`, `build`,
inspection, cleanup, and operations on existing containers do not prompt because
they do not create these binds. `up --no-start` still creates containers and asks.

The resolved configuration and environment files are copied before the prompt;
approval applies to that snapshot. Source locations include YAML aliases and
overrides. If interpolated mount targets make the location ambiguous, all matching
source candidates are explicitly listed. Unrelated configuration values are hidden.

Writable directory binds receive read-only submounts for existing:

- `.devcontainer`, `.devcontainer.json`, `devcontainer.json`;
- live dotenv and common credential configuration files;
- `.dockerignore`, `.containerignore`; and
- `.git`, `.gitmodules`, `.git-credentials`.

Binding a protected path directly forces it read-only. Missing paths are not
created, duplicate targets are denied, and a nested mount cannot override a
protected target.

An explicit read-only bind of the same protected source at the same target is
preserved; the adapter omits its duplicate automatic submount. A different
source or a mount below the protected target remains denied.

Unreadable directories owned by another UID, such as rootless database data,
are skipped during configuration discovery. The adapter preserves the bind's
access mode and does not change host permissions. Files inside skipped
directories do not receive automatic read-only protection; readable project
configuration remains protected. Unreadable user-owned directories and other
filesystem errors still cause a denial.

`PODMAN_GUARD_PROTECT_GIT=0` disables only the `.git` read-only submount for
one invocation. Dev Container configuration remains protected. Existing
containers must be recreated before a changed mount policy can affect them.

Dockerfile, Containerfile, and Compose files outside `.devcontainer` stay writable
unless their bind is explicitly read-only. Everything inside `.devcontainer`
remains read-only, including its Dockerfiles and Compose files.

## Build policy

The build context and Dockerfile must resolve to existing regular paths inside
the Compose project. Supported fields are `context`, `dockerfile`, `args`,
`target`, and `labels`.

Additional contexts, SSH forwarding, build secrets, entitlements, cache
destinations, and other advanced build fields are denied.

The shared build preflight rejects direct literals assigned to recognized
secret-like names in Dockerfile `ARG`, `ENV`, `LABEL`, and straightforward
`RUN` assignments. At the build-context root, live dotenv files and common
credential paths must be excluded by the selected Dockerfile's ignore file or
the context's `.containerignore`/`.dockerignore`, following the
[direct build rules](direct-policy.md#build-boundary). `.git` is allowed in the
build context and is independently protected read-only in runtime workspace
mounts.

The ignore preflight requires a complete exclusion of each sensitive root path.
Descendant exceptions and unreviewed patterns may require a final explicit
root exclusion. With exceptions, a bounded check of sensitive directory entries
rejects names the builder cannot safely exclude; it never reads credential contents.
The checks also miss renamed, nested, encoded, or generated secrets. All
non-ignored context files and ordinary build networking remain available to the
rootless builder.

`CORS_CREDENTIALS` and `CORS_ALLOW_CREDENTIALS` are configuration switches,
not secret assignments. Password, token, and other credential checks still apply.

## DevPod compatibility

The following Compose flow was tested with DevPod 0.6.15:

- validated project names and the narrow JSON project lookup;
- a workspace `.env`;
- Compose files under `.devcontainer`;
- generated files in DevPod's sibling `.docker-compose` directory;
- `build --pull`, `up -d`, `--no-recreate`, `stop`, and `down`; and
- non-interactive reviewed execution with project-local binds.

DevPod compatibility changes only command orchestration. A generated model that
requests privileged mode, dangerous capabilities, engine sockets, broad host
mounts, or another denied setting still fails.

The optional `devpod` entry point also isolates SSH credentials before DevPod
reaches this Compose layer. It does not relax Compose policy. See
[DevPod credential isolation](devpod-credentials.md).

A service carrying the DevPod workspace label receives a readable hostname
derived from that workspace, unless `hostname` is explicitly set. Other services
keep their existing defaults. The hostname takes effect on container creation.

An older or pre-reinstall DevPod Compose container must be recreated so it
receives the current provenance pair and protected mounts. Guarded `down`
remains available when old provenance prevents `up`, `start`, or `exec`.

## Actionable denials

Every rejection exits with code 125 and prints a prominent `BLOCKED`,
`UNSUPPORTED`, or `ERROR` heading, a value-free reason plus
a `compose-guard: next step:` line. Guidance covers Compose input and syntax,
interpolation, mounts, networking, privileges, secrets, build contexts,
provider setup, and container provenance. Guidance uses explicit violation
categories, with a general policy fallback.

Known affected service names are shown; source locations are narrowed to that
service and the relevant category. Color is used only in terminals and respects
`NO_COLOR`. Source values remain hidden. A hint
never changes the reviewed snapshot or bypasses a policy.

## Limitations

- The provider and YAML parser read project input before the normalized model
  is validated.
- Paths and files can change between validation, private copying, and provider
  execution.
- Build instructions can execute arbitrary code and read non-ignored context
  content inside the rootless builder.
- Ordinary outbound networking and reachable host services are not isolated.
- Already running legacy containers do not become safe retroactively. Guarded
  active lifecycle refuses them, while inspection and cleanup remain possible.
- Images, Podman, the Compose provider, OCI runtime, filesystem, and kernel are
  separate trust boundaries.
- A host process running as the user can bypass a `PATH` wrapper.

See the [threat model](threat-model.md) for the complete boundary and zero-day
warning.
