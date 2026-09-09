"""File fingerprints and atomic lifecycle metadata writes."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from paranoid_podman.lifecycle.errors import LifecycleError, fail


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_fingerprint(path: Path) -> dict[str, str]:
    file_stat = path.lstat()
    if stat.S_ISREG(file_stat.st_mode):
        return {"kind": "file", "sha256": sha256_file(path)}
    if stat.S_ISLNK(file_stat.st_mode):
        target = os.readlink(path).encode("utf-8", errors="surrogateescape")
        return {"kind": "symlink", "sha256": sha256_bytes(target)}
    fail(f"refusing unsupported existing path type: {path}")


def fingerprint_matches(path: Path, expected: dict[str, str]) -> bool:
    try:
        return path_fingerprint(path) == expected
    except (LifecycleError, OSError):
        return False


def atomic_write(
    path: Path, content: bytes, mode: int, *, replace: bool = True
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        if replace:
            os.replace(temporary_path, path)
        else:
            # Claim a new launcher without overwriting a concurrently created file.
            os.link(temporary_path, path)
            temporary_path.unlink()
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    content = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write(path, content, 0o600)
