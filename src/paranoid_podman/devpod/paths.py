"""User, project-key, and wrapper paths; validates directory metadata."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

from paranoid_podman.common.layout import wrapper_directory
from paranoid_podman.devpod import errors as devpod_errors
from paranoid_podman.devpod import models as devpod_models


def user_home() -> Path:
    value = os.environ.get("HOME")
    if not value:
        devpod_errors.fail("HOME is required for DevPod integration")
    home = Path(value).expanduser()
    if not home.is_absolute():
        devpod_errors.fail("HOME must be an absolute path")
    return home.resolve(strict=False)


def normalize_path(value: str | Path, *, base: Path | None = None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base or Path.cwd()) / path
    return path.resolve(strict=False)


def default_ssh_config() -> Path:
    return user_home() / ".ssh/config"


def safe_component(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.") or "workspace"
    digest = hashlib.sha256(value.encode()).hexdigest()[:8]
    return f"{slug[:80]}--{digest}"


def project_key_directory(workspace: devpod_models.Workspace) -> Path:
    context_namespace = f"{workspace.devpod_home or '<default>'}\0{workspace.context}"
    return (
        user_home()
        / ".ssh/paranoid-podman"
        / safe_component(context_namespace)
        / safe_component(workspace.name)
    )


def ensure_private_directory(path: Path) -> None:
    ssh_directory = user_home() / ".ssh"
    ssh_directory.mkdir(mode=0o700, exist_ok=True)
    ssh_metadata = ssh_directory.lstat()
    if (
        stat.S_ISLNK(ssh_metadata.st_mode)
        or not stat.S_ISDIR(ssh_metadata.st_mode)
        or ssh_metadata.st_uid != os.getuid()
        or stat.S_IMODE(ssh_metadata.st_mode) & 0o077
    ):
        devpod_errors.fail(
            "~/.ssh must be a private, user-owned, non-symlink directory"
        )
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    current = path
    root = user_home() / ".ssh/paranoid-podman"
    while current == root or root in current.parents:
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            devpod_errors.fail(f"managed SSH path is not a real directory: {current}")
        if metadata.st_uid != os.getuid():
            devpod_errors.fail(
                f"managed SSH directory is not owned by the current user: {current}"
            )
        current.chmod(0o700)
        if current == root:
            break
        current = current.parent


def wrapper_path() -> Path:
    configured = os.environ.get("PARANOID_PODMAN_DEVPOD_WRAPPER")
    if configured:
        path = normalize_path(configured)
    else:
        path = wrapper_directory() / "devpod"
    if not path.is_file() or not os.access(path, os.X_OK):
        devpod_errors.fail("DevPod wrapper path is missing or not executable")
    return path
