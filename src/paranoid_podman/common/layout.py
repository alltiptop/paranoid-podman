"""Locate trusted source/installed entry points without searching the caller's cwd."""

import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def source_root() -> Path | None:
    """Read only the package's own ancestry to identify a source checkout."""
    parent = PACKAGE_ROOT.parent
    root = parent.parent
    if (
        parent.name == "src"
        and (root / "VERSION").is_file()
        and (root / "bin").is_dir()
    ):
        return root
    return None


def wrapper_directory() -> Path:
    root = source_root()
    return root / "bin" if root is not None else Path(sys.prefix) / "bin"


def command_arguments(command: str) -> list[str]:
    root = source_root()
    if root is not None:
        return [sys.executable, "-B", str(root / "bin" / command)]
    return [sys.executable, "-I", "-B", "-m", "paranoid_podman", command]
