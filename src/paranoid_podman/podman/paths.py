"""Project path and host source scope checks using filesystem metadata."""

from pathlib import Path

from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.common.paths import host_source_rejection
from paranoid_podman.podman.errors import reject


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def is_host_path_source(value: str) -> bool:
    return value.startswith(("/", "./", "../")) or value in {".", ".."}


def project_directory() -> Path:
    try:
        directory = Path.cwd().resolve(strict=True)
    except (OSError, RuntimeError):
        reject(
            "blocked invalid current project directory",
            category=ViolationCategory.INPUT,
        )
    if not directory.is_dir():
        reject(
            "blocked invalid current project directory",
            category=ViolationCategory.INPUT,
        )
    return directory


def validate_host_source_scope(source: Path, invocation_dir: Path) -> None:
    reason = host_source_rejection(source, invocation_dir)
    if reason is not None:
        reject(f"blocked {reason}", category=ViolationCategory.MOUNT)
