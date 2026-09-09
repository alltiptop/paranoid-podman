"""Podman configuration; environment settings are read once at import."""

import os
import re

from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.common.executables import is_external_executable
from paranoid_podman.common.layout import wrapper_directory
from paranoid_podman.common.provenance import GUARD_INSTALLATION_ID
from paranoid_podman.podman.errors import reject

REAL_PODMAN = os.environ.get("PODMAN_GUARD_REAL_PODMAN", "/usr/bin/podman")
DEBUG = os.environ.get("PODMAN_GUARD_DEBUG", "0") == "1"
FORCE_KEEP_ID = os.environ.get("PODMAN_GUARD_KEEP_ID", "auto")  # auto | 1 | 0
FORCE_CAP_DROP = os.environ.get("PODMAN_GUARD_CAP_DROP", "auto")  # auto | 1
NO_NEW_PRIVS_SETTING = os.environ.get("PODMAN_GUARD_NO_NEW_PRIVS", "1")


def validate_configuration() -> None:
    if FORCE_KEEP_ID not in {"auto", "0", "1"}:
        reject(
            "PODMAN_GUARD_KEEP_ID must be auto, 0, or 1",
            category=ViolationCategory.POLICY,
        )
    if FORCE_CAP_DROP not in {"auto", "1"}:
        reject(
            "PODMAN_GUARD_CAP_DROP cannot disable capability protection",
            category=ViolationCategory.PRIVILEGE,
        )
    if NO_NEW_PRIVS_SETTING != "1":
        reject(
            "PODMAN_GUARD_NO_NEW_PRIVS cannot disable no-new-privileges",
            category=ViolationCategory.POLICY,
        )
    if not re.fullmatch(r"[0-9a-f]{64}", GUARD_INSTALLATION_ID):
        reject(
            "PODMAN_GUARD_INSTALLATION_ID must contain 64 lowercase hex digits",
            category=ViolationCategory.INSTALLATION,
        )
    if not is_external_executable(REAL_PODMAN, wrapper_directory()):
        reject(
            "configured real Podman must be an external executable",
            category=ViolationCategory.INSTALLATION,
        )
