"""Select and prepare SSH access with explicit protocol and prompt rules."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from typing import TextIO

from paranoid_podman.devpod import agent as devpod_agent
from paranoid_podman.devpod import arguments as devpod_arguments
from paranoid_podman.devpod import context as devpod_context
from paranoid_podman.devpod import errors as devpod_errors
from paranoid_podman.devpod import keys as devpod_keys
from paranoid_podman.devpod import models as devpod_models
from paranoid_podman.devpod import reporting as devpod_reporting
from paranoid_podman.devpod import ssh_config as devpod_ssh_config
from paranoid_podman.devpod import terminal as devpod_terminal


def infer_mode(workspace: devpod_models.Workspace) -> devpod_models.SSHMode | None:
    text, _mode = devpod_ssh_config.read_ssh_config(workspace.ssh_config)
    bounds = devpod_ssh_config.devpod_block_bounds(text, workspace.host)
    if bounds is not None:
        lines, start, end = bounds
        forwarding = devpod_ssh_config.block_option(lines, start, end, "ForwardAgent")
        identity_agent = devpod_ssh_config.block_option(
            lines, start, end, "IdentityAgent"
        )
        if forwarding is not None and forwarding.lower() == "no":
            return devpod_models.SSHMode.IDE_ONLY
        identity = devpod_keys.managed_identity(workspace)
        if identity is not None:
            return devpod_models.SSHMode.PROJECT_KEY
        if identity_agent and identity_agent.lower() != "none":
            devpod_errors.fail(
                "DevPod SSH block names an agent but its managed project key is "
                "missing; run `paranoid-podman devpod configure`"
            )
        return None
    if devpod_keys.managed_identity(workspace) is not None:
        return devpod_models.SSHMode.PROJECT_KEY
    return None


def prompt_choice(
    workspace: devpod_models.Workspace, input_stream: TextIO = sys.stdin
) -> devpod_models.SSHMode:
    saved_identity = devpod_keys.managed_identity(workspace)
    devpod_reporting.warning(
        "DevPod can forward every identity in your normal SSH agent. Code in "
        "the workspace could use those identities without reading private-key files."
    )
    print(f"\nChoose SSH access for {workspace.name}:", file=sys.stderr)
    if saved_identity is None:
        print("  1. Create a project-only Git key (recommended)", file=sys.stderr)
    else:
        print("  1. Use the saved project-only Git key (recommended)", file=sys.stderr)
    print(
        "  2. IDE only - expose no Git, SSH, or registry credentials",
        file=sys.stderr,
    )
    print("  3. Use an existing project-only key", file=sys.stderr)
    print("  4. Continue with the full host agent (unsafe, one time)", file=sys.stderr)
    print("  5. Cancel", file=sys.stderr)
    while True:
        print("Selection [1]: ", end="", file=sys.stderr, flush=True)
        choice = devpod_terminal.read_interactive_line(input_stream) or "1"
        if choice == "1":
            if saved_identity is None:
                devpod_keys.create_project_key(workspace)
            return devpod_models.SSHMode.PROJECT_KEY
        if choice == "2":
            return devpod_models.SSHMode.IDE_ONLY
        if choice == "3":
            print(
                "Project-only private key path: ",
                end="",
                file=sys.stderr,
                flush=True,
            )
            selected = devpod_terminal.read_interactive_line(input_stream)
            try:
                devpod_keys.select_project_key(workspace, selected)
            except devpod_errors.DevPodGuardError as error:
                devpod_reporting.warning(str(error))
                continue
            return devpod_models.SSHMode.PROJECT_KEY
        if choice == "4":
            devpod_reporting.danger(
                "the full ambient SSH agent will be available to this workspace "
                "for this run"
            )
            print("Type y to continue unsafely: ", end="", file=sys.stderr, flush=True)
            if devpod_terminal.read_interactive_line(input_stream) == "y":
                return devpod_models.SSHMode.UNSAFE_AMBIENT
            continue
        if choice == "5":
            devpod_errors.fail("cancelled before running DevPod")
        devpod_reporting.warning("enter a number from 1 to 5")


def agent_prompt_allowed(arguments: list[str]) -> bool:
    return not devpod_arguments.uses_protocol_stdio(arguments) and sys.stdin.isatty()


def choose_mode(
    workspace: devpod_models.Workspace, *, allow_interactive_configuration: bool = True
) -> devpod_models.SSHMode:
    existing = infer_mode(workspace)
    if existing is not None:
        return existing
    if not allow_interactive_configuration:
        devpod_errors.fail(
            "SSH credential mode is not configured for this workspace; run "
            f"`paranoid-podman devpod configure {shlex.quote(workspace.name)}` "
            "before reconnecting"
        )
    stream = devpod_terminal.interactive_stream()
    if stream is None:
        devpod_errors.fail(
            "SSH credential mode is not configured for this workspace; run "
            f"`paranoid-podman devpod configure {shlex.quote(workspace.name)}` "
            "from an interactive terminal"
        )
    try:
        return prompt_choice(workspace, stream)
    finally:
        if stream is not sys.stdin:
            stream.close()


def choose_command_mode(
    workspace: devpod_models.Workspace,
    command: str,
    *,
    allow_interactive_configuration: bool,
) -> devpod_models.SSHMode:
    if command != "build":
        return choose_mode(
            workspace,
            allow_interactive_configuration=allow_interactive_configuration,
        )
    existing = infer_mode(workspace)
    if existing is not None:
        return existing
    devpod_reporting.info(
        "no SSH credential mode is configured for this build; continuing "
        "without host SSH, Git, or registry credentials"
    )
    return devpod_models.SSHMode.IDE_ONLY


def protected_environment(
    mode: devpod_models.SSHMode, paths: devpod_models.AgentPaths | None
) -> dict[str, str]:
    environment = os.environ.copy()
    if mode == devpod_models.SSHMode.PROJECT_KEY:
        if paths is None:
            devpod_errors.fail("project-key mode has no dedicated agent")
        environment["SSH_AUTH_SOCK"] = str(paths.socket)
        pid = devpod_agent.read_agent_pid(paths)
        if pid is not None:
            environment["SSH_AGENT_PID"] = str(pid)
        else:
            environment.pop("SSH_AGENT_PID", None)
    elif mode == devpod_models.SSHMode.IDE_ONLY:
        environment.pop("SSH_AUTH_SOCK", None)
        environment.pop("SSH_AGENT_PID", None)
    return environment


def prepare_mode(
    real_devpod: Path,
    workspace: devpod_models.Workspace,
    mode: devpod_models.SSHMode,
    *,
    allow_agent_prompt: bool = True,
) -> devpod_models.AgentPaths | None:
    if mode == devpod_models.SSHMode.UNSAFE_AMBIENT:
        devpod_reporting.danger(
            "unsafe ambient SSH-agent access is enabled for this run; this "
            "decision is intentionally not remembered"
        )
        return None
    devpod_context.apply_safe_context_options(real_devpod, workspace)
    if mode == devpod_models.SSHMode.IDE_ONLY:
        devpod_reporting.debug(
            "DevPod access remains enabled; Git or registry operations "
            "requiring host credentials may fail"
        )
        return None
    identity = devpod_keys.managed_identity(workspace)
    if identity is None:
        devpod_errors.fail(
            "project-key mode is selected but no managed identity exists"
        )
    return devpod_agent.ensure_agent(
        workspace, identity, allow_prompt=allow_agent_prompt
    )
