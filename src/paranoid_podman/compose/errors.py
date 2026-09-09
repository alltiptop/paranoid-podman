"""Compose violations; the CLI owns diagnostics and exit status."""

from __future__ import annotations

from typing import NoReturn

from paranoid_podman.common.errors import GuardViolation, ViolationCategory


class PolicyViolation(GuardViolation):
    """A Compose invocation or resolved model that cannot be forwarded safely."""

    service_name: str | None = None


def reject(reason: str, *, category: ViolationCategory) -> NoReturn:
    raise PolicyViolation(reason, category=category)
