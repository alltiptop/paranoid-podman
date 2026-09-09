"""Private lazy dispatcher used by isolated wheel invocations."""

import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("paranoid-podman: an entry-point name is required", file=sys.stderr)
        return 126
    entry = sys.argv.pop(1)
    if entry == "docker" and sys.argv[1:2] == ["compose"]:
        sys.argv.pop(1)
        entry = "compose-guard"
    if entry in {"podman", "docker"}:
        from paranoid_podman.podman.cli import main as run

        return run()
    if entry in {"compose-guard", "podman-compose", "docker-compose"}:
        from paranoid_podman.compose.cli import main as run

        return run()
    if entry == "devpod":
        from paranoid_podman.devpod.cli import run_devpod
        from paranoid_podman.devpod.errors import guarded_main

        return guarded_main(run_devpod)
    if entry == "paranoid-podman":
        from paranoid_podman.devpod.errors import guarded_main
        from paranoid_podman.management.cli import management_main

        return guarded_main(management_main)
    if entry == "lifecycle":
        from paranoid_podman.lifecycle.cli import entrypoint

        return entrypoint()
    print("paranoid-podman: invalid entry-point name", file=sys.stderr)
    return 126


if __name__ == "__main__":
    raise SystemExit(main())
