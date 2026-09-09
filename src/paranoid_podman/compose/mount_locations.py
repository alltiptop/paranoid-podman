"""Locate bind declarations in original YAML, including aliases and interpolation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paranoid_podman.compose.yaml_support import yaml


@dataclass(frozen=True)
class MountLocation:
    file: Path
    line: int
    target: str


def _mapping_nodes(
    node: Any, ancestors: frozenset[int] = frozenset()
) -> dict[str, Any]:
    if not isinstance(node, yaml.nodes.MappingNode) or id(node) in ancestors:
        return {}
    ancestors = ancestors | {id(node)}
    inherited: dict[str, Any] = {}
    explicit: dict[str, Any] = {}
    for key, value in node.value:
        if not isinstance(key, yaml.nodes.ScalarNode):
            continue
        if key.tag == "tag:yaml.org,2002:merge":
            items = (
                value.value if isinstance(value, yaml.nodes.SequenceNode) else [value]
            )
            for item in reversed(items):
                inherited.update(_mapping_nodes(item, ancestors))
        else:
            explicit[key.value] = value
    return {**inherited, **explicit}


def _interpolation_end(value: str, start: int) -> int:
    """Skip a Compose variable expression, without evaluating or printing it."""

    if value.startswith("${", start):
        depth = 1
        index = start + 2
        while index < len(value) and depth:
            if value[index] == "{":
                depth += 1
            elif value[index] == "}":
                depth -= 1
            index += 1
        return index
    match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*", value[start:])
    return start + len(match.group()) if match else start + 1


def _short_target(value: str) -> str:
    # Colons in ${PATH:-default} are part of an expression, not mount separators.
    boundaries = [-1]
    index = 0
    while index < len(value):
        if value.startswith("$$", index):
            index += 2
        elif value[index] == "$":
            index = _interpolation_end(value, index)
        else:
            if value[index] == ":":
                boundaries.append(index)
            index += 1
    boundaries.append(len(value))
    if len(boundaries) < 3:
        return value
    return value[boundaries[1] + 1 : boundaries[2]]


def source_mount_locations(
    root: Any, compose_file: Path
) -> dict[str, list[MountLocation]]:
    """Capture source marks before provider execution can change the source files."""

    result: dict[str, list[MountLocation]] = {}
    services = _mapping_nodes(_mapping_nodes(root).get("services"))
    for name, service in services.items():
        volumes = _mapping_nodes(service).get("volumes")
        if not isinstance(volumes, yaml.nodes.SequenceNode):
            continue
        locations = result.setdefault(name, [])
        for volume in volumes.value:
            if isinstance(volume, yaml.nodes.ScalarNode):
                target = _short_target(volume.value)
                line = volume.start_mark.line + 1
            else:
                fields = _mapping_nodes(volume)
                target_node = fields.get("target")
                if not isinstance(target_node, yaml.nodes.ScalarNode):
                    continue
                target = target_node.value
                line = fields.get("source", volume).start_mark.line + 1
            locations.append(MountLocation(compose_file, line, target))
    return result


def _target_parts(value: str) -> list[str]:
    """Split on interpolation without constructing backtracking wildcard regexes."""

    parts = [""]
    index = 0
    while index < len(value):
        if value.startswith("$$", index):
            parts[-1] += "$"
            index += 2
        elif value[index] == "$":
            end = _interpolation_end(value, index)
            if end > index + 1:
                parts.append("")
            else:
                parts[-1] += "$"
            index = end
        else:
            parts[-1] += value[index]
            index += 1
    return parts


def _target_matches(parts: list[str], target: str) -> bool:
    if len(parts) == 1:
        return target == parts[0]
    if not target.startswith(parts[0]) or not target.endswith(parts[-1]):
        return False
    start, end = len(parts[0]), len(target) - len(parts[-1])
    if start > end:
        return False
    for part in parts[1:-1]:
        found = target.find(part, start, end)
        if found < 0:
            return False
        start = found + len(part)
    return True


def bind_locations(locations: list[MountLocation], target: str) -> list[MountLocation]:
    """Match container targets; keep ambiguous interpolated origins explicit."""

    matches: list[MountLocation] = []
    for location in locations:
        parts = _target_parts(location.target.rstrip("/"))
        if not _target_matches(parts, target.rstrip("/")):
            continue
        # A later literal target supersedes earlier declarations of that target.
        if len(parts) == 1:
            matches = []
        if location not in matches:
            matches.append(location)
    return matches
