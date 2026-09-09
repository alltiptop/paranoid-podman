"""Terminal input primitives shared by DevPod and management."""

from __future__ import annotations

import sys
from typing import TextIO

from paranoid_podman.devpod import errors as devpod_errors


def read_interactive_line(input_stream: TextIO) -> str:
    line = input_stream.readline()
    if line == "":
        devpod_errors.fail("cancelled because interactive input was closed")
    return line.strip()


def interactive_stream() -> TextIO | None:
    if sys.stdin.isatty():
        return sys.stdin
    return None
