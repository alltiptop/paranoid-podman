"""Compose validation, review, snapshot sequencing, and CLI errors."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from paranoid_podman.common.diagnostics import print_violation
from paranoid_podman.compose.arguments import parse_invocation
from paranoid_podman.compose.commands import validate_requested_services
from paranoid_podman.compose.diagnostics import (
    print_source_locations,
    with_source_locations,
)
from paranoid_podman.compose.errors import PolicyViolation, reject
from paranoid_podman.compose.models import Invocation
from paranoid_podman.compose.policy import validate_and_rewrite
from paranoid_podman.compose.provider import (
    render_config,
    require_current_policy_project_containers,
    require_safe_named_networks,
    run_direct_command,
    run_project_metadata_command,
    run_snapshot,
    validate_provider_paths,
)
from paranoid_podman.compose.review import confirm_external_binds
from paranoid_podman.compose.settings import (
    DIRECT_PROJECT_COMMANDS,
    MUTATING_COMMANDS,
    PROVENANCE_REQUIRED_COMMANDS,
    SAFE_DIRECT_COMMANDS,
)
from paranoid_podman.compose.snapshot import write_snapshot
from paranoid_podman.compose.source import (
    load_resolved_config,
    preflight_compose_inputs,
)


def run_project_command(invocation: Invocation) -> int:
    try:
        origins = preflight_compose_inputs(invocation)
        rendered = render_config(invocation)
        model = validate_and_rewrite(load_resolved_config(rendered), invocation)
    except PolicyViolation as error:
        reject(
            with_source_locations(
                str(error),
                invocation,
                category=error.category,
                service_name=error.service_name,
            ),
            category=error.category,
        )
    services = set(model["services"])
    validate_requested_services(invocation, services)

    if invocation.command in PROVENANCE_REQUIRED_COMMANDS:
        require_safe_named_networks(model)
        require_current_policy_project_containers(invocation.project_name)

    if invocation.command == "config":
        if "--services" in invocation.command_args:
            for service in sorted(services):
                print(service)
        return 0
    if (
        invocation.command in MUTATING_COMMANDS
        and os.environ.get("PODMAN_GUARD_DEBUG") == "1"
    ):
        print_source_locations(invocation)
        print(
            f"compose-guard: validated {len(services)} service(s); executing {invocation.command} (values hidden)",
            file=sys.stderr,
        )

    with tempfile.TemporaryDirectory(prefix="paranoid-compose-") as temp_dir:
        snapshot = write_snapshot(Path(temp_dir), model)
        confirm_external_binds(invocation, model, origins)
        return run_snapshot(invocation, snapshot)


def main() -> int:
    try:
        validate_provider_paths()
        invocation = parse_invocation(sys.argv[1:])
        if invocation.command in SAFE_DIRECT_COMMANDS:
            return run_direct_command(invocation)
        if invocation.command in DIRECT_PROJECT_COMMANDS:
            return run_project_metadata_command(invocation)
        return run_project_command(invocation)
    except PolicyViolation as error:
        print_violation("compose-guard", error)
        return 125
    except KeyboardInterrupt:
        return 130
