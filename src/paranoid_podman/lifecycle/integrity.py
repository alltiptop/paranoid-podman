"""Record and verify wheel release files without traversing symlink targets."""

import os
import re
import stat
from pathlib import Path
from typing import Any

from paranoid_podman.lifecycle.artifacts import file_digest
from paranoid_podman.lifecycle.errors import fail


def release_inventory(release: Path) -> dict[str, Any]:
    files: dict[str, str] = {}
    links: dict[str, str] = {}
    modes: dict[str, int] = {}
    if release.is_symlink() or not release.is_dir():
        fail("wheel release must be a regular directory")
    for directory, subdirectories, names in os.walk(release, followlinks=False):
        for name in sorted([*subdirectories, *names]):
            path = Path(directory) / name
            relative = path.relative_to(release).as_posix()
            if relative == ".manifest.json":
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                links[relative] = os.readlink(path)
            elif stat.S_ISREG(info.st_mode):
                files[relative] = file_digest(path)
                modes[relative] = stat.S_IMODE(info.st_mode)
            elif stat.S_ISDIR(info.st_mode):
                modes[relative] = stat.S_IMODE(info.st_mode)
            else:
                fail(f"unsupported wheel release file type: {path}")
    return {"files": files, "symlinks": links, "modes": modes}


def validate_venv_links(release: Path, base_python: Path) -> None:
    """Only venv's interpreter and lib64 aliases may refer outside regular files."""
    for relative in release_inventory(release)["symlinks"]:
        link = release / relative
        if relative == "runtime/lib64" and os.readlink(link) == "lib":
            continue
        if (
            link.parent == release / "runtime/bin"
            and (
                link.name in {"python", "𝜋thon"}
                or re.fullmatch(r"python3(?:\.\d+)?", link.name)
            )
            and link.resolve(strict=True) == base_python.resolve(strict=True)
        ):
            continue
        fail(f"unapproved runtime symlink: {relative}")


def verify_inventory(release: Path, manifest: dict[str, Any]) -> None:
    actual = release_inventory(release)
    for field in ("files", "symlinks", "modes"):
        if (
            not isinstance(manifest.get(field), dict)
            or manifest[field] != actual[field]
        ):
            fail(f"installed wheel release {field} were modified: {release}")


def remove_new_tree(root: Path) -> None:
    """Remove only a newly claimed release, never following its symlinks."""
    if root.is_symlink():
        root.unlink()
        return
    for directory, subdirectories, files in os.walk(
        root, topdown=False, followlinks=False
    ):
        parent = Path(directory)
        for name in files:
            (parent / name).unlink()
        for name in subdirectories:
            path = parent / name
            path.unlink() if path.is_symlink() else path.rmdir()
    root.rmdir()
