"""Parse and route the paranoid-podman management commands."""

from __future__ import annotations

import argparse
import sys

from paranoid_podman.devpod import agent as devpod_agent
from paranoid_podman.devpod import context as devpod_context
from paranoid_podman.devpod import errors as devpod_errors
from paranoid_podman.devpod import paths as devpod_paths
from paranoid_podman.devpod import provider as devpod_provider
from paranoid_podman.management import build_context as management_build_context
from paranoid_podman.management import devpod as management_devpod


def parse_management_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="paranoid-podman",
        description="Manage paranoid-podman development safeguards.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    devpod = commands.add_parser("devpod", help="manage DevPod SSH isolation")
    devpod_commands = devpod.add_subparsers(dest="devpod_command", required=True)

    def workspace_parser(name: str, help_text: str) -> argparse.ArgumentParser:
        command = devpod_commands.add_parser(name, help=help_text)
        command.add_argument("workspace")
        command.add_argument("--context")
        command.add_argument("--devpod-home")
        command.add_argument("--ssh-config")
        return command

    workspace_parser("audit", "show the effective workspace SSH mode")
    workspace_parser("configure", "choose protected SSH access")
    key = devpod_commands.add_parser("key", help="inspect or stop a project agent")
    key_commands = key.add_subparsers(dest="key_command", required=True)
    for name in ("show", "stop"):
        command = key_commands.add_parser(name)
        command.add_argument("workspace")
        command.add_argument("--context")
        command.add_argument("--devpod-home")
        command.add_argument("--ssh-config")

    build_context = commands.add_parser(
        "build-context", help="audit or repair build-context exclusions"
    )
    build_commands = build_context.add_subparsers(
        dest="build_context_command", required=True
    )
    audit = build_commands.add_parser("audit")
    audit.add_argument("path", nargs="?", default=".")
    protect = build_commands.add_parser("protect")
    protect.add_argument("path", nargs="?", default=".")
    protect.add_argument(
        "--all",
        action="store_true",
        help="exclude every reported sensitive path without prompting",
    )
    return parser.parse_args(arguments)


def management_main(arguments: list[str] | None = None) -> int:
    options = parse_management_arguments(
        list(sys.argv[1:] if arguments is None else arguments)
    )
    if options.command == "build-context":
        context = devpod_paths.normalize_path(options.path)
        if not context.is_dir():
            devpod_errors.fail("build context must be an existing directory")
        if options.build_context_command == "audit":
            return management_build_context.show_build_context_audit(context)
        return management_build_context.protect_build_context(
            context, protect_all=options.all
        )
    workspace = management_devpod.management_workspace(options)
    own_entry_point = devpod_paths.wrapper_path()
    real_devpod = devpod_provider.discover_real_devpod(own_entry_point)
    devpod_provider.validate_devpod_version(real_devpod)
    if not options.context:
        workspace = devpod_context.resolve_selected_context(real_devpod, workspace)
    if not options.ssh_config:
        workspace = devpod_context.resolve_context_ssh_config(real_devpod, workspace)
    if getattr(options, "key_command", None) == "stop":
        devpod_agent.stop_agent(workspace)
        return 0
    if (
        options.devpod_command == "audit"
        or getattr(options, "key_command", None) == "show"
    ):
        return management_devpod.show_audit(workspace)
    return management_devpod.configure_workspace(real_devpod, workspace)
