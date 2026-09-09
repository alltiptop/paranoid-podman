"""Compose limits, command groups, and import-time provider configuration."""

from __future__ import annotations

import os

REAL_PODMAN = os.environ.get("PODMAN_GUARD_REAL_PODMAN", "/usr/bin/podman")
COMPOSE_PROVIDER = os.environ.get(
    "PODMAN_GUARD_COMPOSE_PROVIDER", "/usr/bin/podman-compose"
)
MAX_CONFIG_BYTES = 8 * 1024 * 1024
DEFAULT_PIDS = 512
MAX_PIDS = 32_768
MAX_SCALE = 10
MAX_PARALLEL = 64
MAX_SOURCE_LOCATIONS = 24
MAX_DOTENV_BYTES = 1024 * 1024
DEFAULT_COMPOSE_FILES = (
    "compose.yaml",
    "compose.yml",
    "compose.override.yaml",
    "compose.override.yml",
    "podman-compose.yaml",
    "podman-compose.yml",
    "docker-compose.yml",
    "docker-compose.yaml",
    "docker-compose.override.yml",
    "docker-compose.override.yaml",
    "container-compose.yml",
    "container-compose.yaml",
    "container-compose.override.yml",
    "container-compose.override.yaml",
)
COMPOSE_CONTROL_ENV = {
    "COMPOSE_FILE",
    "COMPOSE_PATH_SEPARATOR",
    "COMPOSE_PROFILES",
    "COMPOSE_PROJECT_DIR",
    "COMPOSE_PROJECT_NAME",
}
SAFE_DIRECT_COMMANDS = {"help", "version"}
READ_ONLY_PROJECT_COMMANDS = {"config", "images", "logs", "port", "ps", "wait"}
DIRECT_PROJECT_COMMANDS = {"ls"}
MUTATING_COMMANDS = {
    "build",
    "down",
    "exec",
    "pause",
    "pull",
    "restart",
    "run",
    "start",
    "stop",
    "unpause",
    "up",
}
KNOWN_UNSUPPORTED_COMMANDS = {
    "kill",
    "push",
    "systemd",
}
PROVENANCE_REQUIRED_COMMANDS = {"exec", "restart", "run", "start", "unpause", "up"}
