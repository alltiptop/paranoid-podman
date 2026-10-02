# Minimal Chromium test image

This fixture is independent of application repositories, host services, and
personal container images. It contains Node.js 24, `playwright-core` pinned to
1.63.0, Chromium headless shell, and the browser's system dependencies. It does
not install Firefox, WebKit, or an application server. Its default command renders
an inline HTML page with sandboxed Chromium and checks WebGL2 and a PNG screenshot.

From the repository root, explicitly build the image through the source guard:

```sh
bin/podman build -t localhost/paranoid-podman-chromium-test:dev tests/fixtures/chromium
```

The build downloads the public Node.js base image, npm package, browser binary,
and OS dependencies. Only this fixture directory is sent as the build context;
the ignore file allows only the Dockerfile and smoke script.

Run the standalone example:

```sh
bin/podman run --rm --init --network=none \
  --user pwuser --security-opt seccomp=chromium --shm-size=256m \
  localhost/paranoid-podman-chromium-test:dev
```

Run the stronger integration checks:

```sh
scripts/test.sh chromium
scripts/test.sh loopback
```

Both tests use this image by default. To use an equivalent independently built
image, set `PARANOID_PODMAN_CHROMIUM_IMAGE`. It must provide `pwuser`, Node.js,
`playwright-core` resolvable from its working directory, and the corresponding
Chromium headless shell. Tests never build or pull images automatically.

The Chromium test uses inline HTML with networking disabled. The loopback test
creates its own temporary HTTP servers on randomly assigned host loopback ports;
it needs no existing frontend or API. Both use temporary containers with no host
mounts or published ports. Use a disposable rootless Podman environment for these
explicit integration tests.

The package and browser versions must match. Update the pinned package version
in the Dockerfile, rebuild, and run both tests when upgrading Playwright. See
[Playwright's image requirements](https://playwright.dev/docs/docker#build-your-own-image)
and [headless shell installation](https://playwright.dev/docs/browsers#chromium-headless-shell).
