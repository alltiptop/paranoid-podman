"""Mount normalization and protected path discovery using filesystem metadata."""

import os
import posixpath
import re
import stat
from pathlib import Path

from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.common.paths import (
    is_protected_name,
    is_unreadable_foreign_directory,
)
from paranoid_podman.podman.errors import reject
from paranoid_podman.podman.models import CheckedOption, MountPolicy
from paranoid_podman.podman.paths import (
    is_host_path_source,
    is_within,
    validate_host_source_scope,
)

UNSAFE_BIND_OPTIONS = {"U", "idmap", "relabel", "z", "Z"}
VOLUME_OPTIONS = {"ro", "rw"}
MOUNT_KEY_ALIASES = {
    "consistency": "consistency",
    "destination": "target",
    "dst": "target",
    "ro": "readonly",
    "readonly": "readonly",
    "source": "source",
    "src": "source",
    "target": "target",
    "type": "type",
}


def normalize_container_path(value: str) -> str:
    if (
        not value
        or not value.startswith("/")
        or ":" in value
        or "," in value
        or re.search(r"[\x00-\x1f\x7f]", value)
    ):
        reject(
            "blocked invalid container mount target", category=ViolationCategory.MOUNT
        )
    normalized = posixpath.normpath(value)
    if normalized == "/":
        reject("blocked container root mount target", category=ViolationCategory.MOUNT)
    return normalized


def is_container_path_within(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent.rstrip("/") + "/")


def resolve_bind_source(value: str, project_dir: Path) -> Path:
    if not value or not is_host_path_source(value):
        reject(
            "blocked named volume or ambiguous bind source",
            category=ViolationCategory.MOUNT,
        )
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = project_dir / candidate
    try:
        if candidate.is_symlink():
            reject(
                "blocked symlinked host bind source", category=ViolationCategory.MOUNT
            )
        resolved = candidate.resolve(strict=True)
        mode = resolved.stat().st_mode
    except (OSError, RuntimeError):
        reject(
            "blocked missing or invalid host bind source",
            category=ViolationCategory.MOUNT,
        )
    if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
        reject("blocked special host bind source", category=ViolationCategory.MOUNT)
    if (
        ":" in str(resolved)
        or "," in str(resolved)
        or re.search(r"[\x00-\x1f\x7f]", str(resolved))
    ):
        reject(
            "blocked host bind source with unsupported path characters",
            category=ViolationCategory.MOUNT,
        )
    validate_host_source_scope(resolved, project_dir)
    return resolved


def is_protected_source(source: Path) -> bool:
    return any(is_protected_name(part) for part in source.parts)


def protected_submounts(
    source: Path,
    target: str,
) -> tuple[tuple[str, str], ...]:
    if not source.is_dir():
        return ()

    mounts: list[tuple[str, str]] = []

    def unreadable(error: OSError) -> None:
        if not is_unreadable_foreign_directory(error):
            reject(
                "blocked bind directory whose protected paths cannot be inspected; "
                "check directory access and ownership",
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
                    "blocked invalid protected project path",
                    category=ViolationCategory.INPUT,
                )
            if not is_within(resolved, source):
                reject(
                    "blocked protected path that resolves outside the bind source",
                    category=ViolationCategory.MOUNT,
                )
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                reject(
                    "blocked special protected project path",
                    category=ViolationCategory.POLICY,
                )
            if "," in str(resolved) or re.search(r"[\x00-\x1f\x7f]", str(resolved)):
                reject(
                    "blocked protected path with unsupported path characters",
                    category=ViolationCategory.UNSUPPORTED,
                )
            relative = candidate.relative_to(source)
            container_target = normalize_container_path(
                posixpath.join(target, *relative.parts)
            )
            mounts.append((str(resolved), container_target))
    return tuple(mounts)


def mount_policy(
    source: Path,
    target: str,
    read_only: bool,
) -> tuple[bool, MountPolicy]:
    source_is_protected = is_protected_source(source)
    descendants = () if source_is_protected else protected_submounts(source, target)
    read_only = read_only or source_is_protected
    additions = () if read_only else descendants
    protected_targets = (
        (target,)
        if source_is_protected
        else tuple(mount_target for _, mount_target in descendants)
    )
    return read_only, MountPolicy(
        target, additions, protected_targets, source, read_only
    )


def normalize_volume(value: str, project_dir: Path) -> CheckedOption:
    parts = value.split(":")
    if len(parts) == 1:
        target = normalize_container_path(parts[0])
        return CheckedOption(target, mount=MountPolicy(target))
    if len(parts) not in {2, 3}:
        reject("blocked ambiguous volume syntax", category=ViolationCategory.MOUNT)

    source_value, target_value = parts[:2]
    if not source_value:
        reject("blocked malformed volume source", category=ViolationCategory.MOUNT)
    target = normalize_container_path(target_value)
    options = set(parts[2].split(",")) if len(parts) == 3 else set()
    if UNSAFE_BIND_OPTIONS.intersection(options):
        reject("blocked unsafe host volume options", category=ViolationCategory.MOUNT)
    if "" in options or options - VOLUME_OPTIONS:
        reject("blocked unreviewed volume options", category=ViolationCategory.MOUNT)
    if "ro" in options and "rw" in options:
        reject(
            "blocked conflicting volume access modes", category=ViolationCategory.MOUNT
        )

    source = resolve_bind_source(source_value, project_dir)
    read_only, policy = mount_policy(source, target, "ro" in options)
    normalized_options = sorted(options - {"ro", "rw"})
    normalized_options.append("ro" if read_only else "rw")
    normalized = f"{source}:{target}:{','.join(normalized_options)}"
    return CheckedOption(normalized, mount=policy)


def parse_mount_fields(value: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in value.split(","):
        if not part:
            reject(
                "blocked malformed mount specification",
                category=ViolationCategory.MOUNT,
            )
        key, separator, field_value = part.partition("=")
        canonical_key = MOUNT_KEY_ALIASES.get(key)
        if canonical_key is None:
            reject("blocked unreviewed mount setting", category=ViolationCategory.MOUNT)
        if canonical_key in fields:
            reject("blocked duplicate mount setting", category=ViolationCategory.MOUNT)
        if separator:
            if not field_value:
                reject("blocked empty mount setting", category=ViolationCategory.MOUNT)
            fields[canonical_key] = field_value
        else:
            if canonical_key != "readonly":
                reject(
                    "blocked malformed mount setting", category=ViolationCategory.MOUNT
                )
            fields[canonical_key] = "true"
    return fields


def requested_read_only(fields: dict[str, str]) -> bool:
    value = fields.get("readonly")
    if value is None:
        return False
    if value not in {"1", "true"}:
        reject(
            "blocked writable override in mount specification",
            category=ViolationCategory.MOUNT,
        )
    return True


def normalize_mount(value: str, project_dir: Path) -> CheckedOption:
    fields = parse_mount_fields(value)
    mount_type = fields.get("type")
    target_value = fields.get("target")
    if mount_type not in {"bind", "tmpfs", "volume"} or target_value is None:
        reject(
            "blocked malformed or unsupported mount specification",
            category=ViolationCategory.MOUNT,
        )
    target = normalize_container_path(target_value)
    read_only = requested_read_only(fields)

    if mount_type == "bind":
        if set(fields) - {"consistency", "type", "source", "target", "readonly"}:
            reject(
                "blocked unreviewed bind mount setting",
                category=ViolationCategory.MOUNT,
            )
        consistency = fields.get("consistency")
        if consistency not in {None, "cached", "consistent", "delegated"}:
            reject(
                "blocked invalid bind consistency setting",
                category=ViolationCategory.INPUT,
            )
        source_value = fields.get("source")
        if source_value is None:
            reject(
                "blocked bind mount without a source", category=ViolationCategory.MOUNT
            )
        source = resolve_bind_source(source_value, project_dir)
        read_only, policy = mount_policy(source, target, read_only)
        normalized = f"type=bind,src={source},target={target}"
        if read_only:
            normalized += ",readonly=true"
        if consistency is not None:
            normalized += f",consistency={consistency}"
        return CheckedOption(normalized, mount=policy)

    if "source" in fields:
        reject(
            "blocked named or sourced non-bind mount", category=ViolationCategory.MOUNT
        )
    if set(fields) - {"type", "target", "readonly"}:
        reject("blocked unreviewed mount setting", category=ViolationCategory.MOUNT)
    normalized = f"type={mount_type},target={target}"
    if read_only:
        normalized += ",readonly=true"
    return CheckedOption(normalized, mount=MountPolicy(target))


def validate_mount_layout(mounts: list[MountPolicy]) -> None:
    for index, mount in enumerate(mounts):
        for other_index, other in enumerate(mounts):
            if index != other_index and mount.target == other.target:
                reject(
                    "blocked duplicate container mount target",
                    category=ViolationCategory.MOUNT,
                )
        for protected_target in mount.protected_targets:
            for other_index, other in enumerate(mounts):
                if index != other_index and is_container_path_within(
                    other.target, protected_target
                ):
                    if (
                        other.target == protected_target
                        and other.read_only
                        and mount.source is not None
                        and other.source
                        == mount.source / posixpath.relpath(other.target, mount.target)
                    ):
                        continue
                    reject(
                        "blocked mount nested inside a protected project path",
                        category=ViolationCategory.MOUNT,
                    )


def protected_mount_arguments(mounts: list[MountPolicy]) -> list[str]:
    arguments: list[str] = []
    existing_targets = {mount.target for mount in mounts}
    for mount in mounts:
        for source, target in mount.protected_mounts:
            if target in existing_targets:
                continue
            arguments.extend(
                [
                    "--mount",
                    f"type=bind,src={source},target={target},readonly=true",
                ]
            )
    return arguments
