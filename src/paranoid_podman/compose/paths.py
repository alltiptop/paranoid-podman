"""Compose input resolution and project scope; reads filesystem metadata."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.common.paths import host_source_rejection
from paranoid_podman.compose.errors import reject
from paranoid_podman.compose.settings import DEFAULT_COMPOSE_FILES


def guarded_project_name(project_dir: Path) -> str:
    base = re.sub(r"[^-_a-z0-9]", "", project_dir.name.lower()) or "project"
    digest = hashlib.sha256(os.fsencode(project_dir)).hexdigest()[:10]
    return f"{base[:40]}-{digest}"


def _resolve_input_file(raw_value: str, cwd: Path, description: str) -> Path:
    if raw_value == "-":
        reject(
            f"standard-input {description} are not supported",
            category=ViolationCategory.POLICY,
        )
    candidate = Path(raw_value).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    try:
        if candidate.is_symlink():
            reject(
                f"a {description} is not a regular non-symlink file",
                category=ViolationCategory.POLICY,
            )
        resolved = candidate.resolve(strict=True)
        mode = resolved.lstat().st_mode
    except (OSError, RuntimeError):
        reject(
            f"a {description} is missing or cannot be resolved",
            category=ViolationCategory.INPUT,
        )
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        reject(
            f"a {description} is not a regular non-symlink file",
            category=ViolationCategory.POLICY,
        )
    return resolved


def _devcontainer_project_root(compose_file: Path) -> Path | None:
    for parent in compose_file.parents:
        if parent.name == ".devcontainer":
            return parent.parent
    return None


def _resolve_compose_inputs(
    raw_files: list[str], raw_env_file: str | None, cwd: Path
) -> tuple[list[Path], Path | None, Path]:
    if not raw_files:
        raw_files = [name for name in DEFAULT_COMPOSE_FILES if (cwd / name).is_file()]
    if not raw_files:
        reject(
            "no Compose file found; pass an explicit -f file",
            category=ViolationCategory.COMPOSE_FILE,
        )

    resolved_files = [
        _resolve_input_file(raw_file, cwd, "Compose input") for raw_file in raw_files
    ]
    env_file = None
    if raw_env_file is not None:
        env_file = _resolve_input_file(raw_env_file, cwd, "Compose env file")
        if env_file.name != ".env":
            reject(
                "the guarded global Compose env file must be named .env",
                category=ViolationCategory.POLICY,
            )

    root_inputs = [path.parent for path in resolved_files]
    if env_file is not None:
        root_inputs.append(env_file.parent)
    devcontainer_root = _devcontainer_project_root(resolved_files[0])
    if devcontainer_root is not None:
        root_inputs.append(devcontainer_root)
    try:
        project_dir = Path(os.path.commonpath(root_inputs)).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        reject(
            "Compose inputs do not have a usable common project directory",
            category=ViolationCategory.POLICY,
        )
    if host_source_rejection(project_dir, project_dir) is not None:
        reject(
            "Compose inputs resolve to an unsafe project directory",
            category=ViolationCategory.POLICY,
        )
    return resolved_files, env_file, project_dir


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
