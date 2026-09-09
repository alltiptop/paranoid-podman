"""Discover and inspect the external DevPod executable."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from paranoid_podman.devpod import errors as devpod_errors
from paranoid_podman.devpod import settings as devpod_settings


def external_executable(value: str, own_entry_point: Path | None = None) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        devpod_errors.fail("the real DevPod executable must be an absolute path")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise devpod_errors.DevPodGuardError(
            "the real DevPod executable does not exist"
        ) from error
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        devpod_errors.fail("the real DevPod path is not an executable file")
    if own_entry_point is not None:
        try:
            if os.path.samefile(resolved, own_entry_point):
                devpod_errors.fail(
                    "the configured real DevPod executable points back to the wrapper"
                )
        except OSError:
            pass
    return resolved


def discover_real_devpod(own_entry_point: Path) -> Path:
    configured = os.environ.get("PARANOID_PODMAN_REAL_DEVPOD")
    if configured:
        return external_executable(configured, own_entry_point)
    for raw_directory in os.environ.get("PATH", "").split(os.pathsep):
        if not raw_directory or not Path(raw_directory).is_absolute():
            continue
        candidate = Path(raw_directory) / "devpod"
        try:
            return external_executable(str(candidate), own_entry_point)
        except devpod_errors.DevPodGuardError:
            continue
    devpod_errors.fail(
        "could not find the real DevPod executable; install with --devpod "
        "or set PARANOID_PODMAN_REAL_DEVPOD to a reviewed absolute path"
    )


def parse_devpod_version(output: str) -> tuple[int, int, int]:
    match = devpod_settings.DEVPOD_VERSION_PATTERN.fullmatch(output.strip())
    if match is None:
        devpod_errors.fail("could not parse the DevPod version")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def validate_devpod_version(executable: Path) -> str:
    try:
        result = subprocess.run(  # nosec B603
            [str(executable), "version"],
            cwd="/",
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise devpod_errors.DevPodGuardError(
            "failed to inspect the DevPod provider"
        ) from error
    if result.returncode != 0:
        devpod_errors.fail("DevPod provider self-check failed")
    version = parse_devpod_version(result.stdout)
    return ".".join(map(str, version))
