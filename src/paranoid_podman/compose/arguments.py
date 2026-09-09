"""Parse global Compose arguments and resolve project inputs."""

from __future__ import annotations

from pathlib import Path

from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.compose.commands import (
    validate_command_syntax,
    validate_ls_syntax,
    validate_version_arguments,
)
from paranoid_podman.compose.errors import reject
from paranoid_podman.compose.models import Invocation
from paranoid_podman.compose.options import _option_value, printable_option
from paranoid_podman.compose.paths import _resolve_compose_inputs, guarded_project_name
from paranoid_podman.compose.schema import NAME_PATTERN
from paranoid_podman.compose.settings import (
    DIRECT_PROJECT_COMMANDS,
    KNOWN_UNSUPPORTED_COMMANDS,
    MAX_PARALLEL,
    MUTATING_COMMANDS,
    READ_ONLY_PROJECT_COMMANDS,
    SAFE_DIRECT_COMMANDS,
)


def parse_invocation(arguments: list[str], cwd: Path | None = None) -> Invocation:
    """Parse the provider-independent global Compose command line."""

    cwd = (cwd or Path.cwd()).resolve(strict=True)
    raw_files: list[str] = []
    raw_env_file: str | None = None
    requested_project_name: str | None = None
    profiles: list[str] = []
    execution_globals: list[str] = []
    command = ""
    command_args: list[str] = []

    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in {"-f", "--file"} or argument.startswith("--file="):
            value, index = _option_value(arguments, index, argument.split("=", 1)[0])
            raw_files.append(value)
            continue
        if argument == "--profile" or argument.startswith("--profile="):
            value, index = _option_value(arguments, index, "--profile")
            if not NAME_PATTERN.fullmatch(value):
                reject("invalid Compose profile name", category=ViolationCategory.INPUT)
            profiles.append(value)
            continue
        if argument == "--parallel" or argument.startswith("--parallel="):
            value, index = _option_value(arguments, index, "--parallel")
            try:
                parallel = int(value)
            except ValueError:
                reject(
                    "invalid Compose parallel limit", category=ViolationCategory.INPUT
                )
            if not 1 <= parallel <= MAX_PARALLEL:
                reject(
                    f"Compose parallel limit must be between 1 and {MAX_PARALLEL}",
                    category=ViolationCategory.POLICY,
                )
            execution_globals.extend(["--parallel", str(parallel)])
            continue
        if argument in {"--dry-run", "--no-ansi"}:
            execution_globals.append(argument)
            index += 1
            continue
        if argument in {"-p", "--project-name"} or argument.startswith(
            "--project-name="
        ):
            value, index = _option_value(arguments, index, "--project-name")
            if requested_project_name is not None:
                reject(
                    "the Compose project name may be specified only once",
                    category=ViolationCategory.POLICY,
                )
            if len(value) > 128 or not NAME_PATTERN.fullmatch(value):
                reject("invalid Compose project name", category=ViolationCategory.INPUT)
            requested_project_name = value
            continue
        if argument == "--env-file" or argument.startswith("--env-file="):
            value, index = _option_value(arguments, index, "--env-file")
            if raw_env_file is not None:
                reject(
                    "the Compose global env file may be specified only once",
                    category=ViolationCategory.POLICY,
                )
            raw_env_file = value
            continue
        if argument in {"-v", "--version"}:
            if len(arguments) != 1:
                reject(
                    "the global version option cannot be combined with other arguments",
                    category=ViolationCategory.INPUT,
                )
            command = "version"
            index += 1
            break
        if argument in {"-h", "--help"}:
            if len(arguments) != 1:
                reject(
                    "the global help option cannot be combined with other arguments",
                    category=ViolationCategory.INPUT,
                )
            command = "help"
            index += 1
            break
        if argument == "--":
            reject(
                "a command separator before the Compose command is not supported",
                category=ViolationCategory.POLICY,
            )
        if argument.startswith("-"):
            reject(
                f"blocked unknown or unsafe Compose option {printable_option(argument)}; "
                "no unreviewed pass-through is available",
                category=ViolationCategory.UNSUPPORTED,
            )
        command = argument
        command_args = arguments[index + 1 :]
        break

    if not command:
        command = "help"

    if command in SAFE_DIRECT_COMMANDS:
        if (
            raw_files
            or raw_env_file is not None
            or requested_project_name is not None
            or profiles
            or execution_globals
        ):
            reject(
                "Compose input options cannot be combined with help or version",
                category=ViolationCategory.INPUT,
            )
        if command == "help" and command_args:
            reject(
                "command-specific help is not supported by the guarded adapter",
                category=ViolationCategory.POLICY,
            )
        if command == "version":
            validate_version_arguments(command_args)
        return Invocation(
            command, command_args, [], None, [], cwd, "", execution_globals
        )
    if command in DIRECT_PROJECT_COMMANDS:
        if raw_files or raw_env_file is not None or profiles or execution_globals:
            reject(
                "Compose ls does not accept guarded project input options",
                category=ViolationCategory.POLICY,
            )
        if requested_project_name is None:
            reject(
                "Compose ls requires an explicit project name",
                category=ViolationCategory.POLICY,
            )
        validate_ls_syntax(command_args, requested_project_name)
        return Invocation(
            command,
            command_args,
            [],
            None,
            [],
            cwd,
            requested_project_name,
            execution_globals,
        )
    if command in KNOWN_UNSUPPORTED_COMMANDS:
        reject(
            f"Compose command {command} is not supported by the guarded adapter",
            category=ViolationCategory.POLICY,
        )
    if command not in READ_ONLY_PROJECT_COMMANDS | MUTATING_COMMANDS:
        reject(
            "unknown Compose command; no unreviewed pass-through is available",
            category=ViolationCategory.UNSUPPORTED,
        )

    validate_command_syntax(command, command_args)
    compose_files, env_file, project_dir = _resolve_compose_inputs(
        raw_files, raw_env_file, cwd
    )
    return Invocation(
        command=command,
        command_args=command_args,
        compose_files=compose_files,
        env_file=env_file,
        profiles=profiles,
        project_dir=project_dir,
        project_name=requested_project_name or guarded_project_name(project_dir),
        execution_globals=execution_globals,
    )
