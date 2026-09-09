"""Validate, create, or select repository-scoped SSH identities."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

from paranoid_podman.devpod import errors as devpod_errors
from paranoid_podman.devpod import files as devpod_files
from paranoid_podman.devpod import models as devpod_models
from paranoid_podman.devpod import paths as devpod_paths
from paranoid_podman.devpod import process as devpod_process

PUBLIC_KEY_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._+-]{0,127}$")
PUBLIC_KEY_DATA_PATTERN = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


def key_fingerprint(private_key: Path) -> str:
    result = devpod_process.run_checked(
        [
            devpod_process.required_tool("ssh-keygen"),
            "-E",
            "sha256",
            "-lf",
            str(private_key),
        ]
    )
    fields = result.stdout.strip().split()
    if len(fields) < 2 or not fields[1].startswith("SHA256:"):
        devpod_errors.fail("ssh-keygen returned an unrecognized key fingerprint")
    return fields[1]


def validate_private_key(path: Path, *, reject_general_identity: bool) -> Path:
    if path.is_symlink():
        devpod_errors.fail("selected private key must not itself be a symbolic link")
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except (OSError, RuntimeError) as error:
        raise devpod_errors.DevPodGuardError(
            "selected private key does not exist"
        ) from error
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        devpod_errors.fail(
            "selected private key must be a regular file owned by the current user"
        )
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        devpod_errors.fail(
            "selected private key permissions must not allow group or other access"
        )
    if reject_general_identity and resolved.parent == devpod_paths.user_home() / ".ssh":
        devpod_errors.fail(
            "refusing a general identity from the root of ~/.ssh; choose a "
            "repository-scoped key stored in its own directory"
        )
    key_fingerprint(resolved)
    return resolved


def managed_identity(workspace: devpod_models.Workspace) -> Path | None:
    directory = devpod_paths.project_key_directory(workspace)
    if not directory.exists():
        return None
    if directory.is_symlink() or not directory.is_dir():
        devpod_errors.fail(f"invalid managed project-key directory: {directory}")
    candidates = [
        path for path in directory.iterdir() if path.name in {"id_ed25519", "selected"}
    ]
    if not candidates:
        return None
    if len(candidates) != 1:
        devpod_errors.fail(f"multiple managed identities found for {workspace.name}")
    candidate = candidates[0]
    if candidate.is_symlink():
        try:
            target = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise devpod_errors.DevPodGuardError(
                "managed project-key link is broken"
            ) from error
        validate_private_key(target, reject_general_identity=True)
        return target
    return validate_private_key(candidate, reject_general_identity=False)


def managed_public_key(workspace: devpod_models.Workspace) -> Path | None:
    directory = devpod_paths.project_key_directory(workspace)
    for name in ("id_ed25519.pub", "selected.pub"):
        candidate = directory / name
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def create_project_key(workspace: devpod_models.Workspace) -> Path:
    directory = devpod_paths.project_key_directory(workspace)
    devpod_paths.ensure_private_directory(directory)
    private_key = directory / "id_ed25519"
    if any(directory.iterdir()):
        devpod_errors.fail(
            f"project-key directory is not empty: {directory}; inspect it and "
            "use the existing-key option instead of overwriting files"
        )
    comment = f"paranoid-podman:{workspace.context}/{workspace.name}"
    result = subprocess.run(  # nosec B603
        [
            devpod_process.required_tool("ssh-keygen"),
            "-t",
            "ed25519",
            "-a",
            "100",
            "-f",
            str(private_key),
            "-C",
            comment,
        ],
        check=False,
    )
    if result.returncode != 0:
        devpod_errors.fail("ssh-keygen did not create the project key")
    return validate_private_key(private_key, reject_general_identity=False)


def select_project_key(workspace: devpod_models.Workspace, source: str) -> Path:
    if any(character in source for character in ("\x00", "\n", "\r")):
        devpod_errors.fail(
            "selected private-key path contains an invalid control character"
        )
    selected = validate_private_key(
        devpod_paths.normalize_path(source), reject_general_identity=True
    )
    directory = devpod_paths.project_key_directory(workspace)
    devpod_paths.ensure_private_directory(directory)
    link = directory / "selected"
    if any(directory.iterdir()):
        devpod_errors.fail(
            f"project-key directory is not empty: {directory}; no file was overwritten"
        )
    public_result = devpod_process.run_checked(
        [devpod_process.required_tool("ssh-keygen"), "-y", "-f", str(selected)]
    )
    public_key = validated_public_key(public_result.stdout)
    devpod_files.atomic_write_text(directory / "selected.pub", public_key + "\n", 0o644)
    devpod_files.atomic_symlink(selected, link)
    return selected


def validated_public_key(output: str) -> str:
    if any(character in output for character in ("\x00", "\r")):
        devpod_errors.fail("ssh-keygen returned an unrecognized public key")
    lines = output.strip().splitlines()
    fields = lines[0].split() if len(lines) == 1 else []
    if (
        len(fields) < 2
        or PUBLIC_KEY_TYPE_PATTERN.fullmatch(fields[0]) is None
        or PUBLIC_KEY_DATA_PATTERN.fullmatch(fields[1]) is None
    ):
        devpod_errors.fail("ssh-keygen returned an unrecognized public key")
    return " ".join(fields[:2])
