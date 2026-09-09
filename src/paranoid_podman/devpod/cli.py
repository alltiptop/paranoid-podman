"""Discover, validate, and route guarded DevPod commands."""

from __future__ import annotations

import os
import sys

from paranoid_podman.devpod import arguments as devpod_arguments
from paranoid_podman.devpod import context as devpod_context
from paranoid_podman.devpod import errors as devpod_errors
from paranoid_podman.devpod import paths as devpod_paths
from paranoid_podman.devpod import provider as devpod_provider
from paranoid_podman.devpod import session as devpod_session


def run_devpod(arguments: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if arguments is None else arguments)
    own_entry_point = devpod_paths.normalize_path(
        os.environ.get("PARANOID_PODMAN_DEVPOD_WRAPPER", sys.argv[0])
    )
    real_devpod = devpod_provider.discover_real_devpod(own_entry_point)
    devpod_provider.validate_devpod_version(real_devpod)
    command, command_index = devpod_arguments.devpod_subcommand(argv)
    if command is None or command in devpod_arguments.PASSTHROUGH_DEVPOD_COMMANDS:
        os.execve(str(real_devpod), [str(real_devpod), *argv], os.environ.copy())
        return 126
    if command == "upgrade":
        devpod_errors.fail(
            "DevPod self-upgrade through the wrapper is disabled to preserve "
            "saved command backups; update DevPod using its original installation "
            "method, then run ./install.sh update"
        )
    if command not in devpod_arguments.INTERCEPTED_DEVPOD_COMMANDS:
        devpod_errors.fail(f"unreviewed DevPod command is blocked: {command}")
    if any(
        devpod_arguments.boolean_option_value(argv, name, default=False)
        for name in ("--help", "-h")
    ):
        os.execve(str(real_devpod), [str(real_devpod), *argv], os.environ.copy())
        return 126
    workspace = devpod_arguments.workspace_from_arguments(argv, command_index)
    if devpod_arguments.option_value(argv, "--context") is None:
        workspace = devpod_context.resolve_selected_context(real_devpod, workspace)
        argv = devpod_arguments.append_devpod_options(
            argv, f"--context={workspace.context}"
        )
    if devpod_arguments.option_value(argv, "--ssh-config") is None:
        workspace = devpod_context.resolve_context_ssh_config(real_devpod, workspace)
    return devpod_session.run_intercepted_devpod(real_devpod, argv, command, workspace)
