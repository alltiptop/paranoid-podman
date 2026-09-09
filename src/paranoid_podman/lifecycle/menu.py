"""Show installation status before selecting a lifecycle action."""

from __future__ import annotations

import sys
from pathlib import Path

from paranoid_podman.lifecycle import operations
from paranoid_podman.lifecycle.reporting import report


def choose_action(
    requested_binary_directory: Path | None,
    binary_directory: Path,
    install_root: Path,
    *,
    uninstall_only: bool = False,
) -> str | None:
    # Incomplete installations must pass the same checks as explicit status.
    installed = any(
        path.exists() or path.is_symlink()
        for path in (
            install_root / ".install.json",
            install_root / "current",
            install_root / "releases",
            install_root / "backups",
        )
    )
    if installed:
        operations.status(requested_binary_directory, install_root)
    else:
        report("not installed")
        report(f"installation root: {install_root}")
        report(f"binary directory: {binary_directory}")

    actions = {} if uninstall_only else {"1": "update" if installed else "install"}
    print("\nWhat would you like to do?")
    if not uninstall_only:
        print(
            f"  1. {actions['1']} - {'update the installation' if installed else 'install paranoid-podman'}"
        )
    if installed:
        number = str(len(actions) + 1)
        actions[number] = "uninstall"
        print(
            f"  {number}. uninstall - remove the installation and restore saved commands"
        )
    print("  0. Exit (default)")

    if not sys.stdin.isatty():
        report(
            "no interactive terminal; use ./install.sh uninstall to remove the installation"
            if uninstall_only and installed
            else "no interactive terminal; specify an action to make changes"
        )
        return None

    while True:
        try:
            choice = input("Selection [0]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            choice = "0"
        if choice in {"", "0", "q", "exit"}:
            report("cancelled; no changes made")
            return None
        if choice in actions:
            return actions[choice]
        if choice in actions.values():
            return choice
        report("choose one of the listed actions, or 0 to exit")
