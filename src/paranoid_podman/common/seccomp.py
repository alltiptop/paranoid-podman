"""Explicit, package-owned seccomp profiles; never load project-supplied JSON."""

import hashlib
import re

from paranoid_podman.common.layout import PACKAGE_ROOT

CHROMIUM_SECURITY_OPTION = "seccomp=chromium"
CHROMIUM_PROFILE = PACKAGE_ROOT / "profiles" / "chromium.json"
CHROMIUM_PROFILE_SHA256 = (
    "b974a98ecb25b56323f00c67437aa87b5e64beb3698f706062a03877218c1f94"
)


def chromium_security_option() -> str:
    """Resolve the reviewed profile in the trusted installation, not the cwd."""
    try:
        if CHROMIUM_PROFILE.is_symlink():
            raise ValueError("bundled Chromium seccomp profile cannot be a symlink")
        digest = hashlib.sha256(CHROMIUM_PROFILE.read_bytes()).hexdigest()
    except OSError as error:
        raise ValueError("bundled Chromium seccomp profile is unavailable") from error
    if digest != CHROMIUM_PROFILE_SHA256:
        raise ValueError("bundled Chromium seccomp profile failed integrity check")
    return f"seccomp={CHROMIUM_PROFILE}"


def is_explicit_nonroot_user(value: object) -> bool:
    """Require a non-root runtime identity for the Chromium exception."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return False
    parts = str(value).split(":")
    return len(parts) <= 2 and all(
        re.fullmatch(r"(?:[0-9]+|[A-Za-z_][A-Za-z0-9_.-]*)", part)
        and part != "root"
        and not (part.isdigit() and int(part) == 0)
        for part in parts
    )
