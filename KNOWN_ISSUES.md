# Known issues

## CMP-001: Compose resource and profile limitations

The supported `podman-compose` 1.6.x provider does not pass `memswap_limit` to
Podman. The wrapper accepts the field for configuration compatibility, but it
does not enforce that swap limit. CPU, memory, shared-memory, and ulimit options
are forwarded; actual enforcement depends on rootless Podman and host cgroups.

Select profiles explicitly with `--profile NAME`, including for one-off tasks.
The provider does not automatically enable a profile when its service is named
on the command line. `run` volume and label overrides must be declared in the
Compose file so the adapter can validate the complete mount layout.

## DEV-001: VSCode loses the project SSH-agent socket

Status: **Deferred** on 2026-09-08. No verified IDE fix is shipped.

Observed with DevPod 0.6.15 and VSCode using Open Remote - SSH 0.1.2 on
Linux, with a workspace configured in `project-key` mode. This is not a claim
about other versions or IDE extensions.

### Symptoms and cause

In VSCode's integrated terminal, `SSH_AUTH_SOCK` points to a path such as
`/tmp/auth-agent<id>/listener.sock`, but the socket no longer exists:

```text
Error connecting to agent: No such file or directory
```

DevPod creates the forwarded agent socket for an SSH session and removes it
when that session's command ends. Open Remote - SSH captures the socket path
during its short server-setup command, then passes that path to the IDE after
setup has finished. The resulting stale environment prevents SSH-based Git
authentication through the selected project agent.

The failure persisted after an IDE reconnect and a new terminal. Recreating
the container does not address this socket-lifetime mismatch. Adding another
key to the agent does not repair a missing socket either.

### What works and the temporary alternative

The owner verified these paths locally:

- VSCode in `ide-only` mode opens and reconnects, with no forwarded agent in its
  integrated terminal. This mode intentionally provides no host Git credentials.
- Fresh OpenSSH connections and direct `devpod ssh` connections in
  `project-key` mode expose exactly the selected project identity.
- An interactive `devpod ssh` session retains access to that identity while
  the session stays open.

For terminal-only access, run `devpod ssh WORKSPACE` on the host, then
`ssh-add -l` inside that live session. It should list exactly one identity,
matching `paranoid-podman devpod audit WORKSPACE` on the host. Run operations
requiring the project key in that session; this does not repair VSCode's Git
integration or its other terminals.

Do not copy private keys into the container, select an arbitrary socket, or
enable forwarding of the normal host agent as a workaround.

### Scope and follow-up

The wrapper remains responsible for selecting and isolating credentials, not
for maintaining or distributing a patched OpenSSH, DevPod, or IDE extension.
Work on this VSCode issue is postponed; wrapper updates must not modify the
installed extension or weaken agent isolation.

The [real-helper tests](tests/devpod/test_ssh_transport.py) reproduce the
failure and validate a held-session mechanism with synthetic keys; they do
not establish that an IDE integration works.

Revisit after an upstream change or a supported native integration becomes
available. Acceptance must cover IDE Git authentication, reconnect to a reused
IDE server, concurrent sessions, disconnect and mode-change cleanup, and
continued isolation from ambient host identities.

For configuration and source references, see
[DevPod credential isolation](docs/devpod-credentials.md#codium-agent-socket-troubleshooting).
