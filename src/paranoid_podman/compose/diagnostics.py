"""Redacted provider errors and source locations; reads Compose inputs."""

from __future__ import annotations

import re
import sys
from typing import Any

from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.compose.models import Invocation
from paranoid_podman.compose.schema import NAME_PATTERN
from paranoid_podman.compose.settings import MAX_SOURCE_LOCATIONS
from paranoid_podman.compose.yaml_support import yaml

SOURCE_REVIEW_KEYS = {
    "build",
    "cap_add",
    "cap_drop",
    "configs",
    "cpus",
    "devices",
    "env_file",
    "environment",
    "extends",
    "extra_hosts",
    "include",
    "ipc",
    "mem_limit",
    "mem_reservation",
    "memswap_limit",
    "network_mode",
    "networks",
    "pid",
    "ports",
    "privileged",
    "pull_policy",
    "restart",
    "secrets",
    "security_opt",
    "shm_size",
    "sysctls",
    "ulimits",
    "use_api_socket",
    "user",
    "userns_mode",
    "volumes",
    "volumes_from",
}
INTERPOLATION_PATTERN = re.compile(r"\$(?:\{|[A-Za-z_])")


def classify_provider_failure(stderr: str) -> tuple[str, ViolationCategory]:
    """Return a fixed redacted category without forwarding provider text."""

    lowered = stderr.lower()
    categories = (
        (
            ("required variable", "variable is not set", "missing mandatory value"),
            "environment interpolation failed",
            ViolationCategory.INTERPOLATION,
        ),
        (
            ("failed to obtain podman configuration", "runtime init lock"),
            "rootless Podman initialization failed",
            ViolationCategory.PROVIDER,
        ),
        (
            ("unrecognized arguments", "unknown option", "usage:"),
            "provider CLI compatibility failed",
            ViolationCategory.PROVIDER,
        ),
        (
            ("yaml", "compose file", "services must be a mapping"),
            "provider rejected Compose structure",
            ViolationCategory.PROVIDER,
        ),
        (
            ("no such file", "not found", "cannot open"),
            "provider could not read a referenced file",
            ViolationCategory.PROVIDER,
        ),
    )
    for markers, message, category in categories:
        if any(marker in lowered for marker in markers):
            return message, category
    return "provider rejected the configuration", ViolationCategory.PROVIDER


def _display_source_segment(path: tuple[str, ...], key: str) -> str:
    if path == ("services",):
        return "<service>"
    if path == ("volumes",) or path == ("networks",):
        return "<resource>"
    if path and path[-1] == "environment":
        return "<variable>"
    if path and path[-1] in {"depends_on", "networks"}:
        return "<entry>"
    if NAME_PATTERN.fullmatch(key) and len(key) <= 64:
        return key
    return "<key>"


def source_review_locations(
    invocation: Invocation,
    *,
    include_interpolation: bool = False,
    category: ViolationCategory | None = None,
    service_name: str | None = None,
) -> list[str]:
    """Locate security-relevant source fields without rendering their values."""

    if yaml is None:
        return []
    locations: list[str] = []
    seen_locations: set[str] = set()
    review_keys = SOURCE_REVIEW_KEYS
    if category is ViolationCategory.NETWORK:
        review_keys = {"network_mode", "networks"}
    elif category is ViolationCategory.PORT:
        review_keys = {"ports"}
    elif category is ViolationCategory.MOUNT:
        review_keys = {"volumes", "volumes_from"}
    elif category is ViolationCategory.HOST_MAPPING:
        review_keys = {"extra_hosts"}
    elif category is ViolationCategory.ENVIRONMENT:
        review_keys = {"environment", "env_file"}
    elif category is ViolationCategory.BUILD_CONTEXT:
        review_keys = {"build"}
    elif category is ViolationCategory.PRIVILEGE:
        review_keys = {
            "privileged",
            "cap_add",
            "cap_drop",
            "devices",
            "security_opt",
            "userns_mode",
            "pid",
            "ipc",
            "uts",
            "use_api_socket",
        }

    def add_location(file_label: str, line: int, path: tuple[str, ...]) -> None:
        if len(locations) >= MAX_SOURCE_LOCATIONS:
            return
        rendered_path = ".".join(path) if path else "<document>"
        location = f"{file_label}:{line}: {rendered_path} (value hidden)"
        if location not in seen_locations:
            seen_locations.add(location)
            locations.append(location)

    def walk(
        node: Any,
        file_label: str,
        raw_path: tuple[str, ...],
        display_path: tuple[str, ...],
        seen_nodes: set[int],
    ) -> None:
        if len(locations) >= MAX_SOURCE_LOCATIONS or id(node) in seen_nodes:
            return
        seen_nodes.add(id(node))
        if isinstance(node, yaml.nodes.MappingNode):
            for key_node, value_node in node.value:
                if not isinstance(key_node, yaml.nodes.ScalarNode):
                    continue
                key = key_node.value
                if (
                    raw_path == ("services",)
                    and service_name is not None
                    and key != service_name
                ):
                    continue
                next_raw_path = (*raw_path, key)
                next_display_path = (
                    *display_path,
                    _display_source_segment(raw_path, key),
                )
                if key in review_keys or (
                    category is ViolationCategory.NETWORK
                    and len(raw_path) == 2
                    and raw_path[0] == "networks"
                ):
                    add_location(
                        file_label,
                        key_node.start_mark.line + 1,
                        next_display_path,
                    )
                walk(
                    value_node,
                    file_label,
                    next_raw_path,
                    next_display_path,
                    seen_nodes,
                )
        elif isinstance(node, yaml.nodes.SequenceNode):
            for item in node.value:
                walk(
                    item,
                    file_label,
                    raw_path,
                    (*display_path, "[]"),
                    seen_nodes,
                )
        elif (
            include_interpolation
            and isinstance(node, yaml.nodes.ScalarNode)
            and INTERPOLATION_PATTERN.search(node.value)
        ):
            add_location(
                file_label,
                node.start_mark.line + 1,
                display_path,
            )

    for compose_file in invocation.compose_files:
        file_label = (
            compose_file.name
            if re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", compose_file.name)
            else "<compose-file>"
        )
        try:
            source = compose_file.read_text(encoding="utf-8")
            root = yaml.compose(source, Loader=yaml.SafeLoader)
        except (OSError, UnicodeError, yaml.YAMLError):
            continue
        if root is not None:
            walk(root, file_label, (), (), set())
    return locations


def with_source_locations(
    reason: str,
    invocation: Invocation,
    *,
    category: ViolationCategory,
    service_name: str | None = None,
) -> str:
    locations = source_review_locations(
        invocation,
        include_interpolation=category is ViolationCategory.INTERPOLATION,
        category=category,
        service_name=service_name,
    )
    if service_name is not None:
        reason = f"service '{service_name}': {reason}"
    if not locations:
        return reason
    details = "\n".join(f"compose-guard:   {location}" for location in locations)
    return (
        f"{reason}\n"
        "compose-guard: related configuration locations (values hidden; "
        "not necessarily unsafe):\n"
        f"{details}"
    )


def print_source_locations(invocation: Invocation) -> None:
    locations = source_review_locations(invocation)
    if not locations:
        return
    print(
        "compose-guard: security-relevant configuration locations (values hidden):",
        file=sys.stderr,
    )
    for location in locations:
        print(f"compose-guard:   {location}", file=sys.stderr)
