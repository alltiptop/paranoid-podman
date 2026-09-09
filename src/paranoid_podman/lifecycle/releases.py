"""Create, verify, activate, and remove immutable releases."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from paranoid_podman.lifecycle import launchers as lifecycle_launchers
from paranoid_podman.lifecycle import metadata as lifecycle_metadata
from paranoid_podman.lifecycle import paths as lifecycle_paths
from paranoid_podman.lifecycle import reporting as lifecycle_reporting
from paranoid_podman.lifecycle.artifacts import Delivery
from paranoid_podman.lifecycle.errors import LifecycleError, fail
from paranoid_podman.lifecycle.integrity import verify_inventory
from paranoid_podman.lifecycle.wheel_release import create_wheel_release


def release_manifest(release: Path) -> dict[str, Any]:
    manifest_path = release / ".manifest.json"
    manifest = lifecycle_metadata.read_json(manifest_path)
    lifecycle_metadata.validate_metadata_header(manifest, manifest_path)
    if manifest.get("release") != release.name:
        fail(f"release metadata does not match its directory: {release}")
    files = manifest.get("files")
    if not isinstance(files, dict):
        fail(f"invalid release file list: {manifest_path}")
    for relative_path, expected_hash in files.items():
        if not isinstance(relative_path, str) or not isinstance(expected_hash, str):
            fail(f"invalid release file entry: {manifest_path}")
        candidate = release / relative_path
        if candidate.is_absolute() and not lifecycle_paths.is_within(
            candidate, release
        ):
            fail(f"unsafe release file entry: {manifest_path}")
        if ".." in Path(relative_path).parts or Path(relative_path).is_absolute():
            fail(f"unsafe release file entry: {manifest_path}")
    return manifest


def verify_release(release: Path) -> dict[str, Any]:
    manifest = release_manifest(release)
    verify_inventory(release, manifest)
    return manifest


def create_release(
    install_root: Path,
    providers: dict[str, str],
    installation_id: str,
    dry_run: bool,
    delivery: Delivery,
) -> Path:
    release = create_wheel_release(
        install_root,
        providers,
        installation_id,
        delivery,
        lifecycle_launchers.release_launcher(providers, installation_id),
        dry_run,
    )
    lifecycle_reporting.report(
        f"{'would prepare' if dry_run else 'verified'} wheel release {release}"
    )
    return release


def current_release(install_root: Path) -> Path:
    current = install_root / "current"
    if not current.is_symlink():
        fail("installed current-release link is missing or invalid")
    try:
        release = current.resolve(strict=True)
    except (OSError, RuntimeError):
        fail("installed current-release link is broken")
    releases_directory = (install_root / "releases").resolve(strict=True)
    if release.parent != releases_directory:
        fail("installed current-release link points outside the installation")
    verify_release(release)
    return release


def switch_current(install_root: Path, release: Path, dry_run: bool) -> None:
    current = install_root / "current"
    lifecycle_reporting.report(f"point {current} to releases/{release.name}")
    if dry_run:
        return
    if current.exists() and not current.is_symlink():
        fail("refusing to replace a non-symlink current path")
    temporary = install_root / f".current-{os.getpid()}"
    try:
        temporary.symlink_to(Path("releases") / release.name)
        os.replace(temporary, current)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def remove_release(release: Path, dry_run: bool) -> bool:
    try:
        manifest = verify_release(release)
    except LifecycleError as error:
        lifecycle_reporting.report(f"leave release in place: {error}")
        return False
    lifecycle_reporting.report(f"remove verified release {release}")
    if dry_run:
        return True
    files = sorted(
        (
            release / relative_path
            for relative_path in [*manifest["files"], *manifest["symlinks"]]
        ),
        key=lambda item: len(item.parts),
        reverse=True,
    )
    for path in files:
        path.unlink()
    (release / ".manifest.json").unlink()
    for directory in sorted(
        (path for path in release.rglob("*") if path.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass
    try:
        release.rmdir()
    except OSError:
        lifecycle_reporting.report(
            f"leave non-empty release directory in place: {release}"
        )
        return False
    return True
