"""Failures reported by lifecycle operations."""

from typing import NoReturn


class LifecycleError(RuntimeError):
    """A lifecycle request that cannot be completed safely."""


def fail(message: str) -> NoReturn:
    raise LifecycleError(message)
