# Threat model

`paranoid-podman` is a defense-in-depth wrapper for local rootless container
development. This document defines what it tries to protect, which components
it trusts, and where its guarantees end.

## Contents

- [Objective](#objective)
- [Assets and attacker](#assets-and-attacker)
- [Trust boundaries](#trust-boundaries)
- [In scope](#in-scope)
- [Attacks the policy should catch](#attacks-the-policy-should-catch)
- [Out of scope](#out-of-scope)
- [Residual risks](#residual-risks)
- [Security principles](#security-principles)

## Objective

The project aims to stop common, direct attempts to expand a development
container beyond its intended project boundary:

- privileged or host-integrated container settings;
- broad or sensitive host mounts;
- access to the host container engine;
- accidental credential and session forwarding;
- writable container-control files on project mounts; and
- obvious literal secrets committed to Compose or Dockerfiles.

The intended balance is low-friction development. Individual ordinary project
inputs remain usable, while requests for broader host access fail explicitly.
This is not an enterprise isolation boundary, an antivirus, or a claim that a
container cannot escape.

## Assets and attacker

The protected assets are:

- host files outside the selected development inputs;
- the complete user home and broad system trees;
- rootless Podman/Docker sockets and runtime configuration;
- host credentials, SSH/GPG agents, desktop/session endpoints, and provider
  routing variables;
- repository metadata and container configuration on writable project mounts;
  and
- the integrity of other rootless containers and project resources.

The expected hostile input is:

- an untrusted or accidentally modified Compose/devcontainer configuration;
- a script or development tool that invokes `podman`, `docker`, or Compose
  through the normal user `PATH`;
- a container process trying to modify its writable host mounts; or
- a semi-autonomous development workflow that starts containers without a
  complete manual command review.

The attacker is not assumed to already run arbitrary code as the host user. A
host process with the user's permissions can bypass a `PATH` wrapper, invoke
the real provider, replace user-owned files, and access everything the user can
access directly.

## Trust boundaries

```text
untrusted project input
        |
        v
paranoid-podman parser and policy
        |
        v
verified rootless Podman / Compose provider
        |
        v
OCI runtime -> kernel -> host filesystem
```

The wrapper controls only the arguments, environment, reviewed Compose
snapshot, and mount rewrites that it sends to the provider.

The following remain separate trusted dependencies and possible vulnerability
sources:

- Python and the YAML/dotenv parsers;
- `podman-compose` and Podman command-line behavior;
- image contents and Dockerfile build instructions;
- the OCI runtime, user-namespace implementation, filesystem, and kernel; and
- the correctness of this wrapper's own parsing and policy code.

The installer restricts Podman and Compose to reviewed major/minor series.
DevPod 0.6.15 is the tested version; other versions are accepted without a
compatibility guarantee. Credential and command checks still apply.

## In scope

- typed parsing of direct `run`, `create`, and their `container` forms;
- denial of unreviewed global and runtime options;
- namespace, privilege, device, mount, network, port, image, and environment
  policy;
- read-only submounts for existing protected project paths;
- a narrow writable-`.git` opt-out that does not weaken other paths;
- structured Compose validation and private reviewed-snapshot execution;
- reserved policy-generation and installation labels, with provenance checks
  before active lifecycle use of existing containers;
- value-hidden diagnostics and small Compose/Dockerfile literal-secret and
  build-context checks;
- DevPod Docker and Compose command flows, tested with 0.6.15;
- optional DevPod SSH isolation that removes the ambient agent, or
  replaces it with a dedicated agent containing one project identity;
- two-phase DevPod IDE opening that protects the generated SSH block before the
  IDE is allowed to connect; and
- fake-provider tests that verify rejected values do not reach the provider.

Direct host-administration commands are denied rather than partially analyzed.
They remain available only by deliberately invoking the real provider outside
the wrapper.

## Attacks the policy should catch

| Example | Expected result |
| --- | --- |
| Add `--privileged`, a host namespace, device, capability, or unsafe security option | Deny before Podman |
| Mount `/`, the complete user home, a system tree, or a directory broader than the project | Deny |
| Mount the rootless Podman/Docker socket or a sensitive runtime directory | Deny |
| Use `U`, `idmap`, or unsafe relabeling to mutate host ownership/labels | Deny |
| Publish a service on a wildcard, LAN, or loopback host address | Allow; preserve the requested mapping |
| Forward ambient credentials, an SSH agent, desktop session, or provider-routing environment | Remove or deny |
| Let DevPod expose every identity already loaded in the host SSH agent | Prompt before workspace access; use no agent or a one-key project agent by default |
| Modify `.devcontainer`, `.devcontainer.json`, `devcontainer.json`, or protected repository metadata through a project bind | Read-only submount |
| Add an otherwise allowed outside-project bind through Compose `up` or `run` | Show the full path and source location; require terminal confirmation |
| Override a protected submount with a second nested mount | Deny |
| Use external Compose resources, engine access, devices, or joined namespaces | Deny |
| Start or enter an existing container without the current policy and installation provenance | Deny; permit inspection and cleanup |
| Put a literal value under a recognized password/token/secret name in Compose or a Dockerfile | Deny without printing the value |
| Send a common root-level credential path in a build context without an ignore rule | Deny before the builder runs |

These examples are representative, not exhaustive. Passing these cases does
not establish protection from equivalent encodings or unknown techniques.

## Out of scope

- rootful Podman, remote Podman connections, and Docker Compose v2;
- arbitrary code already running as the host user;
- direct invocation of the real Podman or Compose provider;
- kernel, Podman, Compose, OCI-runtime, filesystem, parser, and virtualization
  vulnerabilities;
- malicious or vulnerable container images and build tools;
- protection from unknown techniques or zero-day exploits;
- outbound network isolation and ordinary host services reachable from a
  rootless container;
- general secret discovery, entropy scanning, devcontainer analysis, or
  Dockerfile analysis beyond the small assignment lint;
- protection against misuse of the one project identity deliberately exposed
  through a dedicated SSH-agent socket;
- data the host user deliberately exposes through an allowed individual bind;
  and
- retroactive protection of already running containers.

Advanced Compose features that do not yet have a reviewed policy are denied.
This includes external resources, Compose secrets/configs, additional build
contexts, SSH forwarding, build secret mounts, and entitlements.

## Residual risks

### Host-user bypass

A `PATH` wrapper cannot constrain the user account that owns it. Host malware,
a compromised editor plugin, or a deliberately bypassing command can run the
real provider or access host files directly.

### SSH-agent capability

An agent socket hides private-key bytes but still accepts signing requests. A
compromised workspace can therefore use every identity visible through that
socket while the agent is available. The DevPod integration reduces this from
the ambient account-wide agent to either no credential agent or one verified
project identity. Repository scope and read/write permission are enforced by
the Git forge, not by the wrapper.

DevPod's workspace transport key is separate and remains available for IDE and
terminal connectivity. Protected modes disable automatic private-key loading,
Git and registry credential injection, GPG forwarding, and automatic SSH
signing-key forwarding. DevPod's agent transport stays enabled only so it can
carry the wrapper-selected empty or one-key socket.

### Time-of-check/time-of-use

Paths are canonicalized and inspected before provider execution, but the
wrapper cannot freeze the filesystem. A path component, bind content,
Dockerfile, env file, or Compose input can change after validation.

The private Compose snapshot removes the source-reparse gap for structured
configuration, but it cannot freeze bind-mounted content or the files consumed
by the builder.

Dockerfile, Containerfile, and Compose files outside `.devcontainer` are writable
inside a project bind. Later guarded builds and Compose commands revalidate their
inputs. Outside-project Compose binds require explicit approval for each `up` or
`run`; direct Podman commands retain their existing host-path policy. Approval
applies to a private snapshot created before the prompt and does not persist.

### Existing resources

Read-only submounts affect only newly created containers. An existing container
or Compose project may have been created under an older or unguarded
configuration. New containers receive a public policy-generation label and a
random installation-specific label; guarded `start`, `exec`, and active Compose
lifecycle require both values to match.

The installation value prevents an image from passing by predeclaring only the
public policy label. This still does not stop an already running legacy
container, cryptographically prove historical configuration, or constrain a
host process that reads the local value and invokes the real provider.
Inspection, stopping, and removal remain available so old resources can be
diagnosed and cleaned up. Direct source-tree execution uses a deterministic
fallback unless an installation ID is supplied, so the installed launchers are
the intended provenance boundary.

### Exact mounted paths

Protected submounts cover only the resolved paths actually present beneath an
allowed bind and found during discovery. Inaccessible directories owned by
another UID, including rootless service data, are not searched; configuration
inside them is not automatically protected. Protected submounts do not prevent:

- reads or Git commands;
- access through another already-existing path or mount;
- direct host changes by the user; or
- writes to ordinary project files that are intentionally left writable.

### Providers, builds, and networking

Compose and its parsers process source input before the resolved model is
validated. Builds execute arbitrary instructions inside the rootless builder
and can read non-ignored context content. The context preflight covers only
common sensitive names at its root. Ordinary outbound networking and reachable
host services are not sandboxed.

### Secret lint

The Compose and Dockerfile lints recognize common secret-like names and direct
assignments. They can miss renamed, nested, encoded, split, generated, or
otherwise disguised values. Ignore-file checks require complete exclusion of
known sensitive root names and their subtrees. Descendant exceptions or unreviewed
syntax may require a final explicit root exclusion. With exceptions, sensitive
trees receive a bounded metadata-only check for names the builder cannot safely
exclude. Credential contents are never read. All additional filesystem
contexts receive path preflight; image contexts require an explicit transport.
See the [direct build boundary](direct-policy.md#build-boundary).

## Security principles

- Prefer small, explicit rules over claims of comprehensive detection.
- Fail closed when security-relevant parsing is ambiguous.
- Do not pass an unknown escape surface through to the provider.
- Keep opt-outs narrow, visible, and limited to one invocation.
- Keep automatic policy diagnostics free of resolved secrets and raw provider errors.
- Do not create missing host paths while validating mounts.
- Preserve normal development inputs that do not broaden host access.
- Test policy invariants with fake providers before real runtime tests.
- Document only behavior supported by the current implementation and tests.

For the concrete rules, see the
[direct policy](direct-policy.md) and
[Compose policy](compose-policy.md).
