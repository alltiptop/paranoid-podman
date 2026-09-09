"""Validate and privately copy project and service dotenv files."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from paranoid_podman.common.environment import PROVIDER_CONTROL_ENV_KEYS
from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.compose.errors import reject
from paranoid_podman.compose.models import Invocation
from paranoid_podman.compose.paths import is_within
from paranoid_podman.compose.schema import _mapping
from paranoid_podman.compose.settings import COMPOSE_CONTROL_ENV, MAX_DOTENV_BYTES

if TYPE_CHECKING:
    from dotenv.parser import Binding

parse_dotenv_stream: Callable[[TextIO], Iterator[Binding]] | None
try:
    from dotenv.parser import parse_stream as _parse_dotenv_stream
except ImportError:  # pragma: no cover - exercised without python-dotenv
    parse_dotenv_stream = None
else:
    parse_dotenv_stream = _parse_dotenv_stream


DOTENV_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
DOTENV_CONTROL_PREFIXES = ("COMPOSE_", "CONTAINERS_", "PODMAN_")


def _dotenv_control_key(key: str) -> bool:
    return (
        key in COMPOSE_CONTROL_ENV
        or key in PROVIDER_CONTROL_ENV_KEYS
        or key.startswith(DOTENV_CONTROL_PREFIXES)
        or key.startswith("PODMAN_COMPOSE_")
    )


def _read_dotenv_bindings(
    source: Path,
    description: str,
    *,
    required: bool,
) -> list[tuple[str, str]]:
    if parse_dotenv_stream is None:
        reject(
            "python-dotenv is required for guarded Compose interpolation",
            category=ViolationCategory.ENVIRONMENT,
        )
    try:
        source_stat = source.lstat()
    except FileNotFoundError:
        if required:
            reject(f"{description} is missing", category=ViolationCategory.ENVIRONMENT)
        return []
    except OSError:
        reject(
            f"{description} cannot be inspected", category=ViolationCategory.ENVIRONMENT
        )

    if stat.S_ISLNK(source_stat.st_mode) or not stat.S_ISREG(source_stat.st_mode):
        reject(
            f"{description} must be a regular non-symlink file",
            category=ViolationCategory.ENVIRONMENT,
        )
    if source_stat.st_size > MAX_DOTENV_BYTES:
        reject(
            f"{description} exceeds the guarded size limit",
            category=ViolationCategory.ENVIRONMENT,
        )

    bindings: list[tuple[str, str]] = []
    flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        descriptor = os.open(source, flags)
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            os.close(descriptor)
            reject(
                f"{description} must be a regular non-symlink file",
                category=ViolationCategory.ENVIRONMENT,
            )
        if opened_stat.st_size > MAX_DOTENV_BYTES:
            os.close(descriptor)
            reject(
                f"{description} exceeds the guarded size limit",
                category=ViolationCategory.ENVIRONMENT,
            )
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            for binding in parse_dotenv_stream(stream):
                if binding.error:
                    reject(
                        f"{description} has invalid syntax at "
                        f"line {binding.original.line}",
                        category=ViolationCategory.ENVIRONMENT,
                    )
                if binding.key is None:
                    continue
                if not DOTENV_KEY_PATTERN.fullmatch(binding.key):
                    reject(
                        f"{description} has an invalid variable name at "
                        f"line {binding.original.line}",
                        category=ViolationCategory.ENVIRONMENT,
                    )
                if binding.value is None:
                    reject(
                        f"{description} has a variable without a value at "
                        f"line {binding.original.line}",
                        category=ViolationCategory.ENVIRONMENT,
                    )
                bindings.append((binding.key, binding.original.string))
    except (OSError, UnicodeError):
        reject(
            f"{description} cannot be read safely",
            category=ViolationCategory.ENVIRONMENT,
        )
    return bindings


def write_provider_dotenv(invocation: Invocation, directory: Path) -> Path:
    """Write a private project dotenv copy without provider-control variables."""

    destination = directory / "project.env"
    source = invocation.env_file or invocation.project_dir / ".env"
    bindings = _read_dotenv_bindings(
        source,
        "the project .env",
        required=False,
    )
    lines = [line for key, line in bindings if not _dotenv_control_key(key)]

    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.writelines(lines)
    return destination


def validate_service_env_files(
    value: Any,
    project_dir: Path,
    *,
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    if value is None:
        return []
    definitions = value if isinstance(value, list) else [value]
    validated: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    for definition in definitions:
        path_value: Any
        if isinstance(definition, str):
            path_value = definition
            required = True
        else:
            settings = _mapping(
                definition,
                "malformed service env_file",
                category=ViolationCategory.ENVIRONMENT,
            )
            if set(settings) - {"path", "required"}:
                reject(
                    "blocked resolved configuration: unreviewed env_file setting",
                    category=ViolationCategory.ENVIRONMENT,
                )
            path_value = settings.get("path")
            required = settings.get("required", True)
        if not isinstance(path_value, str) or not path_value:
            reject(
                "blocked resolved configuration: malformed env_file path",
                category=ViolationCategory.ENVIRONMENT,
            )
        if not isinstance(required, bool):
            reject(
                "blocked resolved configuration: malformed env_file requirement",
                category=ViolationCategory.ENVIRONMENT,
            )

        candidate = Path(path_value).expanduser()
        if not candidate.is_absolute():
            candidate = (base_dir or project_dir) / candidate
        if candidate.is_symlink():
            reject(
                "blocked resolved configuration: env_file must be a regular "
                "non-symlink file",
                category=ViolationCategory.ENVIRONMENT,
            )
        try:
            resolved = candidate.resolve(strict=True)
        except (FileNotFoundError, RuntimeError):
            if not required:
                continue
            reject(
                "blocked resolved configuration: required env_file is missing",
                category=ViolationCategory.ENVIRONMENT,
            )
        except OSError:
            reject(
                "blocked resolved configuration: env_file cannot be resolved",
                category=ViolationCategory.ENVIRONMENT,
            )
        if not is_within(resolved, project_dir):
            reject(
                "blocked resolved configuration: env_file outside the project",
                category=ViolationCategory.ENVIRONMENT,
            )
        _read_dotenv_bindings(resolved, "a service env_file", required=True)
        if resolved in seen_paths:
            reject(
                "blocked resolved configuration: duplicate service env_file",
                category=ViolationCategory.ENVIRONMENT,
            )
        seen_paths.add(resolved)
        validated.append({"path": str(resolved), "required": True})
    return validated


def copy_service_env_files(directory: Path, model: dict[str, Any]) -> None:
    services = model.get("services", {})
    for service_index, service_name in enumerate(sorted(services)):
        service = services[service_name]
        rewritten: list[str] = []
        for env_index, definition in enumerate(service.get("env_file", [])):
            source = Path(definition["path"])
            bindings = _read_dotenv_bindings(
                source,
                "a service env_file",
                required=True,
            )
            destination = directory / f"service-{service_index}-{env_index}.env"
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.writelines(line for _key, line in bindings)
            rewritten.append(str(destination))
        if rewritten:
            service["env_file"] = rewritten
        else:
            service.pop("env_file", None)
