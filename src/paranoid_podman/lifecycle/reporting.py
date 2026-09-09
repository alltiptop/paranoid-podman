"""Lifecycle status and planned filesystem changes."""

from __future__ import annotations

from paranoid_podman.lifecycle import settings as lifecycle_settings


def report(message: str) -> None:
    print(f"{lifecycle_settings.PROJECT_NAME}: {message}")
