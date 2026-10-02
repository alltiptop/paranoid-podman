# Explicit access to a host loopback service

Rootless containers have their own loopback interface. A host service bound to
`127.0.0.1` is not normally reachable through `host.containers.internal` with
Podman's default pasta configuration. Renaming the host alias does not change
where the service listens.

For a deliberate connection to one such service, use the standard Podman option
`--network=pasta:-T,PORT`, replacing `PORT` with its TCP port. From the container,
connect to `127.0.0.1:PORT`. The port is supplied by the caller, never inferred
from a project, image, URL, or process running on the host.

```sh
podman run --rm --network="pasta:-T,${HOST_SERVICE_PORT}" IMAGE COMMAND
```

For Compose, put the same value in the service configuration:

```yaml
services:
  app:
    image: example/app
    network_mode: "pasta:-T,${HOST_SERVICE_PORT}"
```

Set `HOST_SERVICE_PORT` explicitly in the shell or project `.env`. This is a
Podman/pasta feature; the supported Compose provider is podman-compose. It does
not use a shared host network namespace or start a separate proxy service.
Pasta's `-T` uses loopback automatically and does not accept a listening address.

## Policy boundary

The guard accepts exactly `pasta:-T,PORT`, with one decimal port from 1 through
65535 and no leading zeroes. The same parser is used by direct run/create and
Compose. Lists, ranges, port remapping, automatic discovery, UDP forwards,
extra pasta arguments, and general host-loopback or gateway mapping are denied.
Direct invocations cannot combine this mode with another `--network` or `--net`;
Compose cannot combine it with service-level `networks`.

Ordinary containers keep their existing network defaults. Opting in makes the
selected host TCP service accessible to every process in that container,
including any service later bound to that same port. Both IPv4 and IPv6 loopback
may be forwarded by pasta. Choose the port of a service the container is trusted
to use. This is an explicit expansion of that container's network access, not a
claim that exposing a service has no security consequences.

The container keeps its separate network namespace and existing user,
capability, seccomp, and `no-new-privileges` policy. This option does not publish
the host service on a LAN address. It also does not restrict normal outbound
network access or replace the host service's authentication.

## Verification

Build the [minimal Chromium fixture](../tests/fixtures/chromium/README.md), then
run `scripts/test.sh loopback` (default image:
`localhost/paranoid-podman-chromium-test:dev`; override with
`PARANOID_PODMAN_CHROMIUM_IMAGE`). This explicit integration test starts two
temporary host HTTP listeners on randomly assigned loopback ports and two
temporary containers through the source guard. It compares default pasta with
the explicit forward, verifies that the second host port stays unreachable and
the forward does not listen on the container's non-loopback interface, and
renders the selected service in sandboxed Chromium. It checks zero capabilities,
seccomp, and `no-new-privileges`. It uses no pulls, host mounts, or published ports.

References: [Podman network options](https://docs.podman.io/en/latest/markdown/podman-run.1.html#network-mode-net),
[pasta TCP namespace forwarding](https://passt.top/builds/latest/web/passt.1.html).
