"""Shared Podman option checks, including environment-file metadata."""

import re
import stat
from pathlib import Path

from paranoid_podman.common.build_inputs import is_safe_image_reference
from paranoid_podman.common.environment import is_sensitive_env_key
from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.common.ports import is_published_port
from paranoid_podman.common.provenance import is_guard_reserved_label
from paranoid_podman.podman.errors import reject
from paranoid_podman.podman.models import CheckedOption
from paranoid_podman.podman.paths import validate_host_source_scope

MAX_INSPECTION_VALUE_LENGTH = 4096
CONTAINER_REFERENCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def normalize_environment(value: str) -> CheckedOption:
    key, separator, _environment_value = value.partition("=")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        reject(
            "blocked invalid environment variable name",
            category=ViolationCategory.ENVIRONMENT,
        )
    if is_sensitive_env_key(key):
        return CheckedOption(None, replacement=(f"--unsetenv={key}",))
    if not separator:
        reject(
            "blocked implicit host environment forwarding",
            category=ViolationCategory.ENVIRONMENT,
        )
    return CheckedOption(value)


def normalize_environment_file(value: str, project_dir: Path) -> CheckedOption:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = project_dir / candidate
    try:
        if candidate.is_symlink():
            reject(
                "blocked symlinked environment file",
                category=ViolationCategory.ENVIRONMENT,
            )
        resolved = candidate.resolve(strict=True)
        mode = resolved.stat().st_mode
        size = resolved.stat().st_size
    except (OSError, RuntimeError):
        reject(
            "blocked missing or invalid environment file",
            category=ViolationCategory.ENVIRONMENT,
        )
    if not stat.S_ISREG(mode) or size > 1024 * 1024:
        reject(
            "blocked invalid environment file", category=ViolationCategory.ENVIRONMENT
        )
    validate_host_source_scope(resolved, project_dir)
    return CheckedOption(str(resolved))


def validate_ulimit(value: str) -> None:
    name, separator, limits = value.partition("=")
    soft, limit_separator, hard = limits.partition(":")
    if (
        not separator
        or name not in {"nofile", "nproc"}
        or not soft.isdigit()
        or not hard.isdigit()
        or not limit_separator
    ):
        reject(
            "blocked malformed or unreviewed resource limit",
            category=ViolationCategory.UNSUPPORTED,
        )
    soft_limit = int(soft)
    hard_limit = int(hard)
    if not 1 <= soft_limit <= hard_limit <= 1_048_576:
        reject(
            "blocked resource limit outside the guarded range",
            category=ViolationCategory.INPUT,
        )


def validate_published_port(value: str) -> None:
    if not is_published_port(value):
        reject(
            "blocked malformed published port",
            category=ViolationCategory.PORT,
        )


def validate_exposed_port(value: str) -> None:
    match = re.fullmatch(r"([0-9]+)(?:/(tcp|udp))?", value)
    if not match or not 1 <= int(match.group(1)) <= 65535:
        reject("blocked invalid exposed port", category=ViolationCategory.INPUT)


def validate_image_reference(value: str) -> None:
    if not is_safe_image_reference(value):
        reject(
            "blocked malformed or non-local image reference",
            category=ViolationCategory.INPUT,
        )


def validate_label(value: str) -> None:
    key, separator, label_value = value.partition("=")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", key):
        reject("blocked invalid container label", category=ViolationCategory.INPUT)
    if len(value) > MAX_INSPECTION_VALUE_LENGTH or re.search(r"[\x00-\x1f\x7f]", value):
        reject("blocked invalid container label", category=ViolationCategory.INPUT)
    if is_guard_reserved_label(key) or key.startswith(
        ("com.docker.compose.", "io.podman.compose.")
    ):
        reject(
            "blocked reserved container-management label",
            category=ViolationCategory.POLICY,
        )
    if separator and is_sensitive_env_key(key) and label_value:
        reject(
            "blocked literal secret in a container label",
            category=ViolationCategory.SECRET,
        )


def validate_container_reference(value: str) -> None:
    if not CONTAINER_REFERENCE_PATTERN.fullmatch(value):
        reject("blocked invalid container name or ID", category=ViolationCategory.INPUT)


def validate_inspection_value(value: str, description: str) -> None:
    if (
        not value
        or len(value) > MAX_INSPECTION_VALUE_LENGTH
        or "\x00" in value
        or "\r" in value
        or "\n" in value
    ):
        reject(f"blocked invalid {description}", category=ViolationCategory.INPUT)
