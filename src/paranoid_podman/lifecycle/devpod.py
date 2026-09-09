"""Discover DevPod context SSH paths for installation removal."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from paranoid_podman.lifecycle import paths as lifecycle_paths
from paranoid_podman.lifecycle import providers as lifecycle_providers
from paranoid_podman.lifecycle.errors import LifecycleError, fail


def devpod_json_output(executable: Path, arguments: list[str]) -> Any:
    try:
        result = subprocess.run(  # nosec B603
            [str(executable), *arguments],
            cwd="/",
            env=lifecycle_providers.sanitized_provider_environment(),
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        fail("failed to inspect DevPod context metadata")
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) > 1024 * 1024:
        fail("DevPod context metadata could not be read safely")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        fail("DevPod returned invalid context metadata")


def devpod_ssh_config_paths(
    executable: Path,
    home: Path,
    explicit_paths: tuple[Path, ...] = (),
) -> tuple[Path, ...]:
    paths = {lifecycle_paths.normalize_path(home / ".ssh/config"), *explicit_paths}
    if explicit_paths:
        return tuple(sorted(paths, key=str))
    try:
        contexts = devpod_json_output(
            executable, ["context", "list", "--output", "json"]
        )
    except LifecycleError as error:
        fail(
            f"{error}; restore the recorded DevPod provider or pass every custom "
            "path with --devpod-ssh-config"
        )
    if not isinstance(contexts, list) or len(contexts) > 256:
        fail("DevPod returned invalid context metadata")
    for item in contexts:
        if not isinstance(item, dict):
            fail("DevPod returned invalid context metadata")
        name = item.get("name")
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name) is None
        ):
            fail("DevPod returned an invalid context name")
        options = devpod_json_output(
            executable,
            ["context", "options", "--context", name, "--output", "json"],
        )
        if not isinstance(options, dict):
            fail("DevPod returned invalid context options")
        ssh_option = options.get("SSH_CONFIG_PATH")
        if not isinstance(ssh_option, dict):
            continue
        configured = ssh_option.get("value", ssh_option.get("default"))
        if isinstance(configured, str) and configured:
            paths.add(lifecycle_paths.normalize_path(configured))
    return tuple(sorted(paths, key=str))
