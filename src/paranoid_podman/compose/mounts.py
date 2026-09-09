"""Normalize mounts and discover protected project paths."""

from __future__ import annotations

import os
import posixpath
import stat
from pathlib import Path
from typing import Any

from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.common.paths import (
    host_source_rejection,
    is_protected_name,
    is_unreadable_foreign_directory,
)
from paranoid_podman.compose.errors import reject
from paranoid_podman.compose.paths import is_within
from paranoid_podman.compose.schema import _mapping

UNSAFE_VOLUME_OPTIONS = {"U", "idmap", "relabel"}


def normalize_target(target: Any) -> str:
    if not isinstance(target, str) or not target.startswith("/"):
        reject(
            "blocked resolved configuration: non-absolute container mount target",
            category=ViolationCategory.MOUNT,
        )
    normalized = posixpath.normpath(target)
    if normalized != target.rstrip("/") or normalized == "/":
        reject(
            "blocked resolved configuration: unsafe container mount target",
            category=ViolationCategory.MOUNT,
        )
    return normalized


def resolve_bind_source(source: Any, project_dir: Path) -> Path:
    if not isinstance(source, str) or not source:
        reject(
            "blocked resolved configuration: malformed bind source",
            category=ViolationCategory.MOUNT,
        )
    candidate = Path(source).expanduser()
    if not candidate.is_absolute():
        candidate = project_dir / candidate
    try:
        if candidate.is_symlink():
            reject(
                "blocked resolved configuration: symlinked bind source",
                category=ViolationCategory.MOUNT,
            )
        resolved = candidate.resolve(strict=True)
        mode = resolved.stat().st_mode
    except (OSError, RuntimeError):
        reject(
            "blocked resolved configuration: missing bind source",
            category=ViolationCategory.MOUNT,
        )
    if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
        reject(
            "blocked resolved configuration: special host file bind",
            category=ViolationCategory.POLICY,
        )
    reason = host_source_rejection(resolved, project_dir)
    if reason is not None:
        reject(
            f"blocked resolved configuration: {reason}",
            category=ViolationCategory.MOUNT,
        )
    return resolved


def parse_short_volume(specification: str) -> dict[str, Any]:
    parts = specification.split(":")
    if len(parts) == 1:
        return {"type": "volume", "target": parts[0]}
    if len(parts) not in {2, 3}:
        reject(
            "blocked resolved configuration: ambiguous short volume syntax",
            category=ViolationCategory.MOUNT,
        )

    source = parts[0]
    target = parts[1]
    options = parts[2].split(",") if len(parts) == 3 else []
    if len(parts) == 2 and not target.startswith("/"):
        source = ""
        target = parts[0]
        options = parts[1].split(",")
    if UNSAFE_VOLUME_OPTIONS.intersection(options):
        reject(
            "blocked resolved configuration: unsafe bind options",
            category=ViolationCategory.POLICY,
        )
    if any(
        option not in {"Z", "cached", "consistent", "delegated", "ro", "rw", "z"}
        for option in options
    ):
        reject(
            "blocked resolved configuration: unreviewed volume options",
            category=ViolationCategory.MOUNT,
        )

    result: dict[str, Any] = {"target": target}
    if source:
        result["source"] = source
        result["type"] = "bind" if source.startswith(("/", ".", "~")) else "volume"
    else:
        result["type"] = "volume"
    if "ro" in options and "rw" in options:
        reject(
            "blocked resolved configuration: conflicting volume access modes",
            category=ViolationCategory.MOUNT,
        )
    if "ro" in options:
        result["read_only"] = True
    selinux_options = {option for option in options if option in {"z", "Z"}}
    if len(selinux_options) > 1:
        reject(
            "blocked resolved configuration: conflicting SELinux relabel modes",
            category=ViolationCategory.INPUT,
        )
    if selinux_options:
        result["bind"] = {"selinux": selinux_options.pop()}
    if any(option in {"cached", "consistent", "delegated"} for option in options):
        result["consistency"] = next(
            option
            for option in options
            if option in {"cached", "consistent", "delegated"}
        )
    return result


def protected_source(path: Path) -> bool:
    return any(is_protected_name(part) for part in path.parts)


def protected_children(
    source: Path,
    target: str,
) -> list[dict[str, Any]]:
    if not source.is_dir():
        return []
    protected: list[dict[str, Any]] = []

    def unreadable(error: OSError) -> None:
        if not is_unreadable_foreign_directory(error):
            reject(
                "blocked resolved configuration: cannot inspect protected paths "
                "in bind directory; check directory access and ownership",
                category=ViolationCategory.MOUNT,
            )

    for root, directories, files in os.walk(source, topdown=True, onerror=unreadable):
        directories.sort()
        files.sort()
        root_path = Path(root)
        protected_names = [
            name for name in (*directories, *files) if is_protected_name(name)
        ]
        directories[:] = [name for name in directories if not is_protected_name(name)]
        for name in protected_names:
            candidate = root_path / name
            try:
                resolved = candidate.resolve(strict=True)
                mode = resolved.stat().st_mode
            except (OSError, RuntimeError):
                reject(
                    "blocked resolved configuration: invalid protected project path",
                    category=ViolationCategory.INPUT,
                )
            if not is_within(resolved, source):
                reject(
                    "blocked resolved configuration: protected path resolves "
                    "outside the bind source",
                    category=ViolationCategory.MOUNT,
                )
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                reject(
                    "blocked resolved configuration: special protected project path",
                    category=ViolationCategory.POLICY,
                )
            relative = candidate.relative_to(source)
            protected.append(
                {
                    "type": "bind",
                    "source": str(resolved),
                    "target": posixpath.join(target, *relative.parts),
                    "read_only": True,
                    "bind": {"create_host_path": False},
                }
            )
    return protected


def normalize_volume(
    volume: Any,
    project_dir: Path,
    declared_volumes: set[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], set[str]]:
    if isinstance(volume, str):
        normalized = parse_short_volume(volume)
    else:
        normalized = dict(
            _mapping(
                volume, "malformed service volume", category=ViolationCategory.MOUNT
            )
        )

    allowed_keys = {
        "bind",
        "consistency",
        "read_only",
        "source",
        "target",
        "tmpfs",
        "type",
        "volume",
    }
    if set(normalized) - allowed_keys:
        reject(
            "blocked resolved configuration: unreviewed service volume settings",
            category=ViolationCategory.MOUNT,
        )
    mount_type = normalized.get("type", "volume")
    target = normalize_target(normalized.get("target"))
    read_only = normalized.get("read_only", False)
    if not isinstance(read_only, bool):
        reject(
            "blocked resolved configuration: malformed volume access mode",
            category=ViolationCategory.MOUNT,
        )

    if mount_type == "bind":
        bind_options = _mapping(
            normalized.get("bind", {}),
            "malformed bind options",
            category=ViolationCategory.INPUT,
        )
        if set(bind_options) - {"create_host_path", "propagation", "selinux"}:
            reject(
                "blocked resolved configuration: unreviewed bind options",
                category=ViolationCategory.UNSUPPORTED,
            )
        selinux = bind_options.get("selinux")
        if selinux not in (None, "", "z", "Z"):
            reject(
                "blocked resolved configuration: unsafe bind relabeling",
                category=ViolationCategory.POLICY,
            )
        if bind_options.get("propagation") not in (None, "", "private", "rprivate"):
            reject(
                "blocked resolved configuration: unsafe bind propagation",
                category=ViolationCategory.POLICY,
            )
        if bind_options.get("create_host_path") not in (None, False):
            reject(
                "blocked resolved configuration: host path creation",
                category=ViolationCategory.POLICY,
            )
        source = resolve_bind_source(normalized.get("source"), project_dir)
        if selinux and not is_within(source, project_dir):
            reject(
                "blocked resolved configuration: SELinux relabel outside the project",
                category=ViolationCategory.INPUT,
            )
        source_is_protected = protected_source(source)
        normalized_bind: dict[str, Any] = {"create_host_path": False}
        if selinux:
            normalized_bind["selinux"] = selinux
        normalized = {
            "type": "bind",
            "source": str(source),
            "target": target,
            "read_only": read_only or source_is_protected,
            "bind": normalized_bind,
        }
        descendants = [] if source_is_protected else protected_children(source, target)
        additions = [] if normalized["read_only"] else descendants
        protected_targets = (
            {target}
            if source_is_protected
            else {mount["target"] for mount in descendants}
        )
        return normalized, additions, protected_targets

    if mount_type == "volume":
        volume_options = _mapping(
            normalized.get("volume", {}),
            "malformed named volume options",
            category=ViolationCategory.MOUNT,
        )
        if set(volume_options) - {"nocopy"}:
            reject(
                "blocked resolved configuration: unreviewed named volume options",
                category=ViolationCategory.MOUNT,
            )
        if "nocopy" in volume_options and not isinstance(
            volume_options["nocopy"], bool
        ):
            reject(
                "blocked resolved configuration: malformed named volume option",
                category=ViolationCategory.MOUNT,
            )
        volume_source = normalized.get("source")
        if volume_source is not None and (
            not isinstance(volume_source, str) or volume_source not in declared_volumes
        ):
            reject(
                "blocked resolved configuration: undeclared named volume",
                category=ViolationCategory.MOUNT,
            )
        result = {"type": "volume", "target": target, "read_only": read_only}
        if volume_source is not None:
            result["source"] = volume_source
        if volume_options:
            result["volume"] = volume_options
        return result, [], set()

    if mount_type == "tmpfs":
        if normalized.get("source") is not None:
            reject(
                "blocked resolved configuration: tmpfs mount with a source",
                category=ViolationCategory.MOUNT,
            )
        tmpfs_options = _mapping(
            normalized.get("tmpfs", {}),
            "malformed tmpfs options",
            category=ViolationCategory.INPUT,
        )
        if set(tmpfs_options) - {"mode", "size"}:
            reject(
                "blocked resolved configuration: unreviewed tmpfs options",
                category=ViolationCategory.UNSUPPORTED,
            )
        result = {"type": "tmpfs", "target": target, "tmpfs": tmpfs_options}
        return result, [], set()

    reject(
        "blocked resolved configuration: unsupported mount type",
        category=ViolationCategory.MOUNT,
    )


def validate_service_volumes(
    service: dict[str, Any], project_dir: Path, declared_volumes: set[str]
) -> None:
    volumes = service.get("volumes", [])
    if not isinstance(volumes, list):
        reject(
            "blocked resolved configuration: malformed service volumes",
            category=ViolationCategory.MOUNT,
        )

    normalized = []
    additions = []
    user_targets = []
    protected_targets: list[tuple[int, str]] = []
    for index, volume in enumerate(volumes):
        mount, protected, protected_for_mount = normalize_volume(
            volume, project_dir, declared_volumes
        )
        normalized.append(mount)
        additions.extend(protected)
        user_targets.append(mount["target"])
        protected_targets.extend((index, target) for target in protected_for_mount)

    if len(user_targets) != len(set(user_targets)):
        reject(
            "blocked resolved configuration: duplicate mount target",
            category=ViolationCategory.MOUNT,
        )
    for user_index, user_target in enumerate(user_targets):
        mount = normalized[user_index]
        for protected_index, protected_target in protected_targets:
            if user_index == protected_index or not (
                user_target == protected_target
                or user_target.startswith(protected_target + "/")
            ):
                continue
            parent = normalized[protected_index]
            same_read_only_bind = (
                user_target == protected_target
                and mount["type"] == parent["type"] == "bind"
                and mount.get("read_only") is True
                and Path(mount["source"])
                == Path(parent["source"])
                / posixpath.relpath(user_target, parent["target"])
            )
            if not same_read_only_bind:
                reject(
                    "blocked resolved configuration: mount overrides a protected project path",
                    category=ViolationCategory.MOUNT,
                )
    service["volumes"] = normalized + [
        mount for mount in additions if mount["target"] not in user_targets
    ]
