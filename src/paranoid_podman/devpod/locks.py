"""Private runtime directories and bounded advisory locks."""

from __future__ import annotations

import fcntl
import hashlib
import os
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from paranoid_podman.devpod import errors as devpod_errors
from paranoid_podman.devpod import paths as devpod_paths
from paranoid_podman.devpod import settings as devpod_settings


def runtime_project_root() -> Path:
    raw_root = os.environ.get("XDG_RUNTIME_DIR")
    if not raw_root:
        devpod_errors.fail(
            "XDG_RUNTIME_DIR is required for a stable protected SSH-agent socket; "
            "log in through a normal user session and retry"
        )
    root = devpod_paths.normalize_path(raw_root)
    try:
        metadata = root.stat()
    except OSError as error:
        raise devpod_errors.DevPodGuardError(
            "XDG_RUNTIME_DIR does not exist"
        ) from error
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        devpod_errors.fail("XDG_RUNTIME_DIR is not a user-owned directory")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        devpod_errors.fail("XDG_RUNTIME_DIR must not allow group or other access")
    directory = root / devpod_settings.PROJECT_NAME
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory_metadata = directory.lstat()
    if (
        stat.S_ISLNK(directory_metadata.st_mode)
        or not stat.S_ISDIR(directory_metadata.st_mode)
        or directory_metadata.st_uid != os.getuid()
    ):
        devpod_errors.fail(
            "runtime project path must be a user-owned non-symlink directory"
        )
    directory.chmod(0o700)
    return directory


@contextmanager
def runtime_lock(scope: str, identity: str) -> Iterator[None]:
    lock_directory = runtime_project_root() / "locks"
    lock_directory.mkdir(mode=0o700, exist_ok=True)
    metadata = lock_directory.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        devpod_errors.fail(
            "runtime lock path must be a user-owned non-symlink directory"
        )
    lock_directory.chmod(0o700)
    digest = hashlib.sha256(identity.encode()).hexdigest()
    lock_path = lock_directory / f"{scope}-{digest}.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise devpod_errors.DevPodGuardError(
            "cannot open the protected runtime lock"
        ) from error
    try:
        os.fchmod(descriptor, 0o600)
        lock_metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(lock_metadata.st_mode)
            or lock_metadata.st_uid != os.getuid()
        ):
            devpod_errors.fail("runtime lock is not a user-owned regular file")
        deadline = time.monotonic() + devpod_settings.LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    devpod_errors.fail(
                        "timed out waiting for another paranoid-podman process"
                    )
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
