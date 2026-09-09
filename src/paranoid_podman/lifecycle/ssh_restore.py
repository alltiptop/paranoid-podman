"""Restore verified DevPod ProxyCommands during removal."""

from __future__ import annotations

import os
import shlex
import stat
from pathlib import Path

from paranoid_podman.devpod.errors import DevPodGuardError
from paranoid_podman.devpod.ssh_config import devpod_blocks, ssh_config_newline
from paranoid_podman.lifecycle import files as lifecycle_files
from paranoid_podman.lifecycle import paths as lifecycle_paths
from paranoid_podman.lifecycle import reporting as lifecycle_reporting
from paranoid_podman.lifecycle.errors import LifecycleError, fail


def restore_devpod_proxy_commands(
    ssh_config: Path,
    wrapper: Path,
    real_devpod: Path,
    dry_run: bool,
) -> None:
    """Restore wrapper-owned ProxyCommand paths inside DevPod marker blocks."""

    if not ssh_config.exists():
        return
    try:
        metadata = ssh_config.lstat()
        if (
            ssh_config.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_size > 1024 * 1024
        ):
            fail(f"cannot safely restore DevPod SSH config: {ssh_config}")
        text = ssh_config.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise LifecycleError(
            f"cannot safely restore DevPod SSH config: {ssh_config}"
        ) from error

    try:
        newline = ssh_config_newline(text)
        lines = text.splitlines()
        blocks = devpod_blocks(lines)
    except DevPodGuardError as error:
        raise LifecycleError(f"{error}: {ssh_config}") from error

    changed = 0
    removed_lines: set[int] = set()
    for start, end in blocks:
        proxy_indexes = [
            index
            for index in range(start + 1, end)
            if lines[index].strip().split(None, 1)
            and lines[index].strip().split(None, 1)[0].lower() == "proxycommand"
        ]
        wrapper_proxy: tuple[int, list[str]] | None = None
        for index in proxy_indexes:
            fields = lines[index].strip().split(None, 1)
            if len(fields) != 2:
                fail(f"invalid DevPod ProxyCommand: {ssh_config}")
            try:
                arguments = shlex.split(fields[1], posix=True)
            except ValueError as error:
                raise LifecycleError(
                    f"invalid DevPod ProxyCommand: {ssh_config}"
                ) from error
            if arguments and lifecycle_paths.normalize_path(arguments[0]) == wrapper:
                wrapper_proxy = (index, arguments)
        if wrapper_proxy is None:
            continue
        if len(proxy_indexes) != 1:
            fail(f"multiple ProxyCommands in managed DevPod block: {ssh_config}")
        proxy_index, arguments = wrapper_proxy
        if len(arguments) < 3 or arguments[1] != "ssh" or "--stdio" not in arguments:
            fail(f"unrecognized wrapper ProxyCommand: {ssh_config}")

        identity_indexes = [
            index
            for index in range(start + 1, end)
            if lines[index].strip().split(None, 1)
            and lines[index].strip().split(None, 1)[0].lower() == "identityagent"
        ]
        if len(identity_indexes) > 1:
            fail(f"multiple IdentityAgent values in managed DevPod block: {ssh_config}")
        if identity_indexes:
            identity_index = identity_indexes[0]
            fields = lines[identity_index].strip().split(None, 1)
            if len(fields) != 2:
                fail(f"invalid DevPod IdentityAgent: {ssh_config}")
            try:
                identity_values = shlex.split(fields[1], posix=True)
            except ValueError as error:
                raise LifecycleError(
                    f"invalid DevPod IdentityAgent: {ssh_config}"
                ) from error
            recognized = len(identity_values) == 1 and (
                identity_values[0].lower() == "none"
                or (
                    Path(identity_values[0]).is_absolute()
                    and "paranoid-podman/ssh-agent/" in identity_values[0]
                )
            )
            if not recognized:
                fail(
                    "DevPod IdentityAgent was independently changed; refusing "
                    f"to overwrite it during uninstall: {ssh_config}"
                )
            removed_lines.add(identity_index)

        arguments[0] = str(real_devpod)
        lines[proxy_index] = "  ProxyCommand " + shlex.join(arguments)
        changed += 1
    if not changed:
        return
    lifecycle_reporting.report(
        f"restore {changed} DevPod SSH ProxyCommand path(s) in {ssh_config}"
    )
    if dry_run:
        return
    replacement = newline.join(
        line for index, line in enumerate(lines) if index not in removed_lines
    )
    if text.endswith(newline):
        replacement += newline
    lifecycle_files.atomic_write(
        ssh_config,
        replacement.encode("utf-8"),
        stat.S_IMODE(metadata.st_mode),
    )
