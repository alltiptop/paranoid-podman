"""Discover and inspect external rootless Podman, Compose, and DevPod providers."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from paranoid_podman.common.environment import (
    ProviderEnvironmentProfile,
    provider_environment,
)
from paranoid_podman.lifecycle import paths as lifecycle_paths
from paranoid_podman.lifecycle import settings as lifecycle_settings
from paranoid_podman.lifecycle.errors import fail


def sanitized_provider_environment() -> dict[str, str]:
    return provider_environment(
        os.environ, profile=ProviderEnvironmentProfile.LIFECYCLE
    )


def provider_output(
    executable: Path, arguments: list[str], *, stdout_only: bool = False
) -> str:
    try:
        # The executable is an absolute path validated before this call.
        result = subprocess.run(  # nosec B603
            [str(executable), *arguments],
            cwd="/",
            env=sanitized_provider_environment(),
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        fail(f"failed to inspect provider: {executable}")
    if result.returncode != 0:
        fail(f"provider self-check failed: {executable}")
    if stdout_only:
        return result.stdout[:4096]
    return (result.stdout + "\n" + result.stderr)[:4096]


def parse_version(output: str, provider_name: str) -> tuple[int, int, int]:
    pattern = (
        lifecycle_settings.PODMAN_VERSION_PATTERN
        if provider_name == "Podman"
        else lifecycle_settings.COMPOSE_VERSION_PATTERN
    )
    matches = {
        tuple(int(part or 0) for part in match.groups())
        for match in pattern.finditer(output)
    }
    if len(matches) != 1:
        fail(f"could not parse {provider_name} version")
    major, minor, patch = matches.pop()
    return major, minor, patch


def parse_devpod_version(output: str) -> tuple[int, int, int]:
    match = lifecycle_settings.DEVPOD_VERSION_PATTERN.fullmatch(output)
    if match is None:
        fail("could not parse DevPod version")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def is_guard_launcher(path: Path) -> bool:
    """Recognize public launchers, including stale installations, without execution."""
    try:
        if not path.is_file():
            return False
        with path.open("rb") as source:
            content = source.read(8192)
        if not content.startswith(b"#!") or b"/current/launch" not in content:
            return False
        _, separator, command = content.decode("utf-8").rpartition("\nexec ")
        if not separator:
            return False
        words = shlex.split(command, comments=True)
    except (OSError, UnicodeError, ValueError):
        return False
    if len(words) != 3:
        return False
    launcher = Path(words[0])
    return (
        launcher.is_absolute()
        and launcher.name == "launch"
        and launcher.parent.name == "current"
        and words[1]
        in lifecycle_settings.ENTRY_POINTS + lifecycle_settings.DEVPOD_ENTRY_POINTS
        and words[2] == "$@"
    )


def is_provider_candidate(
    candidate: Path, install_root: Path, binary_directory: Path
) -> bool:
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return (
        resolved.is_file()
        and os.access(resolved, os.X_OK)
        and not lifecycle_paths.is_within(resolved, lifecycle_settings.PROJECT_ROOT)
        and not lifecycle_paths.is_within(resolved, install_root)
        and not lifecycle_paths.is_within(resolved, binary_directory)
        and not is_guard_launcher(resolved)
    )


def discover_provider(name: str, install_root: Path, binary_directory: Path) -> Path:
    for raw_directory in os.environ.get("PATH", "").split(os.pathsep):
        if not raw_directory:
            continue
        directory = Path(raw_directory).expanduser()
        if not directory.is_absolute():
            continue
        candidate = directory / name
        if is_provider_candidate(candidate, install_root, binary_directory):
            return candidate.resolve(strict=True)
    fail(f"could not find an external {name} executable in absolute PATH entries")


def resolve_provider(
    value: str | None, name: str, install_root: Path, binary_directory: Path
) -> Path:
    if value is None:
        return discover_provider(name, install_root, binary_directory)
    candidate = Path(value).expanduser()
    if candidate.is_absolute() and is_guard_launcher(candidate):
        fail(
            f"configured {name} is a paranoid-podman wrapper; select the real executable"
        )
    if not candidate.is_absolute() or not is_provider_candidate(
        candidate, install_root, binary_directory
    ):
        fail(
            f"configured {name} must be an external absolute executable outside "
            "the source, installation root, and command directory"
        )
    return candidate.resolve(strict=True)


def discover_devpod(install_root: Path, binary_directory: Path) -> str | None:
    """Find the optional CLI, including the common user-local DevPod install."""
    for name in ("devpod", "devpod-cli"):
        for raw_directory in os.environ.get("PATH", "").split(os.pathsep):
            directory = Path(raw_directory).expanduser()
            if not raw_directory or not directory.is_absolute():
                continue
            candidate = directory / name
            if is_provider_candidate(candidate, install_root, binary_directory):
                return str(candidate.resolve(strict=True))
            if candidate == binary_directory / "devpod":
                try:
                    resolved = candidate.resolve(strict=True)
                except (OSError, RuntimeError):
                    continue
                if (
                    resolved.is_file()
                    and os.access(resolved, os.X_OK)
                    and not is_guard_launcher(resolved)
                    and not lifecycle_paths.is_within(resolved, install_root)
                    and not lifecycle_paths.is_within(
                        resolved, lifecycle_settings.PROJECT_ROOT
                    )
                ):
                    return str(candidate)
    return None


def resolve_devpod_provider(
    value: str,
    install_root: Path,
    binary_directory: Path,
    allowed_managed_paths: tuple[Path, ...],
) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        fail("configured DevPod must be an absolute executable")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        fail("configured DevPod executable does not exist")
    if is_guard_launcher(resolved):
        fail(
            "configured DevPod is a paranoid-podman wrapper; select the real executable"
        )
    allowed = {path.resolve(strict=False) for path in allowed_managed_paths}
    if resolved in allowed:
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            fail("configured DevPod path is not executable")
        return resolved
    if not is_provider_candidate(candidate, install_root, binary_directory):
        fail(
            "configured DevPod must be an external absolute executable outside "
            "the source, installation root, and command directory"
        )
    return resolved


def validate_providers(
    podman_value: str | None,
    compose_value: str | None,
    install_root: Path,
    binary_directory: Path,
    devpod_value: str | None = None,
    allowed_devpod_paths: tuple[Path, ...] = (),
) -> dict[str, str]:
    if shutil.which("bash") is None:
        fail("Bash is required")

    python = Path(getattr(sys, "_base_executable", sys.executable))
    if not python.is_absolute() or not is_provider_candidate(
        python, install_root, binary_directory
    ):
        fail("Python must be a durable external executable outside the checkout")

    podman = resolve_provider(podman_value, "podman", install_root, binary_directory)
    compose = resolve_provider(
        compose_value, "podman-compose", install_root, binary_directory
    )
    if podman == compose:
        fail("Podman and Compose providers must be different executables")

    podman_version = parse_version(
        provider_output(podman, ["--version"], stdout_only=True), "Podman"
    )
    if podman_version[:2] != lifecycle_settings.SUPPORTED_PODMAN_SERIES:
        fail(
            "unsupported Podman series; reviewed series is "
            f"{lifecycle_settings.SUPPORTED_PODMAN_SERIES[0]}.{lifecycle_settings.SUPPORTED_PODMAN_SERIES[1]}.x"
        )

    compose_version = parse_version(
        provider_output(
            compose,
            ["--podman-path", str(podman), "version", "--short"],
            stdout_only=True,
        ),
        "podman-compose",
    )
    if compose_version[:2] != lifecycle_settings.SUPPORTED_COMPOSE_SERIES:
        fail(
            "unsupported podman-compose series; reviewed series is "
            f"{lifecycle_settings.SUPPORTED_COMPOSE_SERIES[0]}.{lifecycle_settings.SUPPORTED_COMPOSE_SERIES[1]}.x"
        )

    rootless = provider_output(
        podman,
        ["info", "--format", "{{.Host.Security.Rootless}}"],
        stdout_only=True,
    ).strip()
    if rootless.lower() != "true":
        fail("Podman rootless mode could not be verified")

    providers = {
        "python": str(python.resolve(strict=True)),
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "podman": str(podman),
        "podman_version": ".".join(map(str, podman_version)),
        "compose": str(compose),
        "compose_version": ".".join(map(str, compose_version)),
    }
    if devpod_value is not None:
        devpod = resolve_devpod_provider(
            devpod_value,
            install_root,
            binary_directory,
            allowed_devpod_paths,
        )
        devpod_version = parse_devpod_version(
            provider_output(devpod, ["version"], stdout_only=True)
        )
        providers["devpod"] = str(devpod)
        providers["devpod_version"] = ".".join(map(str, devpod_version))
    return providers
