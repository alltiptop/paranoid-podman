"""Validate lifecycle arguments and dispatch installation operations."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from paranoid_podman.lifecycle import arguments as lifecycle_arguments
from paranoid_podman.lifecycle import menu as lifecycle_menu
from paranoid_podman.lifecycle import operations as lifecycle_operations
from paranoid_podman.lifecycle import paths as lifecycle_paths
from paranoid_podman.lifecycle import providers as lifecycle_providers
from paranoid_podman.lifecycle import settings as lifecycle_settings
from paranoid_podman.lifecycle.artifacts import load_delivery
from paranoid_podman.lifecycle.errors import LifecycleError, fail


def main(arguments: list[str] | None = None, *, uninstall_only: bool = False) -> int:
    options = lifecycle_arguments.parse_arguments(
        arguments if arguments is not None else sys.argv[1:],
        uninstall_only=uninstall_only,
    )
    default_binary_directory, default_install_root = lifecycle_paths.default_paths()
    requested_binary_directory = (
        lifecycle_paths.normalize_path(options.bindir) if options.bindir else None
    )
    binary_directory = requested_binary_directory or default_binary_directory
    install_root = (
        lifecycle_paths.normalize_path(options.libdir)
        if options.libdir
        else default_install_root
    )
    lifecycle_paths.validate_install_paths(binary_directory, install_root)

    if os.geteuid() == 0:
        fail("refusing lifecycle actions as root; use the owning rootless account")
    action = options.action
    if action is None:
        action = lifecycle_menu.choose_action(
            requested_binary_directory,
            binary_directory,
            install_root,
            uninstall_only=uninstall_only,
        )
        if action is None:
            return 0
    if action not in {"install", "update"} and (
        options.podman
        or options.compose_provider
        or options.devpod
        or options.without_devpod
        or options.backup_existing
    ):
        fail("provider and backup options apply only to install or update")
    if action == "update" and options.backup_existing:
        fail("--backup-existing applies only to the initial install")
    if action == "update" and options.without_devpod:
        fail("--without-devpod applies only to the initial install")
    if action != "uninstall" and options.devpod_ssh_config:
        fail("--devpod-ssh-config applies only to uninstall")

    delivery = None
    wheel_options = (
        options.wheel,
        options.sha256,
        options.wheelhouse,
        options.installer_python,
    )
    if action not in {"install", "update"} and any(wheel_options):
        fail("wheel delivery options apply only to install or update")
    if action in {"install", "update"} and any(wheel_options):
        if not all(wheel_options):
            fail(
                "explicit wheel installs require --wheel, --sha256, --wheelhouse and --installer-python; omit all four for automatic preparation"
            )
        if not options.installer_python.is_absolute():
            fail("--installer-python must be absolute")
        if options.installer_python.absolute().is_relative_to(install_root):
            fail("installer must be external to the managed installation")
        delivery = load_delivery(
            options.wheel.absolute(),
            options.sha256,
            options.wheelhouse.absolute(),
            options.installer_python,
        )

    if action == "install":
        if not options.devpod and not options.without_devpod:
            options.devpod = lifecycle_providers.discover_devpod(
                install_root, binary_directory
            )
        allowed_devpod_paths: tuple[Path, ...] = ()
        devpod_target = binary_directory / "devpod"
        if (
            options.devpod
            and lifecycle_paths.normalize_path(options.devpod) == devpod_target
        ):
            allowed_devpod_paths = (devpod_target,)
        providers = lifecycle_providers.validate_providers(
            options.podman,
            options.compose_provider,
            install_root,
            binary_directory,
            options.devpod,
            allowed_devpod_paths,
        )
        if providers.get("devpod") == str(devpod_target):
            providers["devpod"] = str(install_root / "backups/devpod")
        lifecycle_operations.install(
            binary_directory,
            install_root,
            providers,
            options.dry_run,
            options.backup_existing,
            delivery=delivery,
        )
    elif action == "update":
        lifecycle_operations.update(
            requested_binary_directory,
            install_root,
            options.podman,
            options.compose_provider,
            options.dry_run,
            options.devpod,
            delivery=delivery,
        )
    elif action == "status":
        lifecycle_operations.status(requested_binary_directory, install_root)
    else:
        lifecycle_operations.uninstall(
            requested_binary_directory,
            install_root,
            options.dry_run,
            tuple(
                lifecycle_paths.normalize_path(path)
                for path in options.devpod_ssh_config
            ),
        )
    return 0


def entrypoint(*, uninstall_only: bool = False) -> int:
    try:
        return main(uninstall_only=uninstall_only)
    except LifecycleError as error:
        print(f"{lifecycle_settings.PROJECT_NAME}: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(
            f"{lifecycle_settings.PROJECT_NAME}: lifecycle operation failed: {error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(entrypoint())
