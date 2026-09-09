"""Validate installation records, ownership, backups, and provenance."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

from paranoid_podman.lifecycle import files as lifecycle_files
from paranoid_podman.lifecycle import paths as lifecycle_paths
from paranoid_podman.lifecycle import settings as lifecycle_settings
from paranoid_podman.lifecycle.errors import fail


def read_json(path: Path) -> dict[str, Any]:
    try:
        file_stat = path.lstat()
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > 1024 * 1024:
            fail(f"invalid lifecycle metadata: {path}")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(f"cannot read lifecycle metadata: {path}: {error}")
    if not isinstance(value, dict):
        fail(f"invalid lifecycle metadata: {path}")
    return value


def validate_metadata_header(value: dict[str, Any], source: Path) -> None:
    if (
        value.get("format") != lifecycle_settings.MANIFEST_FORMAT
        or value.get("project") != lifecycle_settings.PROJECT_NAME
    ):
        fail(f"unrecognized lifecycle metadata: {source}")


def install_record(install_root: Path) -> dict[str, Any]:
    record_path = install_root / ".install.json"
    if not record_path.exists() and not record_path.is_symlink():
        fail(
            f"no installation found at {install_root}; run ./install.sh install "
            "for a new installation, or use --libdir to select an existing one"
        )
    record = read_json(record_path)
    validate_metadata_header(record, record_path)
    if lifecycle_paths.normalize_path(record.get("install_root", "")) != install_root:
        fail("installation metadata names a different installation root")
    if not isinstance(record.get("launchers"), dict):
        fail("invalid launcher metadata")
    return record


def installation_id_from_record(
    record: dict[str, Any], manifest: dict[str, Any]
) -> str:
    """Return and verify the private installation provenance identifier."""

    installation_id = record.get("installation_id")
    expected_hash = manifest.get("installation_id_sha256")
    if not isinstance(
        installation_id, str
    ) or not lifecycle_settings.INSTALLATION_ID_PATTERN.fullmatch(installation_id):
        fail("installation provenance is missing or invalid")
    if expected_hash != lifecycle_files.sha256_bytes(installation_id.encode()):
        fail("installation provenance does not match the current release")
    return installation_id


def verified_binary_directory(
    requested: Path | None, record: dict[str, Any], install_root: Path
) -> Path:
    recorded_value = record.get("binary_directory")
    if not isinstance(recorded_value, str) or not Path(recorded_value).is_absolute():
        fail("invalid binary-directory metadata")
    recorded = lifecycle_paths.normalize_path(recorded_value)
    lifecycle_paths.validate_install_paths(recorded, install_root)
    if requested is not None and requested != recorded:
        fail(f"installed binary directory is {recorded}, not {requested}")
    return recorded


def installed_entry_points(record: dict[str, Any]) -> tuple[str, ...]:
    installed = tuple(record["launchers"])
    valid_sets = {
        frozenset(lifecycle_settings.ENTRY_POINTS),
        frozenset(
            lifecycle_settings.ENTRY_POINTS + lifecycle_settings.DEVPOD_ENTRY_POINTS
        ),
    }
    if frozenset(installed) not in valid_sets:
        fail("installed launcher metadata is incomplete")
    return installed


def verify_launchers(binary_directory: Path, record: dict[str, Any]) -> tuple[str, ...]:
    launcher_metadata = record["launchers"]
    installed = installed_entry_points(record)
    for entry_point in installed:
        target = binary_directory / entry_point
        item = launcher_metadata[entry_point]
        if not isinstance(item, dict) or not isinstance(item.get("sha256"), str):
            fail("invalid installed launcher metadata")
        expected = {"kind": "file", "sha256": item["sha256"]}
        if not lifecycle_files.fingerprint_matches(target, expected):
            fail(f"installed launcher was modified: {target}")
    return installed


def verified_managed_devpod_backup(
    install_root: Path,
    record: dict[str, Any],
    provider_value: object,
) -> tuple[Path, ...]:
    if not isinstance(provider_value, str):
        return ()
    backup = install_root / "backups/devpod"
    if lifecycle_paths.normalize_path(provider_value) != backup:
        return ()
    item = record["launchers"].get("devpod")
    fingerprint = item.get("backup") if isinstance(item, dict) else None
    if not isinstance(fingerprint, dict) or not lifecycle_files.fingerprint_matches(
        backup, fingerprint
    ):
        fail(f"saved DevPod provider is missing or modified: {backup}")
    return (backup,)
