"""Confirm outside-project bind access before creating Compose containers."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.compose.commands import run_service_index
from paranoid_podman.compose.errors import reject
from paranoid_podman.compose.models import Invocation
from paranoid_podman.compose.mount_locations import MountLocation, bind_locations
from paranoid_podman.compose.options import (
    COMMAND_VALUE_OPTIONS,
    _option_value,
    option_name,
    split_command_arguments,
)
from paranoid_podman.compose.paths import is_within


@dataclass(frozen=True)
class ExternalBind:
    service: str
    source: Path
    target: str
    read_only: bool
    locations: list[MountLocation]


def _mounting_services(invocation: Invocation, services: dict[str, Any]) -> set[str]:
    if invocation.command not in {"up", "run"}:
        return set()
    if invocation.command == "run":
        index = run_service_index(invocation.command_args)
        selected = {invocation.command_args[index]}
        options = []
        cursor = 0
        while cursor < index:
            argument = invocation.command_args[cursor]
            option = option_name(argument, COMMAND_VALUE_OPTIONS["run"])
            if option in COMMAND_VALUE_OPTIONS["run"]:
                _, cursor = _option_value(invocation.command_args, cursor, option)
            else:
                options.append(argument)
                cursor += 1
    else:
        options, positionals = split_command_arguments("up", invocation.command_args)
        selected = set(positionals) or set(services)
    if "--no-deps" not in options:
        pending = list(selected)
        while pending:
            name = pending.pop()
            for dependency in services[name].get("depends_on", {}):
                if dependency not in selected:
                    selected.add(dependency)
                    pending.append(dependency)
    if invocation.command == "up":
        scales = {name: services[name].get("scale", 1) for name in selected}
        for index, option in enumerate(options):
            if option == "--scale":
                name, count = options[index + 1].split("=", 1)
                scales[name] = int(count)
        selected = {name for name in selected if scales[name] != 0}
    return selected


def external_binds(
    invocation: Invocation,
    model: dict[str, Any],
    origins: dict[str, list[MountLocation]],
) -> list[ExternalBind]:
    binds = []
    services = model["services"]
    for name in sorted(_mounting_services(invocation, services)):
        # Automatic protected submounts need no separate confirmation.
        parents: list[tuple[Path, str]] = []
        volumes = sorted(
            services[name].get("volumes", []), key=lambda volume: len(volume["target"])
        )
        for volume in volumes:
            if volume["type"] != "bind":
                continue
            source = Path(volume["source"])
            target = volume["target"]
            if is_within(source, invocation.project_dir):
                continue
            if volume.get("read_only", False) and any(
                target.startswith(parent_target.rstrip("/") + "/")
                and source == parent_source / target[len(parent_target) + 1 :]
                for parent_source, parent_target in parents
            ):
                continue
            parents.append((source, target))
            binds.append(
                ExternalBind(
                    name,
                    source,
                    target,
                    volume.get("read_only", False),
                    bind_locations(origins.get(name, []), target),
                )
            )
    return binds


def _display_path(value: str | Path) -> str:
    # Quote paths so control characters cannot forge terminal messages.
    quoted = json.dumps(str(value), ensure_ascii=False)
    return "".join(
        character if character.isprintable() else f"\\u{ord(character):04x}"
        for character in quoted
    )


def confirm_external_binds(
    invocation: Invocation,
    model: dict[str, Any],
    origins: dict[str, list[MountLocation]],
) -> None:
    binds = external_binds(invocation, model, origins)
    if not binds:
        return
    heading = (
        "compose-guard: [warning] Compose requests host paths outside this project"
    )
    if (
        sys.stderr.isatty()
        and "NO_COLOR" not in os.environ
        and os.environ.get("TERM") != "dumb"
    ):
        heading = f"\033[1;38;5;208m{heading}\033[0m"
    print(heading, file=sys.stderr)
    print(f"  Project: {_display_path(invocation.project_dir)}", file=sys.stderr)
    for bind in binds:
        access = "read-only" if bind.read_only else "read-write"
        print(f"  Service: {bind.service} ({access})", file=sys.stderr)
        print(f"    Host path: {_display_path(bind.source)}", file=sys.stderr)
        print(f"    Container path: {_display_path(bind.target)}", file=sys.stderr)
        label = "Source" if len(bind.locations) == 1 else "Possible source"
        for location in bind.locations:
            print(
                f"    {label}: {_display_path(location.file)}:{location.line}",
                file=sys.stderr,
            )
    if any(not bind.locations for bind in binds):
        reject(
            "cannot locate an external bind in the original Compose files; "
            "no external access was approved",
            category=ViolationCategory.COMPOSE_FILE,
        )
    if not sys.stdin.isatty():
        reject(
            "external host paths require confirmation from an interactive terminal; "
            "stdin was not read",
            category=ViolationCategory.INTERRUPTED,
        )
    print(
        "Allow these mounts for this command? [y/N]: ",
        end="",
        file=sys.stderr,
        flush=True,
    )
    answer = bytearray()
    complete = False
    try:
        # Avoid buffered readline stealing pasted input intended for the container.
        while len(answer) < 128:
            character = os.read(sys.stdin.fileno(), 1)
            if character == b"\n":
                complete = True
                break
            if not character:
                break
            answer.extend(character)
    except OSError:
        answer.clear()
    if not complete or answer != b"y":
        reject(
            "external host mounts were not approved",
            category=ViolationCategory.CANCELLED,
        )
