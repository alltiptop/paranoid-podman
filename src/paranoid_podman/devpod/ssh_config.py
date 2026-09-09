"""Read and update DevPod-owned SSH blocks without starting agents."""

from __future__ import annotations

import os
import shlex
import stat
from pathlib import Path

from paranoid_podman.devpod import arguments as devpod_arguments
from paranoid_podman.devpod import errors as devpod_errors
from paranoid_podman.devpod import files as devpod_files
from paranoid_podman.devpod import locks as devpod_locks
from paranoid_podman.devpod import models as devpod_models

DEVPOD_START_PREFIX = "# DevPod Start "
DEVPOD_END_PREFIX = "# DevPod End "
MAX_SSH_CONFIG_BYTES = 1024 * 1024


def read_ssh_config(path: Path) -> tuple[str, int]:
    if not path.exists():
        return "", 0o600
    try:
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            devpod_errors.fail(f"SSH config must be a regular non-symlink file: {path}")
        if metadata.st_uid != os.getuid():
            devpod_errors.fail(f"SSH config is not owned by the current user: {path}")
        if metadata.st_size > MAX_SSH_CONFIG_BYTES:
            devpod_errors.fail("SSH config is larger than 1 MiB")
        text = path.read_bytes().decode("utf-8")
        return text, stat.S_IMODE(metadata.st_mode)
    except (OSError, UnicodeError) as error:
        raise devpod_errors.DevPodGuardError(
            f"cannot read SSH config: {path}"
        ) from error


def devpod_block_bounds(text: str, host: str) -> tuple[list[str], int, int] | None:
    lines = text.splitlines()
    start_marker = DEVPOD_START_PREFIX + host
    end_marker = DEVPOD_END_PREFIX + host
    starts = [index for index, line in enumerate(lines) if line == start_marker]
    ends = [index for index, line in enumerate(lines) if line == end_marker]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        devpod_errors.fail(f"malformed DevPod-managed SSH block for {host}")
    nested_markers = [
        line
        for line in lines[starts[0] + 1 : ends[0]]
        if line.startswith((DEVPOD_START_PREFIX, DEVPOD_END_PREFIX))
    ]
    if nested_markers:
        devpod_errors.fail(f"nested DevPod-managed SSH block for {host}")
    return lines, starts[0], ends[0]


def devpod_blocks(lines: list[str]) -> list[tuple[int, int]]:
    """Locate all managed blocks, rejecting unpaired or nested markers."""
    block_start: int | None = None
    blocks: list[tuple[int, int]] = []
    start_name = ""
    for index, line in enumerate(lines):
        if line.startswith(DEVPOD_START_PREFIX):
            if block_start is not None:
                devpod_errors.fail("malformed DevPod SSH markers")
            block_start = index
            start_name = line.removeprefix(DEVPOD_START_PREFIX)
        elif line.startswith(DEVPOD_END_PREFIX):
            if (
                block_start is None
                or line.removeprefix(DEVPOD_END_PREFIX) != start_name
            ):
                devpod_errors.fail("malformed DevPod SSH markers")
            blocks.append((block_start, index))
            block_start = None
            start_name = ""
    if block_start is not None:
        devpod_errors.fail("malformed DevPod SSH markers")
    return blocks


def ssh_config_newline(text: str) -> str:
    without_crlf = text.replace("\r\n", "")
    if "\r" in without_crlf or ("\r\n" in text and "\n" in without_crlf):
        devpod_errors.fail("mixed or unsupported SSH config line endings")
    return "\r\n" if "\r\n" in text else "\n"


def ssh_config_quote(value: str) -> str:
    if any(character in value for character in ("\x00", "\n", "\r")):
        devpod_errors.fail("SSH config value contains an invalid control character")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def block_option(lines: list[str], start: int, end: int, name: str) -> str | None:
    matches: list[str] = []
    for line in lines[start + 1 : end]:
        fields = line.strip().split(None, 1)
        if fields and fields[0].lower() == name.lower():
            matches.append(fields[1] if len(fields) == 2 else "")
    if len(matches) > 1:
        devpod_errors.fail(f"duplicate {name} in DevPod-managed SSH block")
    return matches[0] if matches else None


def render_proxy_command(
    value: str, wrapper: Path, workspace: devpod_models.Workspace
) -> str:
    try:
        arguments = shlex.split(value, posix=True)
    except ValueError as error:
        raise devpod_errors.DevPodGuardError(
            "cannot parse DevPod ProxyCommand"
        ) from error
    if (
        len(arguments) < 7
        or arguments[1] != "ssh"
        or "--stdio" not in arguments
        or devpod_arguments.option_value(arguments, "--context") != workspace.context
        or workspace.name not in arguments
    ):
        devpod_errors.fail("refusing to rewrite an unrecognized DevPod ProxyCommand")
    arguments[0] = str(wrapper)
    return "  ProxyCommand " + shlex.join(arguments)


def patch_ssh_block(
    workspace: devpod_models.Workspace,
    mode: devpod_models.SSHMode,
    wrapper: Path,
    agent_socket: Path | None = None,
) -> bool:
    with devpod_locks.runtime_lock("ssh-config", str(workspace.ssh_config)):
        return patch_ssh_block_unlocked(workspace, mode, wrapper, agent_socket)


def patch_ssh_block_unlocked(
    workspace: devpod_models.Workspace,
    mode: devpod_models.SSHMode,
    wrapper: Path,
    agent_socket: Path | None = None,
) -> bool:
    text, file_mode = read_ssh_config(workspace.ssh_config)
    newline = ssh_config_newline(text)
    bounds = devpod_block_bounds(text, workspace.host)
    if bounds is None:
        return False
    lines, start, end = bounds
    rewritten: list[str] = []
    saw_forwarding = False
    saw_proxy = False
    for line in lines[start + 1 : end]:
        fields = line.strip().split(None, 1)
        name = fields[0].lower() if fields else ""
        if name == "forwardagent":
            if saw_forwarding:
                devpod_errors.fail("duplicate ForwardAgent in DevPod-managed SSH block")
            value = "yes" if mode != devpod_models.SSHMode.IDE_ONLY else "no"
            rewritten.append(f"  ForwardAgent {value}")
            saw_forwarding = True
        elif name == "identityagent":
            continue
        elif name == "proxycommand":
            if saw_proxy or len(fields) != 2:
                devpod_errors.fail("invalid ProxyCommand in DevPod-managed SSH block")
            rewritten.append(render_proxy_command(fields[1], wrapper, workspace))
            saw_proxy = True
        else:
            rewritten.append(line)
    if not saw_forwarding or not saw_proxy:
        devpod_errors.fail("DevPod-managed SSH block is missing required options")
    identity_value = (
        str(agent_socket)
        if mode == devpod_models.SSHMode.PROJECT_KEY and agent_socket is not None
        else "none"
        if mode == devpod_models.SSHMode.IDE_ONLY
        else None
    )
    if identity_value is not None:
        forwarding_index = next(
            index
            for index, line in enumerate(rewritten)
            if line.strip().lower().startswith("forwardagent ")
        )
        rewritten.insert(
            forwarding_index + 1,
            f"  IdentityAgent {ssh_config_quote(identity_value)}",
        )
    new_lines = lines[: start + 1] + rewritten + lines[end:]
    new_text = newline.join(new_lines)
    if text.endswith(newline):
        new_text += newline
    if new_text == text:
        return True
    devpod_files.atomic_write_text(workspace.ssh_config, new_text, file_mode or 0o600)
    return True
