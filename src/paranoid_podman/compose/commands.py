"""Command-specific option validation and requested service checks."""

from __future__ import annotations

import re

from paranoid_podman.common.environment import is_sensitive_env_key
from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.common.ports import is_published_port
from paranoid_podman.compose.dotenv import DOTENV_KEY_PATTERN
from paranoid_podman.compose.errors import reject
from paranoid_podman.compose.models import Invocation
from paranoid_podman.compose.options import (
    COMMAND_FLAGS,
    COMMAND_VALUE_OPTIONS,
    _option_value,
    option_name,
    printable_option,
    split_command_arguments,
)
from paranoid_podman.compose.schema import NAME_PATTERN, _valid_name
from paranoid_podman.compose.settings import MAX_SCALE


def validate_ls_syntax(arguments: list[str], project_name: str) -> None:
    """Accept the metadata query used by DevPod to recover Compose files."""

    expected_filter = f"name={project_name}"
    seen_all = False
    seen_filter = False
    seen_format = False
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in {"-a", "--all"} and not seen_all:
            seen_all = True
            index += 1
            continue
        if argument == "--filter" or argument.startswith("--filter="):
            if seen_filter:
                reject(
                    "Compose ls filter may be specified only once",
                    category=ViolationCategory.POLICY,
                )
            value, index = _option_value(arguments, index, "--filter")
            if value != expected_filter:
                reject(
                    "Compose ls filter must match the selected project",
                    category=ViolationCategory.POLICY,
                )
            seen_filter = True
            continue
        if argument == "--format" or argument.startswith("--format="):
            if seen_format:
                reject(
                    "Compose ls format may be specified only once",
                    category=ViolationCategory.POLICY,
                )
            value, index = _option_value(arguments, index, "--format")
            if value != "json":
                reject(
                    "Compose ls supports only JSON output",
                    category=ViolationCategory.POLICY,
                )
            seen_format = True
            continue
        reject(
            f"blocked unknown or unsafe option {printable_option(argument)} for ls",
            category=ViolationCategory.UNSUPPORTED,
        )
    if not (seen_all and seen_filter and seen_format):
        reject(
            "Compose ls requires all-project, matching-filter, and JSON options",
            category=ViolationCategory.POLICY,
        )


def validate_version_arguments(arguments: list[str]) -> None:
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--short":
            index += 1
            continue
        if argument in {"-f", "--format"} or argument.startswith("--format="):
            value, index = _option_value(arguments, index, argument.split("=", 1)[0])
            if value not in {"json", "pretty"}:
                reject(
                    "unsupported Compose version format",
                    category=ViolationCategory.UNSUPPORTED,
                )
            continue
        reject(
            f"blocked unknown option {printable_option(argument)} for version",
            category=ViolationCategory.UNSUPPORTED,
        )


def _integer_option(
    options: list[str], names: set[str], minimum: int, maximum: int
) -> None:
    index = 0
    while index < len(options):
        option = options[index]
        if option in names:
            try:
                value = int(options[index + 1])
            except (IndexError, ValueError):
                reject(
                    f"invalid numeric value for {option}",
                    category=ViolationCategory.INPUT,
                )
            if not minimum <= value <= maximum:
                reject(
                    f"numeric value for {option} is outside the guarded range",
                    category=ViolationCategory.INPUT,
                )
            index += 2
        else:
            index += 1


def validate_build_cli_options(
    options: list[str], *, pull_requires_value: bool
) -> None:
    index = 0
    while index < len(options):
        option = options[index]
        if option == "--pull":
            if not pull_requires_value:
                index += 1
                continue
            if options[index + 1] not in {"always", "missing", "never", "newer"}:
                reject(
                    "unsupported Compose pull policy", category=ViolationCategory.PULL
                )
            index += 2
        elif option == "--build-arg":
            key, separator, _value = options[index + 1].partition("=")
            if not separator or not DOTENV_KEY_PATTERN.fullmatch(key):
                reject(
                    "Compose build arguments must use KEY=VALUE",
                    category=ViolationCategory.POLICY,
                )
            if is_sensitive_env_key(key):
                reject(
                    "sensitive build arguments must come from project interpolation",
                    category=ViolationCategory.POLICY,
                )
            index += 2
        else:
            index += 1


def validate_exec_user(value: str) -> None:
    identity = r"(?:[0-9]+|[A-Za-z_][A-Za-z0-9_.-]*)"
    user, separator, group = value.partition(":")
    if not re.fullmatch(identity, user) or (
        separator and not re.fullmatch(identity, group)
    ):
        reject("invalid Compose container user", category=ViolationCategory.INPUT)


def validate_exec_environment(value: str) -> None:
    key, separator, _environment_value = value.partition("=")
    if not separator or not DOTENV_KEY_PATTERN.fullmatch(key):
        reject(
            "Compose exec environment must use KEY=VALUE",
            category=ViolationCategory.ENVIRONMENT,
        )
    if is_sensitive_env_key(key):
        reject(
            "blocked sensitive environment forwarding during Compose exec",
            category=ViolationCategory.ENVIRONMENT,
        )


def validate_exec_syntax(arguments: list[str]) -> str:
    """Validate options before the service and leave its command opaque."""

    value_options = COMMAND_VALUE_OPTIONS["exec"]
    seen_singletons: set[str] = set()
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            index += 1
            break
        if not argument.startswith("-"):
            break
        if argument in COMMAND_FLAGS["exec"]:
            index += 1
            continue
        option = option_name(argument, value_options)
        if option not in value_options:
            reject(
                "blocked unknown or unsafe option "
                f"{printable_option(argument)} for exec",
                category=ViolationCategory.UNSUPPORTED,
            )
        value, index = _option_value(arguments, index, option)
        if option in {"-e", "--env"}:
            validate_exec_environment(value)
        elif option in {"-u", "--user"}:
            if "user" in seen_singletons:
                reject(
                    "Compose exec user may be specified only once",
                    category=ViolationCategory.POLICY,
                )
            validate_exec_user(value)
            seen_singletons.add("user")
        elif option in {"-w", "--workdir"}:
            if "workdir" in seen_singletons:
                reject(
                    "Compose exec workdir may be specified only once",
                    category=ViolationCategory.POLICY,
                )
            if not value.startswith("/") or re.search(r"[\x00-\x1f\x7f]", value):
                reject(
                    "blocked invalid Compose exec working directory",
                    category=ViolationCategory.INPUT,
                )
            seen_singletons.add("workdir")
        else:
            if "index" in seen_singletons:
                reject(
                    "Compose exec index may be specified only once",
                    category=ViolationCategory.POLICY,
                )
            if not value.isdigit() or not 1 <= int(value) <= MAX_SCALE:
                reject(
                    "Compose exec index is outside the guarded range",
                    category=ViolationCategory.INPUT,
                )
            seen_singletons.add("index")

    remaining = arguments[index:]
    if len(remaining) < 2:
        reject(
            "Compose exec requires a service and command",
            category=ViolationCategory.POLICY,
        )
    service = remaining[0]
    if not NAME_PATTERN.fullmatch(service):
        reject("blocked invalid Compose exec service", category=ViolationCategory.INPUT)
    if not remaining[1] or re.search(r"[\x00\r\n]", remaining[1]):
        reject("blocked invalid Compose exec command", category=ViolationCategory.INPUT)
    return service


def run_service_index(arguments: list[str]) -> int:
    """Review one-off options; everything after the service is container argv."""

    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            index += 1
            break
        if not argument.startswith("-"):
            break
        if argument in COMMAND_FLAGS["run"]:
            index += 1
            continue
        option = option_name(argument, COMMAND_VALUE_OPTIONS["run"])
        if option not in COMMAND_VALUE_OPTIONS["run"]:
            reject(
                f"blocked unknown or unsafe option {printable_option(argument)} for run",
                category=ViolationCategory.UNSUPPORTED,
            )
        value, index = _option_value(arguments, index, option)
        if option in {"-e", "--env"}:
            key, separator, _ = value.partition("=")
            if (
                not separator
                or not DOTENV_KEY_PATTERN.fullmatch(key)
                or is_sensitive_env_key(key)
            ):
                reject(
                    "Compose run environment requires explicit non-sensitive KEY=VALUE",
                    category=ViolationCategory.ENVIRONMENT,
                )
        elif option in {"-u", "--user"}:
            validate_exec_user(value)
        elif option in {"-w", "--workdir"}:
            if not value.startswith("/") or re.search(r"[\x00-\x1f\x7f]", value):
                reject("invalid Compose run workdir", category=ViolationCategory.INPUT)
        elif option == "--name":
            if not _valid_name(value):
                reject("invalid Compose run name", category=ViolationCategory.INPUT)
        elif option in {"-p", "--publish"}:
            if not is_published_port(value):
                reject(
                    "invalid Compose run published port",
                    category=ViolationCategory.PORT,
                )
        elif re.search(r"[\x00\r\n]", value):
            reject("invalid Compose run entrypoint", category=ViolationCategory.INPUT)

    remaining = arguments[index:]
    if not remaining or not _valid_name(remaining[0]):
        reject("Compose run requires a valid service", category=ViolationCategory.INPUT)
    if len(remaining) > 1 and (
        not remaining[1] or re.search(r"[\x00\r\n]", remaining[1])
    ):
        reject("invalid Compose run command", category=ViolationCategory.INPUT)
    return index


def validate_run_syntax(arguments: list[str]) -> str:
    return arguments[run_service_index(arguments)]


def run_build_request(arguments: list[str]) -> tuple[str | None, list[str]]:
    service_index = run_service_index(arguments)
    forwarded: list[str] = []
    build_service = None
    index = 0
    while index < service_index:
        argument = arguments[index]
        if argument == "--build":
            build_service = arguments[service_index]
            index += 1
            continue
        option = option_name(argument, COMMAND_VALUE_OPTIONS["run"])
        next_index = index + 1
        if option in COMMAND_VALUE_OPTIONS["run"]:
            _, next_index = _option_value(arguments, index, option)
        forwarded.extend(arguments[index:next_index])
        index = next_index
    return build_service, forwarded + arguments[service_index:]


def validate_command_syntax(command: str, arguments: list[str]) -> None:
    if command == "run":
        validate_run_syntax(arguments)
        return
    if command == "exec":
        validate_exec_syntax(arguments)
        return
    options, positionals = split_command_arguments(command, arguments)
    if command == "config":
        invalid_option = any(
            option not in {"-q", "--quiet", "--services"} for option in options
        )
        quiet = "-q" in options or "--quiet" in options
        services = "--services" in options
        if positionals or not options or invalid_option or (quiet and services):
            reject(
                "config requires --quiet or --services; resolved values are never printed",
                category=ViolationCategory.POLICY,
            )
    elif command in {"images", "ps", "wait"} and positionals:
        reject(
            f"positional arguments are not supported for Compose {command}",
            category=ViolationCategory.POLICY,
        )
    elif command == "port":
        if len(positionals) != 2:
            reject(
                "Compose port requires a service and private port",
                category=ViolationCategory.POLICY,
            )
        try:
            private_port = int(positionals[1])
        except ValueError:
            reject("invalid private port", category=ViolationCategory.INPUT)
        if not 1 <= private_port <= 65535:
            reject(
                "private port is outside the allowed range",
                category=ViolationCategory.INPUT,
            )
        _integer_option(options, {"--index"}, 1, 1000)
        for index, option in enumerate(options):
            if option == "--protocol" and options[index + 1] not in {"tcp", "udp"}:
                reject(
                    "Compose port protocol must be tcp or udp",
                    category=ViolationCategory.POLICY,
                )
    elif command == "logs":
        for index, option in enumerate(options):
            if option == "--tail":
                value = options[index + 1]
                if value != "all" and (not value.isdigit() or int(value) > 1_000_000):
                    reject(
                        "invalid Compose log tail limit",
                        category=ViolationCategory.INPUT,
                    )
    elif command == "build":
        validate_build_cli_options(options, pull_requires_value=False)
    elif command == "down":
        if positionals:
            reject(
                "positional arguments are not supported for Compose down",
                category=ViolationCategory.POLICY,
            )
        _integer_option(options, {"-t", "--timeout"}, 0, 3600)
    elif command in {"restart", "stop"}:
        _integer_option(options, {"-t", "--timeout"}, 0, 3600)
    elif command == "start":
        _integer_option(options, {"--wait-timeout"}, 0, 3600)
    elif command == "up":
        _integer_option(options, {"-t", "--timeout", "--wait-timeout"}, 0, 3600)
        validate_build_cli_options(options, pull_requires_value=True)
        for index, option in enumerate(options):
            if option == "--scale":
                match = re.fullmatch(
                    r"([a-zA-Z0-9][a-zA-Z0-9_.-]*)=([0-9]+)",
                    options[index + 1],
                )
                if not match or int(match.group(2)) > MAX_SCALE:
                    reject(
                        "Compose scale must use SERVICE=NUM with NUM at most "
                        f"{MAX_SCALE}",
                        category=ViolationCategory.POLICY,
                    )


def validate_requested_services(
    invocation: Invocation, service_names: set[str]
) -> None:
    if invocation.command in {"exec", "run"}:
        validator = (
            validate_exec_syntax
            if invocation.command == "exec"
            else validate_run_syntax
        )
        requested = [validator(invocation.command_args)]
        if any(service not in service_names for service in requested):
            reject(
                "the command refers to an unknown Compose service",
                category=ViolationCategory.UNSUPPORTED,
            )
        return
    options, positionals = split_command_arguments(
        invocation.command, invocation.command_args
    )
    requested = list(positionals)
    for index, option in enumerate(options):
        if option == "--exit-code-from":
            requested.append(options[index + 1])
        elif option == "--scale":
            requested.append(options[index + 1].split("=", 1)[0])
    if invocation.command == "port" and requested:
        requested = requested[:1]
    if any(service not in service_names for service in requested):
        reject(
            "the command refers to an unknown Compose service",
            category=ViolationCategory.UNSUPPORTED,
        )
