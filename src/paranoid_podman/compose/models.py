"""Parsed Compose invocation with explicit project inputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Invocation:
    """A parsed command with explicit, policy-owned Compose inputs."""

    command: str
    command_args: list[str]
    compose_files: list[Path]
    env_file: Path | None
    profiles: list[str]
    project_dir: Path
    project_name: str
    execution_globals: list[str]
