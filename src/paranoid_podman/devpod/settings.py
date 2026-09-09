"""Project constants and DevPod provider settings."""

from __future__ import annotations

import re

PROJECT_NAME = "paranoid-podman"
DEVPOD_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
AGENT_KEY_LIFETIME = "8h"
LOCK_TIMEOUT_SECONDS = 20
