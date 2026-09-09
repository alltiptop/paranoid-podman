"""Podman policy exceptions; the CLI owns diagnostics and exit status."""

from typing import NoReturn

from paranoid_podman.common.errors import GuardViolation, ViolationCategory


class PolicyViolation(GuardViolation):
    """An argument sequence that the guard cannot safely forward."""


def reject(reason: str, *, category: ViolationCategory) -> NoReturn:
    raise PolicyViolation(reason, category=category)
