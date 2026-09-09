"""Compose rendering, Podman provenance queries, and provider execution."""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from paranoid_podman.common.environment import sanitized_provider_environment
from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.common.executables import is_external_executable
from paranoid_podman.common.layout import wrapper_directory
from paranoid_podman.common.provenance import (
    GUARD_INSTALLATION_ID,
    expected_provenance_output,
    provenance_inspect_format,
)
from paranoid_podman.compose.commands import run_build_request
from paranoid_podman.compose.diagnostics import classify_provider_failure
from paranoid_podman.compose.dotenv import write_provider_dotenv
from paranoid_podman.compose.errors import reject
from paranoid_podman.compose.models import Invocation
from paranoid_podman.compose.settings import (
    COMPOSE_CONTROL_ENV,
    COMPOSE_PROVIDER,
    MAX_CONFIG_BYTES,
    REAL_PODMAN,
)
from paranoid_podman.compose.snapshot import execution_arguments


def provider_environment() -> dict[str, str]:
    """Return an environment that cannot alter Compose routing or forward known secrets."""

    environment = {
        key: value
        for key, value in sanitized_provider_environment().items()
        if key not in COMPOSE_CONTROL_ENV and not key.startswith("PODMAN_COMPOSE_")
    }
    environment["PODMAN_COMPOSE_WARNING_LOGS"] = "false"
    return environment


def provider_command(arguments: list[str]) -> list[str]:
    return [COMPOSE_PROVIDER, *arguments]


def validate_provider_paths() -> None:
    package_directory = wrapper_directory()
    if not is_external_executable(REAL_PODMAN, package_directory):
        reject(
            "configured real Podman must be an external executable",
            category=ViolationCategory.INSTALLATION,
        )
    if not is_external_executable(COMPOSE_PROVIDER, package_directory):
        reject(
            "configured Compose provider must be an external executable",
            category=ViolationCategory.INSTALLATION,
        )
    if not re.fullmatch(r"[0-9a-f]{64}", GUARD_INSTALLATION_ID):
        reject(
            "PODMAN_GUARD_INSTALLATION_ID must contain 64 lowercase hex digits",
            category=ViolationCategory.INSTALLATION,
        )


def _run_podman_query(arguments: list[str], failure: str) -> str:
    try:
        result = subprocess.run(
            [REAL_PODMAN, *arguments],
            env=provider_environment(),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        reject(failure, category=ViolationCategory.INPUT)
    if result.returncode != 0:
        reject(failure, category=ViolationCategory.INPUT)
    return result.stdout


def require_current_policy_project_containers(project_name: str) -> None:
    """Reject executable lifecycle actions on legacy or foreign containers."""

    output = _run_podman_query(
        [
            "ps",
            "--all",
            "--filter",
            f"label=io.podman.compose.project={project_name}",
            "--format",
            "{{.ID}}",
        ],
        "failed to enumerate existing Compose containers",
    )
    references = [line.strip() for line in output.splitlines() if line.strip()]
    if len(references) > 1000 or any(
        not re.fullmatch(r"[0-9a-fA-F]{12,64}", reference) for reference in references
    ):
        reject(
            "received malformed existing-container metadata from Podman",
            category=ViolationCategory.INPUT,
        )

    for reference in references:
        policy = _run_podman_query(
            [
                "container",
                "inspect",
                "--format",
                provenance_inspect_format(),
                reference,
            ],
            "failed to verify existing Compose container provenance",
        )
        if policy.strip() != expected_provenance_output():
            reject(
                "blocked existing Compose container not created by the current "
                "guard policy; run guarded Compose down and recreate it",
                category=ViolationCategory.PROVENANCE,
            )


def require_safe_named_networks(model: dict[str, Any]) -> None:
    """Check existing explicit names before the provider can reuse a network."""

    definitions = [
        definition
        for definition in model.get("networks", {}).values()
        if isinstance(definition, dict) and "name" in definition
    ]
    if not definitions:
        return
    existing = set(
        _run_podman_query(
            ["network", "ls", "--format", "{{.Name}}"],
            "failed to enumerate existing Compose networks",
        ).splitlines()
    )
    for definition in definitions:
        if definition["name"] not in existing:
            continue
        actual = _run_podman_query(
            [
                "network",
                "inspect",
                "--format",
                "{{.Driver}}\t{{.Internal}}",
                definition["name"],
            ],
            "failed to verify existing Compose network settings",
        ).strip()
        internal = "true" if definition.get("internal", False) else "false"
        if actual != f"bridge\t{internal}":
            reject(
                "blocked existing named network: expected bridge driver and "
                "the declared internal setting",
                category=ViolationCategory.NETWORK,
            )


def render_arguments(invocation: Invocation, env_file: Path) -> list[str]:
    arguments = [
        "--dry-run",
        "--podman-path",
        REAL_PODMAN,
        "--env-file",
        str(env_file),
    ]
    for compose_file in invocation.compose_files:
        arguments.extend(["-f", str(compose_file)])
    for profile in invocation.profiles:
        arguments.extend(["--profile", profile])
    arguments.extend(["-p", invocation.project_name, "config"])
    return arguments


def render_config(invocation: Invocation) -> str:
    with tempfile.TemporaryDirectory(prefix="paranoid-compose-env-") as temp_dir:
        env_file = write_provider_dotenv(invocation, Path(temp_dir))
        try:
            result = subprocess.run(
                provider_command(render_arguments(invocation, env_file)),
                cwd=invocation.project_dir,
                env=provider_environment(),
                capture_output=True,
                text=True,
                check=False,
            )
        except (OSError, UnicodeError):
            reject(
                "failed to run the supported Compose provider",
                category=ViolationCategory.INSTALLATION,
            )
    if result.returncode != 0:
        message, category = classify_provider_failure(result.stderr)
        reject(
            "failed to render Compose config; provider output hidden to protect "
            f"secrets; category: {message}",
            category=category,
        )
    if len(result.stdout.encode("utf-8")) > MAX_CONFIG_BYTES:
        reject(
            "resolved Compose configuration exceeds the guarded size limit",
            category=ViolationCategory.INPUT,
        )
    return result.stdout


def run_direct_command(invocation: Invocation) -> int:
    arguments = ["--dry-run", "--podman-path", REAL_PODMAN]
    if invocation.command != "help":
        arguments.extend([invocation.command, *invocation.command_args])
    try:
        result = subprocess.run(
            provider_command(arguments),
            env=provider_environment(),
            check=False,
        )
    except OSError:
        reject(
            "failed to run the supported Compose provider",
            category=ViolationCategory.INSTALLATION,
        )
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


def run_project_metadata_command(invocation: Invocation) -> int:
    arguments = [
        "--podman-path",
        REAL_PODMAN,
        "-p",
        invocation.project_name,
        invocation.command,
        *invocation.command_args,
    ]
    try:
        result = subprocess.run(
            provider_command(arguments),
            env=provider_environment(),
            check=False,
        )
    except OSError:
        reject(
            "failed to run the supported Compose provider",
            category=ViolationCategory.INSTALLATION,
        )
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


def run_snapshot(invocation: Invocation, snapshot: Path) -> int:
    if invocation.command == "run":
        build_service, command_args = run_build_request(invocation.command_args)
        if build_service is not None:
            # podman-compose 1.6.x ignores build failures in its run handler.
            # Complete the explicit build before starting dependencies or a task.
            build = replace(invocation, command="build", command_args=[build_service])
            result_code = run_snapshot(build, snapshot)
            if result_code:
                return result_code
            invocation = replace(invocation, command_args=command_args)
    try:
        result = subprocess.run(
            provider_command(execution_arguments(invocation, snapshot)),
            cwd=invocation.project_dir,
            env=provider_environment(),
            check=False,
        )
    except OSError:
        reject(
            "failed to run the supported Compose provider",
            category=ViolationCategory.INSTALLATION,
        )
    return result.returncode if result.returncode >= 0 else 128 - result.returncode
