"""Source preflight and resolved YAML loading; reads selected Compose files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, NoReturn

from paranoid_podman.common.environment import is_sensitive_env_key
from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.compose.dotenv import (
    DOTENV_KEY_PATTERN,
    validate_service_env_files,
)
from paranoid_podman.compose.errors import reject
from paranoid_podman.compose.models import Invocation
from paranoid_podman.compose.mount_locations import (
    MountLocation,
    source_mount_locations,
)
from paranoid_podman.compose.settings import MAX_CONFIG_BYTES
from paranoid_podman.compose.yaml_support import UniqueKeyLoader, yaml

SECRET_REFERENCE_PATTERN = re.compile(
    r"(?:\$[A-Za-z_][A-Za-z0-9_]*|"
    r"\$\{[A-Za-z_][A-Za-z0-9_]*"
    r"(?:(?:\?|:\?)[^}]*|(?::-|-))?\})\Z"
)


def load_resolved_config(rendered: str) -> dict[str, Any]:
    if yaml is None or UniqueKeyLoader is None:
        reject(
            "PyYAML is required for structured Compose validation",
            category=ViolationCategory.INSTALLATION,
        )
    try:
        # UniqueKeyLoader derives from SafeLoader and adds duplicate-key checks.
        model = yaml.load(rendered, Loader=UniqueKeyLoader)  # nosec B506
    except yaml.YAMLError:
        reject(
            "the provider returned an invalid structured Compose configuration",
            category=ViolationCategory.PROVIDER,
        )
    if not isinstance(model, dict):
        reject(
            "the resolved Compose configuration is not a mapping",
            category=ViolationCategory.POLICY,
        )
    if not all(isinstance(key, str) for key in model):
        reject(
            "the resolved Compose configuration has a non-string top-level key",
            category=ViolationCategory.POLICY,
        )
    return model


def _compose_file_label(compose_file: Path) -> str:
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", compose_file.name):
        return compose_file.name
    return "<compose-file>"


def _secret_value_is_indirect(node: Any) -> bool:
    if not isinstance(node, yaml.nodes.ScalarNode):
        return False
    if node.tag == "tag:yaml.org,2002:null":
        return True
    value = node.value.strip()
    return not value or bool(SECRET_REFERENCE_PATTERN.fullmatch(value))


def reject_literal_compose_secrets(root: Any, compose_file: Path) -> None:
    """Reject clear-text secret assignments while allowing variable references."""

    file_label = _compose_file_label(compose_file)

    def reject_assignment(key: str, line: int) -> NoReturn:
        reject(
            "literal secret in Compose source at "
            f"{file_label}:{line} for {key}; use variable interpolation or .env",
            category=ViolationCategory.SECRET,
        )

    def assignment_scope(path: tuple[str, ...]) -> bool:
        return (
            "environment" in path
            or "args" in path
            or bool(path and path[0].startswith("x-"))
        )

    def walk(node: Any, path: tuple[str, ...], seen_nodes: set[int]) -> None:
        if id(node) in seen_nodes:
            return
        seen_nodes.add(id(node))
        if isinstance(node, yaml.nodes.MappingNode):
            for key_node, value_node in node.value:
                if not isinstance(key_node, yaml.nodes.ScalarNode):
                    continue
                key = key_node.value
                if (
                    assignment_scope(path)
                    and DOTENV_KEY_PATTERN.fullmatch(key)
                    and is_sensitive_env_key(key)
                    and not _secret_value_is_indirect(value_node)
                ):
                    reject_assignment(key, key_node.start_mark.line + 1)
                walk(value_node, (*path, key), seen_nodes)
        elif isinstance(node, yaml.nodes.SequenceNode):
            for item in node.value:
                if assignment_scope(path) and isinstance(item, yaml.nodes.ScalarNode):
                    key, separator, value = item.value.partition("=")
                    if (
                        separator
                        and DOTENV_KEY_PATTERN.fullmatch(key)
                        and is_sensitive_env_key(key)
                        and not (
                            not value.strip()
                            or SECRET_REFERENCE_PATTERN.fullmatch(value.strip())
                        )
                    ):
                        reject_assignment(key, item.start_mark.line + 1)
                walk(item, path, seen_nodes)

    walk(root, (), set())


def reject_provider_extensions(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.lower().startswith("x-podman"):
                reject(
                    "provider-specific x-podman extensions are not supported",
                    category=ViolationCategory.PROVIDER,
                )
            reject_provider_extensions(item)
    elif isinstance(value, list):
        for item in value:
            reject_provider_extensions(item)


def validate_source_env_files(
    model: dict[str, Any],
    compose_file: Path,
    project_dir: Path,
) -> None:
    services = model.get("services", {})
    if not isinstance(services, dict):
        return
    for service in services.values():
        if isinstance(service, dict) and "env_file" in service:
            validate_service_env_files(
                service["env_file"],
                project_dir,
                base_dir=compose_file.parent,
            )


def preflight_compose_inputs(invocation: Invocation) -> dict[str, list[MountLocation]]:
    """Reject source directives that could make the renderer read other files."""

    if yaml is None or UniqueKeyLoader is None:
        reject(
            "PyYAML is required for structured Compose validation",
            category=ViolationCategory.INSTALLATION,
        )
    origins: dict[str, list[MountLocation]] = {}
    for compose_file in invocation.compose_files:
        try:
            if compose_file.stat().st_size > MAX_CONFIG_BYTES:
                reject(
                    "a Compose input exceeds the guarded size limit",
                    category=ViolationCategory.INPUT,
                )
            source = compose_file.read_text(encoding="utf-8")
            source_root = yaml.compose(source, Loader=yaml.SafeLoader)
            # UniqueKeyLoader derives from SafeLoader and adds duplicate-key checks.
            model = yaml.load(source, Loader=UniqueKeyLoader)  # nosec B506
        except (OSError, UnicodeError, yaml.YAMLError):
            reject(
                "a Compose input is not valid guarded YAML",
                category=ViolationCategory.POLICY,
            )
        if not isinstance(model, dict):
            reject(
                "a Compose input is not a top-level mapping",
                category=ViolationCategory.POLICY,
            )
        if source_root is None:
            reject("a Compose input is empty", category=ViolationCategory.POLICY)
        reject_literal_compose_secrets(source_root, compose_file)
        reject_provider_extensions(model)
        validate_source_env_files(model, compose_file, invocation.project_dir)
        if "include" in model:
            reject(
                "Compose include is blocked because it can read unreviewed files",
                category=ViolationCategory.UNSUPPORTED,
            )
        services = model.get("services", {})
        if not isinstance(services, dict):
            reject(
                "a Compose input has malformed services",
                category=ViolationCategory.INPUT,
            )
        if any(
            isinstance(service, dict) and "extends" in service
            for service in services.values()
        ):
            reject(
                "Compose extends is blocked because it can read unreviewed files",
                category=ViolationCategory.UNSUPPORTED,
            )
        for name, locations in source_mount_locations(
            source_root, compose_file
        ).items():
            origins.setdefault(name, []).extend(locations)
    return origins
