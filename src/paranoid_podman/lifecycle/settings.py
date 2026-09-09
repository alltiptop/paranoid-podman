"""Installation format, public entry points, and reviewed provider versions."""

from __future__ import annotations

import re
from pathlib import Path

from paranoid_podman.common.layout import PACKAGE_ROOT, source_root

PROJECT_NAME = "paranoid-podman"
PROJECT_ROOT = source_root() or PACKAGE_ROOT
ENTRY_POINTS = (
    "podman",
    "docker",
    "compose-guard",
    "podman-compose",
    "docker-compose",
    "paranoid-podman",
)
DEVPOD_ENTRY_POINTS = ("devpod",)
SUPPORTED_PODMAN_SERIES = (6, 1)
SUPPORTED_COMPOSE_SERIES = (1, 6)
PODMAN_VERSION_PATTERN = re.compile(
    r"^\s*podman(?:\s+version)?\s+v?(\d+)\.(\d+)(?:\.(\d+))?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
COMPOSE_VERSION_PATTERN = re.compile(
    r"^\s*(?:podman-compose(?:\s+version)?\s+)?v?"
    r"(\d+)\.(\d+)(?:\.(\d+))?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
DEVPOD_VERSION_PATTERN = re.compile(r"^\s*v?(\d+)\.(\d+)\.(\d+)\s*$", re.IGNORECASE)
MANIFEST_FORMAT = 2
INSTALLATION_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SYSTEM_PREFIXES = tuple(
    Path(path).resolve(strict=False)
    for path in ("/bin", "/sbin", "/usr", "/etc", "/opt", "/var", "/lib", "/lib64")
)
