"""Port syntax checks shared by direct Podman and Compose; no address policy."""

from __future__ import annotations

import ipaddress
import re


def port_range(value: object) -> tuple[int, int] | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    match = re.fullmatch(r"([0-9]{1,5})(?:-([0-9]{1,5}))?", str(value))
    if not match:
        return None
    start = int(match.group(1))
    end = int(match.group(2) or match.group(1))
    return (start, end) if 1 <= start <= end <= 65535 else None


def is_ip_address(value: str) -> bool:
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def is_published_port(value: str) -> bool:
    match = re.fullmatch(
        r"(?:(?:(?P<ip>\[[0-9A-Fa-f:.]+\]|[0-9.]+):)?"
        r"(?P<published>[0-9-]*):)?"
        r"(?P<target>[0-9-]+)(?:/(?:tcp|udp))?",
        value,
    )
    if not match:
        return False
    address = match.group("ip")
    if address is not None and not is_ip_address(address):
        return False
    target = port_range(match.group("target"))
    if target is None:
        return False
    published = match.group("published")
    if not published:
        return True
    host_range = port_range(published)
    if host_range is None:
        return False
    return (
        host_range[0] == host_range[1]
        or target[0] == target[1]
        or host_range[1] - host_range[0] == target[1] - target[0]
    )
