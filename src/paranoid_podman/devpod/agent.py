"""Dedicated SSH-agent lifecycle, identity checks, and runtime artifacts."""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import signal
import stat
import subprocess
from pathlib import Path

from paranoid_podman.devpod import errors as devpod_errors
from paranoid_podman.devpod import files as devpod_files
from paranoid_podman.devpod import keys as devpod_keys
from paranoid_podman.devpod import locks as devpod_locks
from paranoid_podman.devpod import models as devpod_models
from paranoid_podman.devpod import process as devpod_process
from paranoid_podman.devpod import reporting as devpod_reporting
from paranoid_podman.devpod import settings as devpod_settings


def runtime_root() -> Path:
    directory = devpod_locks.runtime_project_root() / "ssh-agent"
    directory.mkdir(mode=0o700, exist_ok=True)
    metadata = directory.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        devpod_errors.fail(
            "SSH-agent runtime path must be a user-owned non-symlink directory"
        )
    directory.chmod(0o700)
    return directory


def agent_paths(workspace: devpod_models.Workspace) -> devpod_models.AgentPaths:
    identifier = hashlib.sha256(workspace_identity(workspace).encode()).hexdigest()[:24]
    directory = runtime_root() / identifier
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = directory.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        devpod_errors.fail(
            "workspace agent path must be a user-owned non-symlink directory"
        )
    directory.chmod(0o700)
    return devpod_models.AgentPaths(
        directory, directory / "agent.sock", directory / "agent.pid"
    )


def workspace_identity(workspace: devpod_models.Workspace) -> str:
    return (
        f"{workspace.devpod_home or '<default>'}\0{workspace.context}\0{workspace.name}"
    )


def agent_environment(
    paths: devpod_models.AgentPaths, pid: int | None = None
) -> dict[str, str]:
    environment = os.environ.copy()
    environment["SSH_AUTH_SOCK"] = str(paths.socket)
    if pid is not None:
        environment["SSH_AGENT_PID"] = str(pid)
    else:
        environment.pop("SSH_AGENT_PID", None)
    return environment


def agent_fingerprints(paths: devpod_models.AgentPaths) -> list[str] | None:
    try:
        metadata = paths.socket.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise devpod_errors.DevPodGuardError(
            "cannot inspect the dedicated SSH-agent socket"
        ) from error
    if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
        devpod_errors.fail("dedicated SSH-agent socket is not a user-owned socket")
    result = subprocess.run(  # nosec B603
        [devpod_process.required_tool("ssh-add"), "-l", "-E", "sha256"],
        env=agent_environment(paths),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode not in {0, 1}:
        return None
    fingerprints: list[str] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1].startswith("SHA256:"):
            fingerprints.append(fields[1])
    return fingerprints


def read_agent_pid(paths: devpod_models.AgentPaths) -> int | None:
    try:
        metadata = paths.pid_file.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            devpod_errors.fail(
                "dedicated SSH-agent PID file is not a private user-owned file"
            )
        value = paths.pid_file.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise devpod_errors.DevPodGuardError(
            "cannot inspect the dedicated SSH-agent PID file"
        ) from error
    return int(value) if value.isdigit() and int(value) > 1 else None


def process_is_our_ssh_agent(pid: int, socket: Path) -> bool:
    process = Path("/proc") / str(pid)
    try:
        if process.stat().st_uid != os.getuid() or "ssh-agent" not in (
            process / "comm"
        ).read_text(encoding="utf-8"):
            return False
        arguments = (process / "cmdline").read_bytes().split(b"\x00")
        encoded_socket = os.fsencode(socket)
        return any(
            argument == b"-a"
            and index + 1 < len(arguments)
            and arguments[index + 1] == encoded_socket
            for index, argument in enumerate(arguments)
        )
    except OSError:
        return False


def remove_agent_artifact(path: Path, expected_type: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise devpod_errors.DevPodGuardError(
            "cannot inspect a dedicated-agent runtime file"
        ) from error
    expected = (
        stat.S_ISSOCK(metadata.st_mode)
        if expected_type == "socket"
        else stat.S_ISREG(metadata.st_mode)
    )
    if not expected or metadata.st_uid != os.getuid():
        devpod_errors.fail(
            f"refusing to remove an invalid dedicated-agent {expected_type}"
        )
    path.unlink()


def stop_agent_unlocked(paths: devpod_models.AgentPaths) -> None:
    pid = read_agent_pid(paths)
    if pid is not None and process_is_our_ssh_agent(pid, paths.socket):
        os.kill(pid, signal.SIGTERM)
    remove_agent_artifact(paths.socket, "socket")
    remove_agent_artifact(paths.pid_file, "PID file")
    try:
        paths.directory.rmdir()
    except OSError:
        pass


def stop_agent(workspace: devpod_models.Workspace) -> None:
    with devpod_locks.runtime_lock("agent", workspace_identity(workspace)):
        stop_agent_unlocked(agent_paths(workspace))
    devpod_reporting.info(f"stopped the dedicated SSH agent for {workspace.name}")


def ensure_agent_unlocked(
    workspace: devpod_models.Workspace, private_key: Path, *, allow_prompt: bool
) -> devpod_models.AgentPaths:
    expected = devpod_keys.key_fingerprint(private_key)
    paths = agent_paths(workspace)
    current = agent_fingerprints(paths)
    if current == [expected]:
        devpod_reporting.info(
            f"using one project SSH identity for {workspace.name}: {expected}"
        )
        return paths
    if current is not None:
        stop_agent_unlocked(paths)
        paths = agent_paths(workspace)
    elif read_agent_pid(paths) is not None:
        stop_agent_unlocked(paths)
        paths = agent_paths(workspace)
    else:
        remove_agent_artifact(paths.socket, "socket")

    result = devpod_process.run_checked(
        [
            devpod_process.required_tool("ssh-agent"),
            "-a",
            str(paths.socket),
            "-t",
            devpod_settings.AGENT_KEY_LIFETIME,
            "-s",
        ]
    )
    match = re.search(r"SSH_AGENT_PID=(\d+)", result.stdout)
    if match is None:
        devpod_errors.fail("ssh-agent did not report its process id")
    pid = int(match.group(1))
    devpod_files.atomic_write_text(paths.pid_file, f"{pid}\n", 0o600)

    add_environment = agent_environment(paths, pid)
    add_result: subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]
    try:
        if allow_prompt:
            add_result = subprocess.run(  # nosec B603
                [devpod_process.required_tool("ssh-add"), str(private_key)],
                env=add_environment,
                check=False,
            )
        else:
            add_environment.pop("DISPLAY", None)
            add_environment.pop("WAYLAND_DISPLAY", None)
            add_environment.pop("SSH_ASKPASS", None)
            add_environment["SSH_ASKPASS_REQUIRE"] = "never"
            add_result = subprocess.run(  # nosec B603
                [devpod_process.required_tool("ssh-add"), str(private_key)],
                env=add_environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
    except (OSError, subprocess.TimeoutExpired):
        stop_agent_unlocked(paths)
        devpod_errors.fail("failed to run ssh-add for the dedicated SSH agent")
    if add_result.returncode != 0:
        stop_agent_unlocked(paths)
        if not allow_prompt:
            devpod_errors.fail(
                "the project key could not be loaded without a terminal; run "
                f"`paranoid-podman devpod configure {shlex.quote(workspace.name)}` "
                "interactively, then reconnect"
            )
        devpod_errors.fail(
            "the selected project key was not loaded into the dedicated SSH agent"
        )
    if agent_fingerprints(paths) != [expected]:
        stop_agent_unlocked(paths)
        devpod_errors.fail("dedicated SSH agent identity verification failed")
    devpod_reporting.debug(
        f"using one project SSH identity for {workspace.name}: {expected}"
    )
    return paths


def ensure_agent(
    workspace: devpod_models.Workspace, private_key: Path, *, allow_prompt: bool = True
) -> devpod_models.AgentPaths:
    with devpod_locks.runtime_lock("agent", workspace_identity(workspace)):
        return ensure_agent_unlocked(workspace, private_key, allow_prompt=allow_prompt)
