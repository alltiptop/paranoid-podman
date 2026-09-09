"""Parse the user-level installation command line."""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_arguments(
    arguments: list[str], *, uninstall_only: bool = False
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="uninstall.sh" if uninstall_only else "install.sh",
        description=(
            "Show installation status and an uninstall / Exit menu.\n"
            "Press Enter to exit without changes. Without an interactive\n"
            "terminal, only display status and available actions."
            if uninstall_only
            else "Manage a user-level paranoid-podman installation.\n"
            "Without an action, show status and an interactive menu;\n"
            "without a terminal, only display status and available actions."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./uninstall.sh\n"
            "  ./uninstall.sh --dry-run\n"
            "\n"
            "For removal without a menu (including scripts):\n"
            "  ./install.sh uninstall"
            if uninstall_only
            else "Actions:\n"
            "  install    Install this checkout and prepare Python automatically.\n"
            "  update     Install this checkout, keeping installation settings.\n"
            "  status     Check the installed version, paths, and file integrity.\n"
            "  uninstall  Remove managed files and restore saved commands and\n"
            "             managed SSH settings.\n"
            "\n"
            "Examples:\n"
            "  ./install.sh\n"
            "  ./install.sh install\n"
            "  ./install.sh update --dry-run\n"
            "  ./install.sh uninstall"
        ),
    )
    if uninstall_only:
        parser.set_defaults(
            action=None,
            podman=None,
            compose_provider=None,
            devpod=None,
            without_devpod=False,
            backup_existing=False,
            wheel=None,
            sha256=None,
            wheelhouse=None,
            installer_python=None,
        )
    else:
        parser.add_argument(
            "action",
            nargs="?",
            choices=("install", "update", "status", "uninstall"),
            help="operation to perform (omit for status and menu)",
        )
    general = parser.add_argument_group("General options")
    general.add_argument(
        "--bindir",
        metavar="DIR",
        help=(
            "command directory (default: recorded installation directory)"
            if uninstall_only
            else "command directory (install: XDG_BIN_HOME or ~/.local/bin; other actions: recorded directory)"
        ),
    )
    general.add_argument(
        "--libdir",
        metavar="DIR",
        help="installation root (default: ${XDG_DATA_HOME:-~/.local/share}/paranoid-podman)",
    )
    general.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "preview removal after menu selection without changing files"
            if uninstall_only
            else "preview install, update, or uninstall without downloads or file changes"
        ),
    )
    if not uninstall_only:
        add_setup_options(parser)
    removal = parser.add_argument_group("Uninstall options")
    removal.add_argument(
        "--devpod-ssh-config",
        metavar="FILE",
        action="append",
        default=[],
        help=(
            "additional SSH config whose managed settings should be restored; repeat for "
            "contexts under non-default DEVPOD_HOME directories"
        ),
    )
    return parser.parse_args(arguments)


def add_setup_options(parser: argparse.ArgumentParser) -> None:
    """Options exclusive to installation and updates."""
    setup = parser.add_argument_group(
        "Install / update options",
        "All optional. Providers are discovered on install and reused on update.\n"
        "Python dependencies are prepared automatically.",
    )
    setup.add_argument(
        "--podman", metavar="PATH", help="absolute real Podman executable"
    )
    setup.add_argument(
        "--compose-provider",
        metavar="PATH",
        help="absolute real podman-compose executable",
    )
    devpod = setup.add_mutually_exclusive_group()
    devpod.add_argument(
        "--devpod",
        metavar="PATH",
        help="absolute real DevPod executable (update: requires an existing DevPod guard)",
    )
    devpod.add_argument(
        "--without-devpod",
        action="store_true",
        help="install only: skip DevPod discovery and install Podman and Compose guards",
    )
    setup.add_argument(
        "--backup-existing",
        action="store_true",
        help="install only: allow backing up existing commands without an interactive confirmation",
    )
    delivery = parser.add_argument_group(
        "Advanced install / update options: local wheels",
        "Supply all four together to use prepared wheels,\n"
        "or omit all four for automatic setup.",
    )
    delivery.add_argument(
        "--wheel",
        type=Path,
        metavar="FILE",
        help="prebuilt local paranoid-podman wheel",
    )
    delivery.add_argument(
        "--sha256", metavar="HASH", help="trusted SHA-256 of the --wheel file"
    )
    delivery.add_argument(
        "--wheelhouse",
        type=Path,
        metavar="DIR",
        help="directory containing runtime dependency wheels and runtime.lock",
    )
    delivery.add_argument(
        "--installer-python",
        type=Path,
        metavar="PATH",
        help="absolute Python interpreter with pip, outside the managed installation",
    )
