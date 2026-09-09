"""Management notices on stderr, preserving protocol stdout."""

from __future__ import annotations

import os
import sys

from paranoid_podman.devpod import settings as devpod_settings


def info(message: str) -> None:
    print(f"{devpod_settings.PROJECT_NAME}: INFO: {message}", file=sys.stderr)


def debug(message: str) -> None:
    if os.environ.get("PODMAN_GUARD_DEBUG") == "1":
        info(message)


def warning(message: str) -> None:
    print(f"{devpod_settings.PROJECT_NAME}: WARNING: {message}", file=sys.stderr)


def danger(message: str) -> None:
    print(f"{devpod_settings.PROJECT_NAME}: DANGER: {message}", file=sys.stderr)
