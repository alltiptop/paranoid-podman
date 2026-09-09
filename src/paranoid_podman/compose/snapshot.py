"""Write private reviewed snapshots and construct execution arguments."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.compose.dotenv import copy_service_env_files
from paranoid_podman.compose.errors import reject
from paranoid_podman.compose.models import Invocation
from paranoid_podman.compose.settings import REAL_PODMAN
from paranoid_podman.compose.yaml_support import yaml


def escape_interpolation(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("$", "$$")
    if isinstance(value, list):
        return [escape_interpolation(item) for item in value]
    if isinstance(value, dict):
        return {key: escape_interpolation(item) for key, item in value.items()}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    reject(
        "blocked resolved configuration: unsupported scalar type",
        category=ViolationCategory.UNSUPPORTED,
    )


def write_snapshot(directory: Path, model: dict[str, Any]) -> Path:
    if yaml is None:  # Kept explicit for type checkers and direct unit calls.
        reject(
            "PyYAML is required for structured Compose validation",
            category=ViolationCategory.INSTALLATION,
        )
    copy_service_env_files(directory, model)
    snapshot = directory / "compose.snapshot.yaml"
    rendered = yaml.safe_dump(
        escape_interpolation(model),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=True,
    )
    descriptor = os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(rendered)
    return snapshot


def execution_arguments(invocation: Invocation, snapshot: Path) -> list[str]:
    arguments = [
        "--podman-path",
        REAL_PODMAN,
        *invocation.execution_globals,
        "--env-file",
        "/dev/null",
        "-f",
        str(snapshot),
        "-p",
        invocation.project_name,
        invocation.command,
    ]
    arguments.extend(invocation.command_args)
    return arguments
