"""Reviewed user-mode network options, independent of any project or port."""

import re


def loopback_tcp_port(value: object) -> int | None:
    """Accept one explicit loopback TCP forward through pasta."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"pasta:-T,([1-9][0-9]{0,4})", value)
    if match is None:
        return None
    port = int(match[1])
    return port if port <= 65535 else None
