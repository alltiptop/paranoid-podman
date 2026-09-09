"""Read and apply protected DevPod context options."""

from __future__ import annotations

import json
import re
from pathlib import Path

from paranoid_podman.devpod import errors as devpod_errors
from paranoid_podman.devpod import locks as devpod_locks
from paranoid_podman.devpod import models as devpod_models
from paranoid_podman.devpod import paths as devpod_paths
from paranoid_podman.devpod import process as devpod_process
from paranoid_podman.devpod import reporting as devpod_reporting

SAFE_CONTEXT_OPTIONS = {
    "GIT_SSH_SIGNATURE_FORWARDING": "false",
    "GPG_AGENT_FORWARDING": "false",
    "SSH_ADD_PRIVATE_KEYS": "false",
    # DevPod needs its forwarding transport for a dedicated project agent.
    # Safety comes from replacing SSH_AUTH_SOCK, not from disabling transport.
    "SSH_AGENT_FORWARDING": "true",
    "SSH_INJECT_DOCKER_CREDENTIALS": "false",
    "SSH_INJECT_GIT_CREDENTIALS": "false",
}


def context_command(
    real_devpod: Path,
    action: str,
    workspace: devpod_models.Workspace,
) -> list[str]:
    arguments = [
        str(real_devpod),
        "context",
        action,
        "--context",
        workspace.context,
    ]
    if workspace.devpod_home:
        arguments.extend(("--devpod-home", workspace.devpod_home))
    return arguments


def context_options(
    real_devpod: Path, workspace: devpod_models.Workspace
) -> dict[str, str]:
    result = devpod_process.run_checked(
        context_command(real_devpod, "options", workspace) + ["--output", "json"]
    )
    try:
        raw_options = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise devpod_errors.DevPodGuardError(
            "DevPod returned invalid context-option JSON"
        ) from error
    if not isinstance(raw_options, dict):
        devpod_errors.fail("DevPod returned invalid context options")
    values: dict[str, str] = {}
    for name, item in raw_options.items():
        if not isinstance(name, str) or not isinstance(item, dict):
            continue
        value = item.get("value", item.get("default"))
        if isinstance(value, str):
            values[name] = value
    return values


def apply_safe_context_options(
    real_devpod: Path, workspace: devpod_models.Workspace
) -> None:
    identity = f"{workspace.devpod_home or '<default>'}\0{workspace.context}"
    with devpod_locks.runtime_lock("devpod-context", identity):
        apply_safe_context_options_unlocked(real_devpod, workspace)


def apply_safe_context_options_unlocked(
    real_devpod: Path, workspace: devpod_models.Workspace
) -> None:
    current = context_options(real_devpod, workspace)
    missing = [name for name in SAFE_CONTEXT_OPTIONS if name not in current]
    if missing:
        devpod_errors.fail(
            "DevPod context does not expose reviewed options: " + ", ".join(missing)
        )
    changes = {
        name: value
        for name, value in SAFE_CONTEXT_OPTIONS.items()
        if current.get(name) != value
    }
    if changes:
        arguments = context_command(real_devpod, "set-options", workspace)
        for name, value in changes.items():
            arguments.extend(("--option", f"{name}={value}"))
        # stdout belongs to the SSH protocol when invoked as a ProxyCommand.
        devpod_process.run_checked(arguments)
        effective = context_options(real_devpod, workspace)
        rejected = [
            name
            for name, value in SAFE_CONTEXT_OPTIONS.items()
            if effective.get(name) != value
        ]
        if rejected:
            devpod_errors.fail(
                "DevPod did not retain required protected context options: "
                + ", ".join(rejected)
            )
    devpod_reporting.debug(
        "DevPod automatic private-key discovery and Git, registry, and signing "
        f"credential injection are disabled in context {workspace.context}; "
        "agent transport remains available only for the wrapper-selected socket"
    )


def resolve_context_ssh_config(
    real_devpod: Path, workspace: devpod_models.Workspace
) -> devpod_models.Workspace:
    options = context_options(real_devpod, workspace)
    configured = options.get("SSH_CONFIG_PATH")
    if not configured:
        return workspace
    return devpod_models.Workspace(
        workspace.context,
        workspace.name,
        devpod_paths.normalize_path(configured),
        workspace.devpod_home,
    )


def resolve_selected_context(
    real_devpod: Path, workspace: devpod_models.Workspace
) -> devpod_models.Workspace:
    arguments = [str(real_devpod), "context", "list", "--output", "json"]
    if workspace.devpod_home:
        arguments.extend(("--devpod-home", workspace.devpod_home))
    result = devpod_process.run_checked(arguments)
    try:
        contexts = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise devpod_errors.DevPodGuardError(
            "DevPod returned invalid context metadata"
        ) from error
    selected = (
        [
            item.get("name")
            for item in contexts
            if isinstance(item, dict) and item.get("default") is True
        ]
        if isinstance(contexts, list)
        else []
    )
    if (
        len(selected) != 1
        or not isinstance(selected[0], str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", selected[0]) is None
    ):
        devpod_errors.fail("could not identify the selected DevPod context")
    return devpod_models.Workspace(
        selected[0],
        workspace.name,
        workspace.ssh_config,
        workspace.devpod_home,
    )
