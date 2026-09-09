"""Required external tools and bounded command execution."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from paranoid_podman.devpod import errors as devpod_errors


def run_checked(
    arguments: list[str],
    *,
    environment: dict[str, str] | None = None,
    capture_output: bool = True,
    timeout: int = 20,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(  # nosec B603
            arguments,
            env=environment,
            capture_output=capture_output,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise devpod_errors.DevPodGuardError(
            f"failed to run required command: {arguments[0]}"
        ) from error
    if result.returncode != 0:
        devpod_errors.fail(f"required command failed: {arguments[0]}")
    return result


def required_tool(name: str) -> str:
    value = shutil.which(name)
    if value is None or not Path(value).is_absolute():
        devpod_errors.fail(f"required tool is unavailable: {name}")
    return value
