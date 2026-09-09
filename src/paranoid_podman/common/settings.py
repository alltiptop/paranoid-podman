"""Shared policy settings; environment overrides are read once at import."""

import os

PROTECT_GIT = os.environ.get("PODMAN_GUARD_PROTECT_GIT", "1") != "0"
MAX_BUILD_CONTROL_FILE_BYTES = 1024 * 1024
