"""Workspace, agent paths, and supported SSH credential modes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class SSHMode(str, Enum):
    PROJECT_KEY = "project-key"
    IDE_ONLY = "ide-only"
    UNSAFE_AMBIENT = "unsafe-ambient"


@dataclass(frozen=True)
class Workspace:
    context: str
    name: str
    ssh_config: Path
    devpod_home: str | None = None

    @property
    def host(self) -> str:
        return f"{self.name}.devpod"


@dataclass(frozen=True)
class AgentPaths:
    directory: Path
    socket: Path
    pid_file: Path
