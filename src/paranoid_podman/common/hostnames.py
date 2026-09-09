"""Readable container hostnames derived from DevPod workspace labels."""

import hashlib
import re

DEVPOD_ID_LABEL = "devpod.sh/id"


def workspace_hostname(value: object) -> str | None:
    """Keep ordinary names; suffix shortened or normalized names for uniqueness."""

    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        return value
    name = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-") or "workspace"
    suffix = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{name[:54].rstrip('-')}-{suffix}"
