"""Guard labels; the source or installed identity is selected once at import."""

import hashlib
import os

from paranoid_podman.common.layout import wrapper_directory

GUARD_LABEL_PREFIX = "io.github.paranoid-podman."
GUARD_POLICY_LABEL = f"{GUARD_LABEL_PREFIX}policy"
GUARD_POLICY_VERSION = "1"
GUARD_INSTALLATION_LABEL = f"{GUARD_LABEL_PREFIX}installation"
GUARD_INSTALLATION_ID = (
    os.environ.get("PODMAN_GUARD_INSTALLATION_ID")
    or hashlib.sha256(
        f"source:{wrapper_directory()}:{os.getuid()}".encode()
    ).hexdigest()
)


def is_guard_reserved_label(key: str) -> bool:
    """Return whether a user-supplied label would impersonate this guard."""

    return key.startswith(GUARD_LABEL_PREFIX)


def provenance_inspect_format() -> str:
    """Return a value-only Podman template for both provenance labels."""

    policy = f'{{{{ index .Config.Labels "{GUARD_POLICY_LABEL}" }}}}'
    installation = f'{{{{ index .Config.Labels "{GUARD_INSTALLATION_LABEL}" }}}}'
    return f"{policy}\t{installation}"


def expected_provenance_output() -> str:
    return f"{GUARD_POLICY_VERSION}\t{GUARD_INSTALLATION_ID}"
