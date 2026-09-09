"""Validate external model shapes and names before using them."""

from __future__ import annotations

import re
from typing import Any

from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.compose.errors import reject

NAME_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*\Z")


def _mapping(value: Any, risk: str, *, category: ViolationCategory) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        reject(f"blocked resolved configuration: {risk}", category=category)
    return value


def _string_list(value: Any, risk: str, *, category: ViolationCategory) -> list[str]:
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        reject(f"blocked resolved configuration: {risk}", category=category)
    return value


def _valid_name(name: str) -> bool:
    return len(name) <= 128 and bool(NAME_PATTERN.fullmatch(name))
