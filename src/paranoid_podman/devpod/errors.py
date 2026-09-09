"""DevPod and management failures and their CLI exit status."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import NoReturn

from paranoid_podman.devpod import settings as devpod_settings


class DevPodGuardError(RuntimeError):
    """A DevPod request that cannot be completed safely."""


def fail(message: str) -> NoReturn:
    raise DevPodGuardError(message)


def guarded_main(function: Callable[[], int]) -> int:
    try:
        return function()
    except DevPodGuardError as error:
        print(f"{devpod_settings.PROJECT_NAME}: {error}", file=sys.stderr)
        return 125
