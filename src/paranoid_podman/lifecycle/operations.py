"""Install, update, inspect, and remove with explicit rollback boundaries."""

from __future__ import annotations

import os
import secrets
import shlex
import sys
from pathlib import Path
from typing import Any

from paranoid_podman.lifecycle import devpod as lifecycle_devpod
from paranoid_podman.lifecycle import files as lifecycle_files
from paranoid_podman.lifecycle import launchers as lifecycle_launchers
from paranoid_podman.lifecycle import metadata as lifecycle_metadata
from paranoid_podman.lifecycle import paths as lifecycle_paths
from paranoid_podman.lifecycle import providers as lifecycle_providers
from paranoid_podman.lifecycle import releases as lifecycle_releases
from paranoid_podman.lifecycle import reporting as lifecycle_reporting
from paranoid_podman.lifecycle import settings as lifecycle_settings
from paranoid_podman.lifecycle import ssh_restore as lifecycle_ssh_restore
from paranoid_podman.lifecycle.artifacts import Delivery
from paranoid_podman.lifecycle.bootstrap import installation_delivery
from paranoid_podman.lifecycle.errors import LifecycleError, fail


def install(
    binary_directory: Path,
    install_root: Path,
    providers: dict[str, str],
    dry_run: bool,
    backup_existing: bool,
    *,
    delivery: Delivery | None = None,
) -> None:
    record_path = install_root / ".install.json"
    if record_path.exists() or record_path.is_symlink():
        fail("installation already exists; use update")
    if install_root.exists() and any(install_root.iterdir()):
        fail("installation root exists and is not empty")

    entry_points = lifecycle_launchers.desired_entry_points(providers)
    conflicts = [
        binary_directory / name
        for name in entry_points
        if (binary_directory / name).exists() or (binary_directory / name).is_symlink()
    ]
    if conflicts and not backup_existing:
        for target in conflicts:
            lifecycle_reporting.report(f"existing command: {target}")
        if dry_run:
            lifecycle_reporting.report(
                "installation will ask to back up existing commands"
            )
        elif not sys.stdin.isatty():
            fail(f"target already exists; use --backup-existing: {conflicts[0]}")
        elif (
            input("Back up these commands and restore them on uninstall? [y/N]: ")
            != "y"
        ):
            fail("installation cancelled; no files changed")
        backup_existing = True
    existing: dict[str, dict[str, str]] = {}
    for entry_point in entry_points:
        target = binary_directory / entry_point
        if target.exists() or target.is_symlink():
            if not backup_existing:
                fail(f"target already exists; use --backup-existing: {target}")
            existing[entry_point] = lifecycle_files.path_fingerprint(target)

    launcher_metadata: dict[str, dict[str, Any]] = {}
    installation_id = secrets.token_hex(32)
    for entry_point in entry_points:
        target = binary_directory / entry_point
        content = lifecycle_launchers.command_launcher(install_root, entry_point)
        item: dict[str, Any] = {"sha256": lifecycle_files.sha256_bytes(content)}
        if entry_point in existing:
            backup = install_root / "backups" / entry_point
            item["backup"] = existing[entry_point]
            lifecycle_reporting.report(f"back up {target} to {backup}")
        lifecycle_reporting.report(f"install launcher {target}")
        launcher_metadata[entry_point] = item

    with installation_delivery(delivery, dry_run) as prepared:
        if prepared is None:
            return
        release: Path | None = None
        installed_targets: list[Path] = []
        moved_backups: list[tuple[Path, Path]] = []
        try:
            release = lifecycle_releases.create_release(
                install_root, providers, installation_id, dry_run, prepared
            )
            lifecycle_releases.switch_current(install_root, release, dry_run)
            if dry_run:
                lifecycle_reporting.report("dry run complete; no files changed")
                return

            binary_directory.mkdir(parents=True, exist_ok=True, mode=0o755)
            if existing:
                (install_root / "backups").mkdir(
                    parents=True, exist_ok=True, mode=0o700
                )
            for entry_point in entry_points:
                target = binary_directory / entry_point
                if entry_point in existing:
                    if not lifecycle_files.fingerprint_matches(
                        target, existing[entry_point]
                    ):
                        fail(
                            f"command changed during installation; leaving it in place: {target}"
                        )
                    backup = install_root / "backups" / entry_point
                    os.replace(target, backup)
                    moved_backups.append((backup, target))
                content = lifecycle_launchers.command_launcher(
                    install_root, entry_point
                )
                lifecycle_files.atomic_write(target, content, 0o755, replace=False)
                installed_targets.append(target)

            record = {
                "binary_directory": str(binary_directory),
                "format": lifecycle_settings.MANIFEST_FORMAT,
                "install_root": str(install_root),
                "installation_id": installation_id,
                "launchers": launcher_metadata,
                "project": lifecycle_settings.PROJECT_NAME,
                "release": release.name,
                "version": prepared.project.version,
            }
            lifecycle_files.atomic_json(record_path, record)
        except BaseException:
            for target in reversed(installed_targets):
                expected = {
                    "kind": "file",
                    "sha256": launcher_metadata[target.name]["sha256"],
                }
                if lifecycle_files.fingerprint_matches(target, expected):
                    target.unlink()
            for backup, target in reversed(moved_backups):
                if not target.exists() and not target.is_symlink():
                    os.replace(backup, target)
            current = install_root / "current"
            if current.is_symlink():
                try:
                    if current.resolve(strict=True) == release:
                        current.unlink()
                except (OSError, RuntimeError):
                    pass
            if release is not None:
                try:
                    lifecycle_releases.remove_release(release, False)
                except OSError:
                    pass
            for directory in (
                install_root / "backups",
                install_root / "releases",
                install_root,
                binary_directory,
            ):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise

        lifecycle_reporting.report(f"installed {prepared.project.version}")
        lifecycle_reporting.report(
            f"Python: {providers['python']} ({providers['python_version']})"
        )
        lifecycle_reporting.report(
            f"real Podman: {providers['podman']} ({providers['podman_version']})"
        )
        lifecycle_reporting.report(
            f"Compose provider: {providers['compose']} ({providers['compose_version']})"
        )
        if "devpod" in providers:
            lifecycle_reporting.report(
                f"real DevPod: {providers['devpod']} ({providers['devpod_version']})"
            )
        path_entries = [
            lifecycle_paths.normalize_path(value)
            for value in os.environ.get("PATH", "").split(os.pathsep)
            if value
        ]
        if not path_entries or path_entries[0] != binary_directory:
            lifecycle_reporting.report("activate guarded commands in this shell:")
            lifecycle_reporting.report(
                f'export PATH={shlex.quote(str(binary_directory))}:"$PATH"'
            )
        policy = "guarded direct run/create and podman-compose 1.6.x"
        if "devpod" in providers:
            policy += "; DevPod credential isolation"
        lifecycle_reporting.report(f"policy: {policy}")
        command = shlex.join([str(install_root / "current/launch"), "lifecycle"])
        paths = shlex.join(
            ["--bindir", str(binary_directory), "--libdir", str(install_root)]
        )
        lifecycle_reporting.report(f"self-check: {command} status {paths}")
        lifecycle_reporting.report(f"uninstall: {command} uninstall --dry-run {paths}")


def update(
    requested_binary_directory: Path | None,
    install_root: Path,
    podman_value: str | None,
    compose_value: str | None,
    dry_run: bool,
    devpod_value: str | None = None,
    *,
    delivery: Delivery | None = None,
) -> None:
    record = lifecycle_metadata.install_record(install_root)
    binary_directory = lifecycle_metadata.verified_binary_directory(
        requested_binary_directory, record, install_root
    )
    previous_release = lifecycle_releases.current_release(install_root)
    previous_manifest = lifecycle_releases.verify_release(previous_release)
    installed_commands = lifecycle_metadata.verify_launchers(binary_directory, record)
    installation_id = lifecycle_metadata.installation_id_from_record(
        record, previous_manifest
    )
    previous_providers = previous_manifest.get("providers")
    if not isinstance(previous_providers, dict):
        fail("installed provider metadata is invalid")
    devpod_installed = "devpod" in installed_commands
    if devpod_value is not None and not devpod_installed:
        fail("enabling DevPod integration requires uninstall and a new install")
    previous_devpod = previous_providers.get("devpod") if devpod_installed else None
    allowed_devpod_paths = lifecycle_metadata.verified_managed_devpod_backup(
        install_root, record, previous_devpod
    )
    providers = lifecycle_providers.validate_providers(
        podman_value or previous_providers.get("podman"),
        compose_value or previous_providers.get("compose"),
        install_root,
        binary_directory,
        devpod_value or previous_devpod,
        allowed_devpod_paths,
    )
    with installation_delivery(delivery, dry_run) as prepared:
        if prepared is None:
            return
        release = lifecycle_releases.create_release(
            install_root, providers, installation_id, dry_run, prepared
        )
        if release == previous_release:
            lifecycle_reporting.report(f"{prepared.project.version} is already current")
            return

        lifecycle_reporting.report(f"update metadata to {release.name}")
        if dry_run:
            lifecycle_releases.switch_current(install_root, release, True)
            lifecycle_reporting.report("dry run complete; no files changed")
            return
        try:
            lifecycle_releases.switch_current(install_root, release, False)
            record["release"] = release.name
            record["version"] = prepared.project.version
            lifecycle_files.atomic_json(install_root / ".install.json", record)
        except BaseException:
            current = install_root / "current"
            try:
                if current.is_symlink() and current.resolve(strict=True) == release:
                    lifecycle_releases.switch_current(
                        install_root, previous_release, False
                    )
            except (LifecycleError, OSError, RuntimeError):
                pass
            try:
                lifecycle_releases.remove_release(release, False)
            except OSError:
                pass
            raise
        lifecycle_reporting.report(f"updated to {prepared.project.version}")


def uninstall(
    requested_binary_directory: Path | None,
    install_root: Path,
    dry_run: bool,
    explicit_devpod_ssh_configs: tuple[Path, ...] = (),
) -> None:
    record = lifecycle_metadata.install_record(install_root)
    binary_directory = lifecycle_metadata.verified_binary_directory(
        requested_binary_directory, record, install_root
    )
    launcher_metadata = record["launchers"]
    changed_launchers: list[Path] = []

    active_release = lifecycle_releases.current_release(install_root)
    active_manifest = lifecycle_releases.release_manifest(active_release)
    installed_commands = lifecycle_metadata.installed_entry_points(record)

    for entry_point in installed_commands:
        target = binary_directory / entry_point
        item = launcher_metadata.get(entry_point)
        if not isinstance(item, dict) or not isinstance(item.get("sha256"), str):
            fail("invalid installed launcher metadata")
        installed_fingerprint = {"kind": "file", "sha256": item["sha256"]}
        if not lifecycle_files.fingerprint_matches(target, installed_fingerprint):
            changed_launchers.append(target)
        backup_fingerprint = item.get("backup")
        if backup_fingerprint is not None:
            backup = install_root / "backups" / entry_point
            if not isinstance(
                backup_fingerprint, dict
            ) or not lifecycle_files.fingerprint_matches(backup, backup_fingerprint):
                fail(f"saved launcher backup is missing or modified: {backup}")

    if changed_launchers:
        for target in changed_launchers:
            lifecycle_reporting.report(
                f"refuse to remove modified or missing launcher: {target}"
            )
        fail("uninstall stopped without changing files")

    current = install_root / "current"
    releases_directory = install_root / "releases"
    releases: list[Path] = []
    for release in sorted(releases_directory.iterdir()):
        if not release.is_dir() or release.is_symlink():
            fail(f"unknown releases entry prevents safe uninstall: {release}")
        lifecycle_releases.verify_release(release)
        releases.append(release)

    if "devpod" in installed_commands:
        providers = active_manifest.get("providers")
        if not isinstance(providers, dict) or not isinstance(
            providers.get("devpod"), str
        ):
            fail("installed DevPod provider metadata is invalid")
        devpod_item = launcher_metadata["devpod"]
        replacement = (
            binary_directory / "devpod"
            if devpod_item.get("backup") is not None
            else lifecycle_paths.normalize_path(providers["devpod"])
        )
        home_value = os.environ.get("HOME")
        if not home_value:
            fail("HOME is required to restore DevPod SSH configuration")
        real_devpod = lifecycle_paths.normalize_path(providers["devpod"])
        for ssh_config in lifecycle_devpod.devpod_ssh_config_paths(
            real_devpod,
            lifecycle_paths.normalize_path(home_value),
            explicit_devpod_ssh_configs,
        ):
            lifecycle_ssh_restore.restore_devpod_proxy_commands(
                ssh_config,
                binary_directory / "devpod",
                replacement,
                dry_run,
            )

    for entry_point in installed_commands:
        target = binary_directory / entry_point
        item = launcher_metadata[entry_point]
        lifecycle_reporting.report(f"remove launcher {target}")
        backup_fingerprint = item.get("backup")
        backup = install_root / "backups" / entry_point
        if backup_fingerprint is not None:
            lifecycle_reporting.report(f"restore previous launcher from {backup}")
        if not dry_run:
            target.unlink()
            if backup_fingerprint is not None:
                os.replace(backup, target)

    lifecycle_reporting.report(f"remove current-release link {current}")
    if not dry_run:
        current.unlink()

    all_releases_removed = True
    for release in releases:
        all_releases_removed &= lifecycle_releases.remove_release(release, dry_run)

    if not all_releases_removed:
        fail("owned launchers were removed, but modified installation data remains")
    lifecycle_reporting.report(
        f"remove lifecycle metadata {install_root / '.install.json'}"
    )
    if dry_run:
        lifecycle_reporting.report("dry run complete; no files changed")
        return

    (install_root / ".install.json").unlink()
    for directory in (
        install_root / "backups",
        releases_directory,
        install_root,
    ):
        try:
            directory.rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            lifecycle_reporting.report(
                f"leave non-empty directory in place: {directory}"
            )
    lifecycle_reporting.report("uninstalled")


def status(requested_binary_directory: Path | None, install_root: Path) -> None:
    record = lifecycle_metadata.install_record(install_root)
    binary_directory = lifecycle_metadata.verified_binary_directory(
        requested_binary_directory, record, install_root
    )
    release = lifecycle_releases.current_release(install_root)
    manifest = lifecycle_releases.verify_release(release)
    installed_commands = lifecycle_metadata.verify_launchers(binary_directory, record)
    lifecycle_metadata.installation_id_from_record(record, manifest)
    providers = manifest.get("providers", {})
    if not isinstance(providers, dict):
        fail("installed provider metadata is invalid")
    if "devpod" in installed_commands:
        lifecycle_metadata.verified_managed_devpod_backup(
            install_root, record, providers.get("devpod")
        )
    lifecycle_reporting.report(
        f"installed version: {manifest.get('version', 'unknown')}"
    )
    lifecycle_reporting.report(f"installation root: {install_root}")
    lifecycle_reporting.report(f"binary directory: {binary_directory}")
    lifecycle_reporting.report(f"real Podman: {providers.get('podman', 'unknown')}")
    lifecycle_reporting.report(
        f"Compose provider: {providers.get('compose', 'unknown')}"
    )
    if "devpod" in providers:
        lifecycle_reporting.report(f"real DevPod: {providers.get('devpod', 'unknown')}")
    lifecycle_reporting.report(f"Python: {providers.get('python', 'unknown')}")
    lifecycle_reporting.report("installation files are intact")
