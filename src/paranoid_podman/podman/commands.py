"""Reviewed inspection, container, image, and DevPod copy command forms."""

import os
import re
import stat
from pathlib import Path

from paranoid_podman.common.environment import is_sensitive_env_key
from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.podman.arguments import printable_option
from paranoid_podman.podman.errors import reject
from paranoid_podman.podman.models import ExecCommand
from paranoid_podman.podman.settings import REAL_PODMAN
from paranoid_podman.podman.validation import (
    validate_container_reference,
    validate_image_reference,
    validate_inspection_value,
)


def build_ps_command(argv: list[str], command_index: int) -> list[str]:
    arguments = argv[command_index + 1 :]
    flag_options = {"-a", "-q", "--all", "--no-trunc", "--quiet", "--size"}
    value_options = {"-f", "-n", "--filter", "--format", "--last"}
    short_flags = {"a", "q"}
    forwarded: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in flag_options:
            forwarded.append(argument)
            index += 1
            continue
        if (
            len(argument) > 2
            and argument.startswith("-")
            and not argument.startswith("--")
            and all(flag in short_flags for flag in argument[1:])
        ):
            forwarded.append(argument)
            index += 1
            continue
        if argument in value_options:
            if index + 1 >= len(arguments):
                reject(
                    f"missing value for {argument}", category=ViolationCategory.INPUT
                )
            value = arguments[index + 1]
            validate_inspection_value(value, "container-list option")
            if argument in {"-n", "--last"} and (
                not value.isdigit() or int(value) > 10_000
            ):
                reject(
                    "blocked invalid container-list limit",
                    category=ViolationCategory.INPUT,
                )
            forwarded.extend((argument, value))
            index += 2
            continue
        if argument.startswith("--") and "=" in argument:
            option, value = argument.split("=", 1)
            if option not in value_options:
                reject(
                    "blocked unreviewed container-list option",
                    category=ViolationCategory.UNSUPPORTED,
                )
            validate_inspection_value(value, "container-list option")
            if option == "--last" and (not value.isdigit() or int(value) > 10_000):
                reject(
                    "blocked invalid container-list limit",
                    category=ViolationCategory.INPUT,
                )
            forwarded.append(argument)
            index += 1
            continue
        reject(
            "blocked unreviewed container-list option",
            category=ViolationCategory.UNSUPPORTED,
        )
    return [REAL_PODMAN, *argv[: command_index + 1], *forwarded]


def build_inspect_command(
    argv: list[str],
    command_index: int,
    forced_type: str | None = None,
) -> list[str]:
    arguments = argv[command_index + 1 :]
    forwarded: list[str] = []
    inspect_type = forced_type
    targets: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if not argument.startswith("-"):
            targets = arguments[index:]
            break
        if argument in {"-s", "--size"}:
            forwarded.append(argument)
            index += 1
            continue
        if argument in {"-f", "--format", "--type"}:
            if index + 1 >= len(arguments):
                reject(
                    f"missing value for {argument}", category=ViolationCategory.INPUT
                )
            value = arguments[index + 1]
            validate_inspection_value(value, "inspect option")
            if argument == "--type":
                if forced_type is not None or value not in {"container", "image"}:
                    reject(
                        "blocked unreviewed inspect object type",
                        category=ViolationCategory.UNSUPPORTED,
                    )
                inspect_type = value
            forwarded.extend((argument, value))
            index += 2
            continue
        if argument.startswith("--") and "=" in argument:
            option, value = argument.split("=", 1)
            if option not in {"--format", "--type"}:
                reject(
                    "blocked unreviewed inspect option",
                    category=ViolationCategory.UNSUPPORTED,
                )
            validate_inspection_value(value, "inspect option")
            if option == "--type":
                if forced_type is not None or value not in {"container", "image"}:
                    reject(
                        "blocked unreviewed inspect object type",
                        category=ViolationCategory.UNSUPPORTED,
                    )
                inspect_type = value
            forwarded.append(argument)
            index += 1
            continue
        reject(
            "blocked unreviewed inspect option", category=ViolationCategory.UNSUPPORTED
        )

    if not targets:
        reject("missing inspect object", category=ViolationCategory.INPUT)
    for target in targets:
        if inspect_type == "image":
            validate_image_reference(target)
        else:
            validate_container_reference(target)
    return [REAL_PODMAN, *argv[: command_index + 1], *forwarded, *targets]


def build_info_command(argv: list[str]) -> list[str]:
    arguments = argv[1:]
    if not arguments:
        return [REAL_PODMAN, *argv]
    if len(arguments) == 2 and arguments[0] in {"-f", "--format"}:
        validate_inspection_value(arguments[1], "info format")
        return [REAL_PODMAN, *argv]
    if len(arguments) == 1 and arguments[0].startswith("--format="):
        validate_inspection_value(arguments[0].split("=", 1)[1], "info format")
        return [REAL_PODMAN, *argv]
    reject("blocked unreviewed info option", category=ViolationCategory.UNSUPPORTED)


def start_targets(argv: list[str], command_index: int) -> list[str]:
    targets = argv[command_index + 1 :]
    if not targets:
        reject("missing container to start", category=ViolationCategory.INPUT)
    return targets


def build_stop_command(argv: list[str], command_index: int) -> list[str]:
    arguments = argv[command_index + 1 :]
    forwarded: list[str] = []
    targets: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if not argument.startswith("-"):
            targets = arguments[index:]
            break
        if argument in {"-t", "--time", "--timeout"}:
            if index + 1 >= len(arguments):
                reject(
                    f"missing value for {argument}", category=ViolationCategory.INPUT
                )
            value = arguments[index + 1]
            if not value.isdigit() or int(value) > 86_400:
                reject("blocked invalid stop timeout", category=ViolationCategory.INPUT)
            forwarded.extend((argument, value))
            index += 2
            continue
        if argument.startswith(("--time=", "--timeout=")):
            value = argument.split("=", 1)[1]
            if not value.isdigit() or int(value) > 86_400:
                reject("blocked invalid stop timeout", category=ViolationCategory.INPUT)
            forwarded.append(argument)
            index += 1
            continue
        reject("blocked unreviewed stop option", category=ViolationCategory.UNSUPPORTED)
    if not targets:
        reject("missing container to stop", category=ViolationCategory.INPUT)
    for target in targets:
        validate_container_reference(target)
    return [REAL_PODMAN, *argv[: command_index + 1], *forwarded, *targets]


def build_logs_command(argv: list[str], command_index: int) -> list[str]:
    arguments = argv[command_index + 1 :]
    forwarded: list[str] = []
    target: str | None = None
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if not argument.startswith("-"):
            if index != len(arguments) - 1:
                reject(
                    "blocked extra container-log target",
                    category=ViolationCategory.POLICY,
                )
            target = argument
            break
        if argument in {"-f", "-t", "--details", "--follow", "--timestamps"}:
            forwarded.append(argument)
            index += 1
            continue
        if argument in {"-n", "--since", "--tail", "--until"}:
            if index + 1 >= len(arguments):
                reject(
                    f"missing value for {argument}", category=ViolationCategory.INPUT
                )
            value = arguments[index + 1]
            validate_inspection_value(value, "log option")
            forwarded.extend((argument, value))
            index += 2
            continue
        if argument.startswith(("--since=", "--tail=", "--until=")):
            validate_inspection_value(argument.split("=", 1)[1], "log option")
            forwarded.append(argument)
            index += 1
            continue
        reject("blocked unreviewed log option", category=ViolationCategory.UNSUPPORTED)
    if target is None:
        reject("missing container for logs", category=ViolationCategory.INPUT)
    validate_container_reference(target)
    return [REAL_PODMAN, *argv[: command_index + 1], *forwarded, target]


def validate_exec_user(value: str) -> None:
    user, separator, group = value.partition(":")
    identity = r"(?:[0-9]+|[A-Za-z_][A-Za-z0-9_.-]*)"
    if not re.fullmatch(identity, user) or (
        separator and not re.fullmatch(identity, group)
    ):
        reject("blocked invalid exec user", category=ViolationCategory.INPUT)


def validate_exec_environment(value: str) -> None:
    key, separator, _environment_value = value.partition("=")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        reject(
            "blocked invalid exec environment variable name",
            category=ViolationCategory.ENVIRONMENT,
        )
    if not separator:
        reject(
            "blocked implicit host environment forwarding during exec",
            category=ViolationCategory.ENVIRONMENT,
        )
    if is_sensitive_env_key(key):
        reject(
            "blocked sensitive environment forwarding during exec",
            category=ViolationCategory.ENVIRONMENT,
        )


def build_exec_command(argv: list[str], command_index: int) -> ExecCommand:
    arguments = argv[command_index + 1 :]
    forwarded: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            forwarded.append(argument)
            index += 1
            break
        if not argument.startswith("-"):
            break
        if argument in {"-d", "-i", "-t", "--detach", "--interactive", "--tty"}:
            forwarded.append(argument)
            index += 1
            continue
        if (
            len(argument) > 2
            and argument.startswith("-")
            and not argument.startswith("--")
            and all(flag in {"d", "i", "t"} for flag in argument[1:])
        ):
            forwarded.append(argument)
            index += 1
            continue
        if argument in {"-e", "-u", "-w", "--env", "--user", "--workdir"}:
            if index + 1 >= len(arguments):
                reject(
                    f"missing value for {argument}", category=ViolationCategory.INPUT
                )
            value = arguments[index + 1]
            if argument in {"-e", "--env"}:
                validate_exec_environment(value)
            elif argument in {"-u", "--user"}:
                validate_exec_user(value)
            elif not value.startswith("/") or re.search(r"[\x00-\x1f\x7f]", value):
                reject(
                    "blocked invalid exec working directory",
                    category=ViolationCategory.INPUT,
                )
            forwarded.extend((argument, value))
            index += 2
            continue
        if argument.startswith("--") and "=" in argument:
            option, value = argument.split("=", 1)
            if option == "--env":
                validate_exec_environment(value)
            elif option == "--user":
                validate_exec_user(value)
            elif option == "--workdir":
                if not value.startswith("/") or re.search(r"[\x00-\x1f\x7f]", value):
                    reject(
                        "blocked invalid exec working directory",
                        category=ViolationCategory.INPUT,
                    )
            else:
                reject(
                    "blocked unreviewed exec option",
                    category=ViolationCategory.UNSUPPORTED,
                )
            forwarded.append(argument)
            index += 1
            continue
        if len(argument) > 2 and argument[:2] in {"-e", "-u", "-w"}:
            option = argument[:2]
            value = argument[2:]
            if option == "-e":
                validate_exec_environment(value)
            elif option == "-u":
                validate_exec_user(value)
            elif not value.startswith("/") or re.search(r"[\x00-\x1f\x7f]", value):
                reject(
                    "blocked invalid exec working directory",
                    category=ViolationCategory.INPUT,
                )
            forwarded.append(argument)
            index += 1
            continue
        reject("blocked unreviewed exec option", category=ViolationCategory.UNSUPPORTED)

    remaining = arguments[index:]
    if len(remaining) < 2:
        reject(
            "exec requires a container and command", category=ViolationCategory.POLICY
        )
    validate_container_reference(remaining[0])
    return ExecCommand(
        remaining[0], [REAL_PODMAN, *argv[: command_index + 1], *forwarded, *remaining]
    )


def build_pull_or_push_command(argv: list[str]) -> list[str]:
    if len(argv) != 2:
        reject(
            f"blocked unreviewed {argv[0]} option",
            category=ViolationCategory.UNSUPPORTED,
        )
    validate_image_reference(argv[1])
    return [REAL_PODMAN, *argv]


def build_tag_command(argv: list[str]) -> list[str]:
    if len(argv) != 3:
        reject(
            "tag requires exactly one source and destination image",
            category=ViolationCategory.POLICY,
        )
    validate_image_reference(argv[1])
    validate_image_reference(argv[2])
    return [REAL_PODMAN, *argv]


def build_remove_command(argv: list[str], command_index: int) -> list[str]:
    """Allow explicit cleanup targets and options without requiring provenance."""

    arguments = argv[command_index + 1 :]
    target_count = 0
    parse_options = True
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        index += 1
        if parse_options and argument == "--":
            parse_options = False
            continue
        if parse_options and argument.startswith("-"):
            option, separator, value = argument.partition("=")
            if option in {"--force", "--ignore", "--volumes"} or re.fullmatch(
                r"-[fiv]+", option
            ):
                # Match the provider's explicit Boolean flag values.
                if separator and value not in {
                    "1",
                    "t",
                    "T",
                    "TRUE",
                    "true",
                    "True",
                    "0",
                    "f",
                    "F",
                    "FALSE",
                    "false",
                    "False",
                }:
                    reject(
                        "invalid Boolean container-removal option",
                        category=ViolationCategory.INPUT,
                    )
                continue
            short_time = re.fullmatch(r"-[fiv]*t(.*)", argument)
            if option == "--time" or short_time:
                attached = bool(separator)
                if short_time:
                    value = short_time.group(1)
                    attached = bool(value)
                    value = value.removeprefix("=")
                if not attached:
                    if index >= len(arguments):
                        reject(
                            "missing value for container-removal timeout",
                            category=ViolationCategory.INPUT,
                        )
                    value = arguments[index]
                    index += 1
                if value != "-1" and (
                    not re.fullmatch(r"[0-9]{1,19}", value) or int(value) > 2**63 - 1
                ):
                    reject(
                        "invalid container-removal timeout; use non-negative seconds or -1",
                        category=ViolationCategory.INPUT,
                    )
                continue
            reject(
                f"unsupported container-removal option {printable_option(argument)}; "
                "specify container names or IDs explicitly",
                category=ViolationCategory.UNSUPPORTED,
            )
        validate_container_reference(argument)
        target_count += 1
    if not target_count:
        reject("missing container to remove", category=ViolationCategory.INPUT)
    return [REAL_PODMAN, *argv]


def split_container_copy_endpoint(value: str) -> tuple[str, str] | None:
    container, separator, container_path = value.partition(":")
    if not separator:
        return None
    validate_container_reference(container)
    if container_path not in {"/etc/group", "/etc/passwd"}:
        reject(
            "blocked container copy path outside DevPod account setup",
            category=ViolationCategory.INPUT,
        )
    return container, container_path


def validate_devpod_temporary_file(value: str) -> None:
    candidate = Path(value)
    if not candidate.is_absolute() or ":" in value:
        reject(
            "blocked invalid DevPod temporary file", category=ViolationCategory.INPUT
        )
    try:
        if candidate.is_symlink():
            reject(
                "blocked symlinked DevPod temporary file",
                category=ViolationCategory.POLICY,
            )
        resolved = candidate.resolve(strict=True)
        metadata = resolved.stat()
    except (OSError, RuntimeError):
        reject(
            "blocked missing DevPod temporary file", category=ViolationCategory.INPUT
        )
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
        or not re.fullmatch(
            r"devpod_container_(?:group|passwd)_(?:in|out)[A-Za-z0-9]*",
            resolved.name,
        )
    ):
        reject(
            "blocked invalid DevPod temporary file", category=ViolationCategory.INPUT
        )


def copy_request(argv: list[str]) -> tuple[str, str]:
    if len(argv) != 3:
        reject(
            "blocked unreviewed container copy option",
            category=ViolationCategory.UNSUPPORTED,
        )
    source_container = split_container_copy_endpoint(argv[1])
    destination_container = split_container_copy_endpoint(argv[2])
    if (source_container is None) == (destination_container is None):
        reject(
            "blocked copy outside DevPod account setup",
            category=ViolationCategory.INPUT,
        )
    container = source_container or destination_container
    if container is None:  # Defensive; the exclusive check above prevents this.
        raise AssertionError("container copy endpoint was not selected")
    host_path = argv[2] if source_container is not None else argv[1]
    return container[0], host_path
