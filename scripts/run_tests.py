"""Separate temporary-data local tests from explicitly selected integrations."""

import argparse
import os
import subprocess  # nosec B404
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = {
    "compose-provider": (
        "PARANOID_PODMAN_RUN_COMPOSE_PROVIDER_TESTS",
        "tests.compose.test_provider",
    ),
    "rootless": (
        "PARANOID_PODMAN_RUN_ROOTLESS_TESTS",
        "tests.podman.test_rootless",
    ),
    "ssh-agent": (
        "PARANOID_PODMAN_RUN_SSH_AGENT_TESTS",
        "tests.devpod.test_compatibility.DevPodCompatibilityTests."
        "test_real_dedicated_agent_contains_only_the_project_identity",
    ),
    "devpod-ssh": (
        "PARANOID_PODMAN_RUN_DEVPOD_SSH_TESTS",
        "tests.devpod.test_ssh_transport",
    ),
}


def local_environment(root: Path) -> dict[str, str]:
    """Use a fresh home/config and no inherited credentials or provider flags."""
    environment = {
        "PATH": os.pathsep.join((str(Path(sys.executable).parent), os.defpath)),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_NO_INDEX": "1",
    }
    for name in (
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "TMPDIR",
    ):
        directory = root / name.lower()
        directory.mkdir(mode=0o700)
        environment[name] = str(directory)
    for flag, _ in INTEGRATIONS.values():
        environment[flag] = "0"
    return environment


def run_local_tests(project: Path = PROJECT_ROOT) -> int:
    # An ambient TMPDIR may point into a personal tree. Own all runner data.
    with tempfile.TemporaryDirectory(prefix="pp-tests-", dir="/tmp") as temporary:
        environment = local_environment(Path(temporary))
        return subprocess.run(  # nosec B603
            [
                sys.executable,
                "-B",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-t",
                ".",
                "-v",
            ],
            cwd=project,
            env=environment,
            stdin=subprocess.DEVNULL,
            check=False,
            timeout=300,
        ).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", nargs="?", default="local", choices=("local", *INTEGRATIONS)
    )
    mode = parser.parse_args().mode
    if mode == "local":
        return run_local_tests()
    flag, test = INTEGRATIONS[mode]
    environment = {**os.environ, flag: "1", "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(  # nosec B603
        [sys.executable, "-B", "-m", "unittest", test, "-v"],
        cwd=PROJECT_ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        check=False,
        timeout=300,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
