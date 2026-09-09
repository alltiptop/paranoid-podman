"""Load only this checkout's package for source entry points."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def run(entry: str) -> int:
    from paranoid_podman.__main__ import main

    sys.argv.insert(1, entry)
    return main()
