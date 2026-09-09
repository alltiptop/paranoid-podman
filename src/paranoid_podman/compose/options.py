"""Compose option tables and argument token helpers."""

from __future__ import annotations

import re

from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.compose.errors import reject


def printable_option(argument: str) -> str:
    option = argument.split("=", 1)[0]
    if re.fullmatch(r"--[A-Za-z0-9][A-Za-z0-9-]*", option):
        return option
    if re.fullmatch(r"-[A-Za-z]", option):
        return option
    return "an unrecognized option"


def _option_value(arguments: list[str], index: int, option: str) -> tuple[str, int]:
    if len(option) == 2 and not option.startswith("--") and arguments[index] != option:
        value = arguments[index][2:].removeprefix("=")
        if not value:
            reject(f"missing value for {option}", category=ViolationCategory.INPUT)
        return value, index + 1
    if "=" in arguments[index]:
        value = arguments[index].split("=", 1)[1]
        if not value:
            reject(f"missing value for {option}", category=ViolationCategory.INPUT)
        return value, index + 1
    if index + 1 >= len(arguments) or not arguments[index + 1]:
        reject(f"missing value for {option}", category=ViolationCategory.INPUT)
    return arguments[index + 1], index + 2


COMMAND_FLAGS = {
    "build": {"--no-cache", "--pull", "--quiet"},
    "config": {"-q", "--quiet", "--services"},
    "down": {"-v", "--volumes", "--remove-orphans"},
    "exec": {"-T", "-d", "--detach"},
    "images": {"-q", "--quiet"},
    "logs": {
        "-f",
        "--follow",
        "-n",
        "--names",
        "--no-color",
        "--no-log-prefix",
        "-t",
        "--timestamps",
    },
    "port": set(),
    "pause": set(),
    "ps": {"-q", "--quiet"},
    "pull": {"--force-local"},
    "restart": set(),
    "run": {"--build", "-d", "--detach", "--no-deps", "--rm", "--service-ports", "-T"},
    "start": {"--wait"},
    "stop": set(),
    "unpause": set(),
    "up": {
        "-d",
        "--detach",
        "--no-color",
        "--quiet-pull",
        "--no-deps",
        "--no-recreate",
        "--no-start",
        "--abort-on-container-exit",
        "--abort-on-container-failure",
        "--wait",
        "--build",
        "--no-build",
        "--no-cache",
        "--force-recreate",
        "--always-recreate-deps",
        "--remove-orphans",
        "-V",
        "--renew-anon-volumes",
    },
    "wait": set(),
}
COMMAND_VALUE_OPTIONS = {
    "build": {"--build-arg"},
    "down": {"-t", "--timeout"},
    "exec": {"-e", "-u", "-w", "--env", "--index", "--user", "--workdir"},
    "logs": {"--since", "--tail", "--until"},
    "port": {"--index", "--protocol"},
    "restart": {"-t", "--timeout"},
    "run": {
        "--name",
        "--entrypoint",
        "-e",
        "--env",
        "-u",
        "--user",
        "-p",
        "--publish",
        "-w",
        "--workdir",
    },
    "start": {"--wait-timeout"},
    "stop": {"-t", "--timeout"},
    "up": {
        "-t",
        "--timeout",
        "--build-arg",
        "--exit-code-from",
        "--pull",
        "--scale",
        "--wait-timeout",
    },
}


def option_name(argument: str, value_options: set[str]) -> str:
    if not argument.startswith("--") and argument[:2] in value_options:
        return argument[:2]
    return argument.split("=", 1)[0]


def split_command_arguments(
    command: str, arguments: list[str]
) -> tuple[list[str], list[str]]:
    """Return normalized options and positional values for a supported command."""

    options: list[str] = []
    positionals: list[str] = []
    flags = COMMAND_FLAGS.get(command, set())
    value_options = COMMAND_VALUE_OPTIONS.get(command, set())
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        option = option_name(argument, value_options)
        if argument == "--":
            positionals.extend(arguments[index + 1 :])
            break
        if option in flags:
            if "=" in argument:
                reject(
                    f"option {option} does not accept a value",
                    category=ViolationCategory.POLICY,
                )
            options.append(argument)
            index += 1
            continue
        if option in value_options:
            value, index = _option_value(arguments, index, option)
            options.extend([option, value])
            continue
        if argument.startswith("-"):
            if command == "up" and option == "--replace":
                reject(
                    "podman-compose up does not support --replace; use "
                    "`podman compose up --force-recreate` for the same Compose project. "
                    "If a container name is still in use, check its Compose project "
                    "and select it with -p NAME; the wrapper's default project name "
                    "includes a path hash",
                    category=ViolationCategory.INPUT,
                )
            reject(
                f"blocked unknown or unsafe option {printable_option(argument)} for {command}",
                category=ViolationCategory.UNSUPPORTED,
            )
        positionals.append(argument)
        index += 1
    return options, positionals
