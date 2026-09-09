"""Provider queries, provenance sequencing, Compose delegation, and final execve."""

import os
import subprocess
import sys
from typing import NoReturn

from paranoid_podman.common.environment import sanitized_provider_environment
from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.common.layout import command_arguments
from paranoid_podman.common.provenance import (
    GUARD_INSTALLATION_ID,
    expected_provenance_output,
    provenance_inspect_format,
)
from paranoid_podman.podman.commands import (
    build_exec_command,
    copy_request,
    start_targets,
    validate_devpod_temporary_file,
)
from paranoid_podman.podman.errors import reject
from paranoid_podman.podman.settings import DEBUG, REAL_PODMAN
from paranoid_podman.podman.validation import validate_container_reference


def debug_exec(cmd: list[str]) -> NoReturn:
    if DEBUG:
        print(
            f"podman-guard: executing provider with {len(cmd) - 1} arguments "
            "(values redacted)",
            file=sys.stderr,
        )
    try:
        os.execve(cmd[0], cmd, sanitized_provider_environment())
    except OSError:
        reject(
            "failed to execute the configured provider",
            category=ViolationCategory.INSTALLATION,
        )


def require_current_policy_container(reference: str) -> None:
    """Reject containers that were not created by the current guard policy."""

    command = [
        REAL_PODMAN,
        "container",
        "inspect",
        "--format",
        provenance_inspect_format(),
        reference,
    ]
    try:
        result = subprocess.run(
            command,
            env=sanitized_provider_environment(),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        reject(
            "failed to verify existing container provenance",
            category=ViolationCategory.INPUT,
        )
    if result.returncode != 0:
        reject(
            "could not verify existing container provenance",
            category=ViolationCategory.INPUT,
        )
    if result.stdout.strip() != expected_provenance_output():
        reject(
            "blocked container not created by the current guard policy; "
            "recreate it or use the real Podman deliberately for cleanup",
            category=ViolationCategory.PROVENANCE,
        )


def execute_start(argv: list[str], command_index: int) -> NoReturn:
    for target in start_targets(argv, command_index):
        validate_container_reference(target)
        require_current_policy_container(target)
    debug_exec([REAL_PODMAN, *argv])


def execute_exec(argv: list[str], command_index: int) -> NoReturn:
    command = build_exec_command(argv, command_index)
    require_current_policy_container(command.reference)
    debug_exec(command.arguments)


def execute_copy(argv: list[str]) -> NoReturn:
    container, host_path = copy_request(argv)
    require_current_policy_container(container)
    validate_devpod_temporary_file(host_path)
    debug_exec([REAL_PODMAN, *argv])


def execute_compose(arguments: list[str]) -> NoReturn:
    compose_command = command_arguments("compose-guard")
    compose_environment = sanitized_provider_environment()
    compose_environment["PODMAN_GUARD_INSTALLATION_ID"] = GUARD_INSTALLATION_ID
    try:
        os.execve(
            compose_command[0],
            [*compose_command, *arguments],
            compose_environment,
        )
    except OSError:
        reject("failed to execute the Compose guard", category=ViolationCategory.INPUT)
