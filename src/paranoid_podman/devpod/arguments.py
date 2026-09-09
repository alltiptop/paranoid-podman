"""DevPod token boundaries, workspace selection, and IDE arguments."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

from paranoid_podman.devpod import errors as devpod_errors
from paranoid_podman.devpod import models as devpod_models
from paranoid_podman.devpod import paths as devpod_paths

INTERCEPTED_DEVPOD_COMMANDS = {"build", "ssh", "up"}
DEVPOD_VALUE_OPTIONS = {
    "--command",
    "--context",
    "--devcontainer-image",
    "--devcontainer-path",
    "--devpod-home",
    "--dotfiles",
    "--dotfiles-script",
    "--dotfiles-script-env",
    "--dotfiles-script-env-file",
    "--fallback-image",
    "--forward-ports",
    "-L",
    "--forward-ports-timeout",
    "--git-clone-strategy",
    "--git-ssh-signing-key",
    "--id",
    "--ide",
    "--ide-option",
    "--init-env",
    "--log-output",
    "--machine",
    "--prebuild-repository",
    "--platform",
    "--provider",
    "--provider-option",
    "--repository",
    "--reverse-forward-ports",
    "-R",
    "--send-env",
    "--set-env",
    "--source",
    "--ssh-config",
    "--ssh-keepalive-interval",
    "--tag",
    "--user",
    "--workdir",
    "--workspace-env",
    "--workspace-env-file",
}
PASSTHROUGH_DEVPOD_COMMANDS = {
    "completion",
    "context",
    "delete",
    "help",
    "ide",
    "list",
    "logs",
    "logs-daemon",
    "machine",
    "pro",
    "provider",
    "status",
    "stop",
    "use",
    "version",
}


def devpod_argument_indices(arguments: list[str]) -> Iterator[int]:
    """Visit options and operands, skipping values consumed by string flags."""

    index = 0
    while index < len(arguments):
        yield index
        if arguments[index] == "--":
            yield from range(index + 1, len(arguments))
            return
        if arguments[index] in DEVPOD_VALUE_OPTIONS:
            index += 1
        index += 1


def option_value(arguments: list[str], name: str) -> str | None:
    values = option_values(arguments, name)
    return values[-1] if values else None


def boolean_option_value(arguments: list[str], name: str, *, default: bool) -> bool:
    # pflag uses Go's strconv.ParseBool. A bare flag is true and does not
    # consume the following operand, even when that operand is named "false".
    true_values = {"1", "t", "T", "true", "TRUE", "True"}
    false_values = {"0", "f", "F", "false", "FALSE", "False"}
    value = default
    for index in devpod_argument_indices(arguments):
        argument = arguments[index]
        if argument == "--":
            break
        if argument == name:
            value = True
        elif argument.startswith(f"{name}="):
            raw_value = argument.split("=", 1)[1]
            if raw_value not in true_values | false_values:
                devpod_errors.fail(
                    f"{name} requires a boolean value; use {name}=true or =false"
                )
            value = raw_value in true_values
    return value


def append_devpod_options(arguments: list[str], *options: str) -> list[str]:
    for index in devpod_argument_indices(arguments):
        if arguments[index] == "--":
            return [*arguments[:index], *options, *arguments[index:]]
    return [*arguments, *options]


def replace_boolean_option(
    arguments: list[str], name: str, *, value: bool
) -> list[str]:
    removed: set[int] = set()
    for index in devpod_argument_indices(arguments):
        argument = arguments[index]
        if argument == "--":
            break
        if argument == name or argument.startswith(f"{name}="):
            removed.add(index)
    updated = [arg for index, arg in enumerate(arguments) if index not in removed]
    return append_devpod_options(updated, f"{name}={'true' if value else 'false'}")


def devpod_subcommand(arguments: list[str]) -> tuple[str | None, int]:
    """Return the top-level subcommand without reordering DevPod arguments."""

    positional_only = False
    for index in devpod_argument_indices(arguments):
        argument = arguments[index]
        if positional_only:
            return argument, index
        if argument == "--":
            positional_only = True
            continue
        if argument.startswith("-"):
            continue
        return argument, index
    return None, -1


def workspace_argument(arguments: list[str], command_index: int) -> str | None:
    explicit = option_value(arguments, "--id")
    if explicit:
        return explicit

    positional_only = False
    for index in devpod_argument_indices(arguments):
        if index <= command_index:
            continue
        argument = arguments[index]
        if positional_only:
            return argument
        if argument == "--":
            positional_only = True
            continue
        if argument.startswith("-"):
            continue
        return argument
    if arguments[command_index] in {"build", "up"}:
        return option_value(arguments, "--source") or "."
    return None


def canonical_workspace_name(value: str) -> str:
    if "\x00" in value or "\n" in value or "\r" in value:
        devpod_errors.fail("workspace name contains an invalid control character")
    candidate = value.rstrip("/")
    if candidate.endswith(".git"):
        candidate = candidate[:-4]
    candidate = candidate.rsplit("/", 1)[-1]
    if candidate in {"", "."}:
        candidate = Path.cwd().name
    if candidate.endswith(".devpod"):
        candidate = candidate[: -len(".devpod")]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", candidate):
        devpod_errors.fail(
            "could not derive a safe DevPod workspace id; pass an explicit "
            "--id using letters, digits, dots, underscores, or hyphens"
        )
    return candidate


def workspace_from_arguments(
    arguments: list[str], command_index: int
) -> devpod_models.Workspace:
    raw_workspace = workspace_argument(arguments, command_index)
    if raw_workspace is None:
        devpod_errors.fail(
            "a workspace name or path is required for guarded DevPod access"
        )
    context = option_value(arguments, "--context") or "default"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", context):
        devpod_errors.fail("DevPod context name contains unsupported characters")
    raw_config = option_value(arguments, "--ssh-config")
    ssh_config = (
        devpod_paths.normalize_path(raw_config, base=Path.cwd())
        if raw_config
        else devpod_paths.default_ssh_config()
    )
    devpod_home = option_value(arguments, "--devpod-home") or os.environ.get(
        "DEVPOD_HOME"
    )
    return devpod_models.Workspace(
        context,
        canonical_workspace_name(raw_workspace),
        ssh_config,
        devpod_home,
    )


def option_values(arguments: list[str], name: str) -> list[str]:
    values: list[str] = []
    prefix = f"{name}="
    for index in devpod_argument_indices(arguments):
        argument = arguments[index]
        if argument == "--":
            break
        if argument.startswith(prefix):
            values.append(argument[len(prefix) :])
        elif argument == name and index + 1 < len(arguments):
            values.append(arguments[index + 1])
    return values


def ide_open_arguments(
    arguments: list[str], workspace: devpod_models.Workspace
) -> list[str]:
    opened: list[str] = ["--context", workspace.context]
    if workspace.devpod_home:
        opened.extend(("--devpod-home", workspace.devpod_home))
    for name in ("--log-output", "--provider"):
        value = option_value(arguments, name)
        if value is not None:
            opened.extend((name, value))
    for name in ("--debug", "--silent"):
        if boolean_option_value(arguments, name, default=False):
            opened.append(name)
    opened.extend(("up", workspace.name))
    for name in ("--ide", "--machine", "--ssh-config"):
        value = option_value(arguments, name)
        if value is not None:
            opened.extend((name, value))
    for name in ("--disable-daemon", "--proxy"):
        if boolean_option_value(arguments, name, default=False):
            opened.append(name)
    for name in ("--ide-option", "--provider-option"):
        for value in option_values(arguments, name):
            opened.extend((name, value))
    opened.extend(("--open-ide=true", "--configure-ssh=false"))
    return opened


def uses_protocol_stdio(arguments: list[str]) -> bool:
    return any(
        boolean_option_value(arguments, name, default=False)
        for name in ("--stdio", "--proxy")
    )


def reject_unreviewed_credential_forwarding(arguments: list[str]) -> None:
    if boolean_option_value(arguments, "--gpg-agent-forwarding", default=False):
        devpod_errors.fail(
            "DevPod GPG-agent forwarding is blocked because it exposes another "
            "host signing capability"
        )
    if option_value(arguments, "--git-ssh-signing-key") is not None:
        devpod_errors.fail(
            "DevPod SSH signing-key forwarding is blocked; the selected project "
            "agent is limited to repository authentication"
        )
