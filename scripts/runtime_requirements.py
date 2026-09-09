"""Generate the transitional runtime requirements from canonical project metadata."""

import argparse
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def requirements_text(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as source:
        project = tomllib.load(source)["project"]
    return (
        "# Generated from pyproject.toml by scripts/runtime_requirements.py.\n"
        + "\n".join(project["dependencies"])
        + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    options = parser.parse_args()
    expected = requirements_text(PROJECT_ROOT)
    destination = PROJECT_ROOT / "requirements.txt"
    if options.check:
        if destination.read_text(encoding="utf-8") != expected:
            print(
                "requirements.txt is stale; run python3 -B scripts/runtime_requirements.py",
                file=sys.stderr,
            )
            return 1
    else:
        destination.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
