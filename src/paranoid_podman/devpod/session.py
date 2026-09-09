"""Run DevPod and protect its SSH block before opening an IDE."""

from __future__ import annotations

import shlex
import subprocess
from contextlib import nullcontext
from pathlib import Path

from paranoid_podman.devpod import agent as devpod_agent
from paranoid_podman.devpod import arguments as devpod_arguments
from paranoid_podman.devpod import errors as devpod_errors
from paranoid_podman.devpod import locks as devpod_locks
from paranoid_podman.devpod import models as devpod_models
from paranoid_podman.devpod import modes as devpod_modes
from paranoid_podman.devpod import paths as devpod_paths
from paranoid_podman.devpod import reporting as devpod_reporting
from paranoid_podman.devpod import ssh_config as devpod_ssh_config


def run_intercepted_devpod(
    real_devpod: Path,
    arguments: list[str],
    command: str,
    workspace: devpod_models.Workspace,
) -> int:
    devpod_arguments.reject_unreviewed_credential_forwarding(arguments)
    mode = devpod_modes.choose_command_mode(
        workspace,
        command,
        allow_interactive_configuration=not devpod_arguments.uses_protocol_stdio(
            arguments
        ),
    )
    paths = devpod_modes.prepare_mode(
        real_devpod,
        workspace,
        mode,
        allow_agent_prompt=devpod_modes.agent_prompt_allowed(arguments),
    )
    environment = devpod_modes.protected_environment(mode, paths)
    open_ide_after_protection = (
        command == "up"
        and devpod_arguments.boolean_option_value(arguments, "--open-ide", default=True)
    )
    run_arguments = (
        devpod_arguments.replace_boolean_option(arguments, "--open-ide", value=False)
        if command == "up"
        else arguments
    )
    patched = False
    operation_lock = (
        devpod_locks.runtime_lock(
            "devpod-workspace", devpod_agent.workspace_identity(workspace)
        )
        if command == "up"
        else nullcontext()
    )
    with operation_lock:
        result = subprocess.run(  # nosec B603
            [str(real_devpod), *run_arguments],
            env=environment,
            check=False,
        )
        if command == "up" or devpod_arguments.boolean_option_value(
            run_arguments, "--stdio", default=False
        ):
            socket = paths.socket if paths is not None else None
            patched = devpod_ssh_config.patch_ssh_block(
                workspace,
                mode,
                devpod_paths.wrapper_path(),
                socket,
            )
            ssh_configuration_disabled = not devpod_arguments.boolean_option_value(
                arguments, "--configure-ssh", default=True
            )
            if (
                result.returncode == 0
                and not patched
                and not ssh_configuration_disabled
            ):
                devpod_errors.fail(
                    "DevPod completed but did not create the expected managed SSH "
                    "block; workspace access was not marked as protected"
                )
    if result.returncode != 0 or not open_ide_after_protection:
        return result.returncode
    if not patched:
        devpod_errors.fail(
            "DevPod workspace creation completed without a protected SSH block; "
            "the IDE was not opened. Rerun without --configure-ssh=false or use "
            f"`paranoid-podman devpod configure {shlex.quote(workspace.name)}`"
        )
    devpod_reporting.info(
        "opening the IDE only after the DevPod SSH block was protected"
    )
    opened = subprocess.run(  # nosec B603
        [str(real_devpod), *devpod_arguments.ide_open_arguments(arguments, workspace)],
        env=environment,
        check=False,
    )
    return opened.returncode
