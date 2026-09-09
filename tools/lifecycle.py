#!/usr/bin/env python3
"""Source compatibility entry for installation lifecycle commands."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from paranoid_podman.lifecycle.cli import entrypoint  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(entrypoint())
