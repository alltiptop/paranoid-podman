"""Render public command launchers and the private runtime dispatcher."""

from __future__ import annotations

import shlex
from pathlib import Path

from paranoid_podman.lifecycle import settings as lifecycle_settings


def desired_entry_points(providers: dict[str, str]) -> tuple[str, ...]:
    if "devpod" in providers:
        return lifecycle_settings.ENTRY_POINTS + lifecycle_settings.DEVPOD_ENTRY_POINTS
    return lifecycle_settings.ENTRY_POINTS


def release_launcher(providers: dict[str, str], installation_id: str) -> bytes:
    podman = shlex.quote(providers["podman"])
    compose = shlex.quote(providers["compose"])
    provenance = shlex.quote(installation_id)
    devpod = shlex.quote(providers["devpod"]) if "devpod" in providers else ""
    devpod_environment = (
        f"export PARANOID_PODMAN_REAL_DEVPOD={devpod}\n" if devpod else ""
    )
    return f"""#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "paranoid-podman: installed launcher requires an entry-point name" >&2
  exit 126
fi

ENTRY_POINT="$1"
shift
SCRIPT_DIR="$(cd -- "$(dirname -- "${{BASH_SOURCE[0]}}")" && pwd -P)"
export PYTHONDONTWRITEBYTECODE=1
export PODMAN_GUARD_REAL_PODMAN={podman}
export PODMAN_GUARD_COMPOSE_PROVIDER={compose}
export PODMAN_GUARD_INSTALLATION_ID={provenance}
{devpod_environment}

case "$ENTRY_POINT" in
  podman)
    exec "$SCRIPT_DIR/runtime/bin/python" -I -B -m paranoid_podman podman "$@"
    ;;
  docker)
    if [[ "${{1:-}}" == "compose" ]]; then
      shift
      exec "$SCRIPT_DIR/runtime/bin/python" -I -B -m paranoid_podman compose-guard "$@"
    fi
    exec "$SCRIPT_DIR/runtime/bin/python" -I -B -m paranoid_podman podman "$@"
    ;;
  compose-guard|podman-compose|docker-compose)
    exec "$SCRIPT_DIR/runtime/bin/python" -I -B -m paranoid_podman compose-guard "$@"
    ;;
  paranoid-podman)
    # Optional DevPod support is fixed at installation, not read from the caller.
    # shellcheck disable=SC2157
    if [[ "${{1:-}}" == "devpod" && -z {devpod or "''"} ]]; then
      echo "paranoid-podman: DevPod integration is not installed; uninstall and install again with --devpod before using DevPod management" >&2
      exit 125
    fi
    exec "$SCRIPT_DIR/runtime/bin/python" -I -B -m paranoid_podman paranoid-podman "$@"
    ;;
  devpod)
    if [[ -z "${{PARANOID_PODMAN_REAL_DEVPOD:-}}" ]]; then
      echo "paranoid-podman: DevPod integration is not installed" >&2
      exit 126
    fi
    exec "$SCRIPT_DIR/runtime/bin/python" -I -B -m paranoid_podman devpod "$@"
    ;;
  lifecycle)
    exec "$SCRIPT_DIR/runtime/bin/python" -I -B -m paranoid_podman lifecycle "$@"
    ;;
  *)
    echo "paranoid-podman: invalid installed entry point" >&2
    exit 126
    ;;
esac
""".encode()


def command_launcher(install_root: Path, entry_point: str) -> bytes:
    installed_launcher = shlex.quote(str(install_root / "current/launch"))
    entry = shlex.quote(entry_point)
    devpod_wrapper = ""
    if entry_point in {"devpod", "paranoid-podman"}:
        devpod_wrapper = """\
LAUNCHER_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
export PARANOID_PODMAN_DEVPOD_WRAPPER="$LAUNCHER_DIR/devpod"
"""
    return f"""#!/usr/bin/env bash
set -euo pipefail
{devpod_wrapper}
exec {installed_launcher} {entry} "$@"
""".encode()
