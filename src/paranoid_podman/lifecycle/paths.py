"""User installation paths and protected-prefix validation."""

from __future__ import annotations

import os
from pathlib import Path

from paranoid_podman.common.layout import source_root
from paranoid_podman.lifecycle import settings as lifecycle_settings
from paranoid_podman.lifecycle.errors import fail


def normalize_path(value: str | Path) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve(strict=False)


def is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


def default_paths() -> tuple[Path, Path]:
    user_home_value = os.environ.get("HOME")
    if not user_home_value:
        fail("HOME is required when installation paths are not explicit")
    user_home_directory = normalize_path(user_home_value)
    binary_directory = normalize_path(
        os.environ.get("XDG_BIN_HOME", user_home_directory / ".local/bin")
    )
    data_directory = normalize_path(
        os.environ.get("XDG_DATA_HOME", user_home_directory / ".local/share")
    )
    return binary_directory, data_directory / lifecycle_settings.PROJECT_NAME


def validate_install_paths(binary_directory: Path, install_root: Path) -> None:
    forbidden_roots = {Path("/"), normalize_path(lifecycle_settings.PROJECT_ROOT)}
    if install_root in forbidden_roots:
        fail("refusing an unsafe installation root")
    for candidate in (binary_directory, install_root):
        if source_root() is not None and (
            is_within(lifecycle_settings.PROJECT_ROOT, candidate)
            or is_within(candidate, lifecycle_settings.PROJECT_ROOT)
        ):
            fail("installation paths must be separate from the source checkout")
    if (
        binary_directory == Path("/")
        or is_within(binary_directory, install_root)
        or is_within(install_root, binary_directory)
    ):
        fail("binary directory and installation root must be separate")
    if (
        install_root.parent == install_root
        or binary_directory.parent == binary_directory
    ):
        fail("refusing a filesystem root as an installation path")
    for candidate in (binary_directory, install_root):
        if any(
            candidate == prefix or is_within(candidate, prefix)
            for prefix in lifecycle_settings.SYSTEM_PREFIXES
        ):
            fail("installation paths must be user-owned, not system prefixes")
