"""Checked options, mount decisions, and container exec arguments."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MountPolicy:
    target: str
    protected_mounts: tuple[tuple[str, str], ...] = ()
    protected_targets: tuple[str, ...] = ()
    source: Path | None = None
    read_only: bool = False


@dataclass(frozen=True)
class CheckedOption:
    value: str | None
    replacement: tuple[str, ...] | None = None
    mount: MountPolicy | None = None


@dataclass(frozen=True)
class ExecCommand:
    reference: str
    arguments: list[str]
