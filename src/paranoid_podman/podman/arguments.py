"""Runtime option tables and argument-form helpers."""

import re
from collections.abc import Iterator

from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.podman.errors import reject
from paranoid_podman.podman.models import CheckedOption, MountPolicy

VALUE_OPTIONS = {
    "-a",
    "-c",
    "-e",
    "-h",
    "-l",
    "-m",
    "-p",
    "-u",
    "-v",
    "-w",
    "--arch",
    "--add-host",
    "--attach",
    "--blkio-weight",
    "--cap-drop",
    "--cgroupns",
    "--cpu-period",
    "--cpu-quota",
    "--cpu-shares",
    "--cpus",
    "--cpuset-cpus",
    "--cpuset-mems",
    "--dns",
    "--dns-option",
    "--dns-search",
    "--entrypoint",
    "--env",
    "--env-file",
    "--expose",
    "--hostname",
    "--ipc",
    "--label",
    "--memory",
    "--memory-reservation",
    "--memory-swap",
    "--memory-swappiness",
    "--mount",
    "--name",
    "--net",
    "--network",
    "--network-alias",
    "--oom-score-adj",
    "--os",
    "--pid",
    "--pids-limit",
    "--platform",
    "--publish",
    "--pull",
    "--restart",
    "--security-opt",
    "--shm-size",
    "--stop-signal",
    "--stop-timeout",
    "--timeout",
    "--tmpfs",
    "--ulimit",
    "--umask",
    "--unsetenv",
    "--user",
    "--userns",
    "--uts",
    "--variant",
    "--volume",
    "--workdir",
}
FLAG_OPTIONS = {
    "-P",
    "-d",
    "-i",
    "-q",
    "-t",
    "--detach",
    "--init",
    "--interactive",
    "--no-healthcheck",
    "--no-hostname",
    "--no-hosts",
    "--quiet",
    "--publish-all",
    "--read-only",
    "--read-only-tmpfs",
    "--rm",
    "--tty",
    "--unsetenv-all",
}
FALSE_ONLY_OPTIONS = {
    "--env-host",
    "--http-proxy",
    "--privileged",
    "--sig-proxy",
}
SHORT_VALUE_OPTIONS = {
    "-a",
    "-c",
    "-e",
    "-h",
    "-l",
    "-m",
    "-p",
    "-u",
    "-v",
    "-w",
}
COMBINABLE_SHORT_FLAGS = {"P", "d", "i", "q", "t"}


def printable_option(argument: str) -> str:
    option = argument.split("=", 1)[0]
    if re.fullmatch(r"--[A-Za-z0-9][A-Za-z0-9-]*", option):
        return option
    if re.fullmatch(r"-[A-Za-z]+", option):
        return option
    return "an unrecognized option"


def has_arg(runtime: list[str], names: set[str]) -> bool:
    return any(option in names for option, _ in runtime_options(runtime))


def runtime_options(runtime: list[str]) -> Iterator[tuple[str, str | None]]:
    """Read already validated options without mistaking their values for flags."""
    index = 0
    while index < len(runtime):
        argument = runtime[index]
        if argument == "--":
            return
        if argument in VALUE_OPTIONS:
            yield argument, runtime[index + 1]
            index += 2
            continue
        if argument.startswith("--") and "=" in argument:
            option, value = argument.split("=", 1)
            yield option, value
        else:
            short_option, short_value = attached_short_value(argument)
            yield short_option or argument, short_value
        index += 1


def is_short_flag_bundle(argument: str) -> bool:
    return (
        len(argument) > 2
        and argument.startswith("-")
        and not argument.startswith("--")
        and all(flag in COMBINABLE_SHORT_FLAGS for flag in argument[1:])
    )


def attached_short_value(argument: str) -> tuple[str | None, str | None]:
    for option in SHORT_VALUE_OPTIONS:
        if argument.startswith(option) and argument != option:
            return option, argument[len(option) :]
    return None, None


def append_checked_option(
    runtime: list[str],
    mounts: list[MountPolicy],
    option: str,
    checked: CheckedOption,
    form: str,
) -> None:
    if checked.replacement is not None:
        runtime.extend(checked.replacement)
    elif checked.value is None:
        reject("blocked malformed runtime option", category=ViolationCategory.INPUT)
    elif form == "separate":
        runtime.extend([option, checked.value])
    elif form == "long":
        runtime.append(f"{option}={checked.value}")
    elif form == "short":
        runtime.append(f"{option}{checked.value}")
    else:  # Defensive: callers use a closed set of internal form names.
        raise AssertionError(f"unknown option form: {form}")
    if checked.mount is not None:
        mounts.append(checked.mount)
