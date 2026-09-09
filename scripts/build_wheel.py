"""Build a wheel and SHA-256 in a prepared build environment, without checks."""

import argparse
import hashlib
import os
import shutil
import subprocess  # nosec B404
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def copy_build_source(root: Path, destination: Path) -> None:
    """Stage only declared package sources and packaging inputs; reject symlinks."""
    source = root / "src" / "paranoid_podman"
    if source.is_symlink() or source.parent.is_symlink():
        raise ValueError("package source directories cannot be symlinks")
    paths = [
        root / name for name in ("pyproject.toml", "VERSION", "README.md", "LICENSE")
    ]
    for path in sorted(source.rglob("*")):
        if any(
            part.startswith(".") or part == "__pycache__"
            for part in path.relative_to(source).parts
        ):
            continue
        if path.is_symlink():
            raise ValueError("package sources cannot contain symlinks")
        if path.suffix == ".py" or path.name == "py.typed":
            paths.append(path)
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"invalid build input: {path}")
        target = destination / path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", type=Path, default=PROJECT_ROOT / "dist")
    destination = parser.parse_args().outdir.resolve()
    with tempfile.TemporaryDirectory(prefix="pp-build-") as temporary:
        root = Path(temporary)
        source, output = root / "source", root / "wheels"
        copy_build_source(PROJECT_ROOT, source)
        environment = {
            **os.environ,
            "PIP_NO_INDEX": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "SOURCE_DATE_EPOCH": os.environ.get("SOURCE_DATE_EPOCH", "315532800"),
        }
        subprocess.run(  # nosec B603
            [
                sys.executable,
                "-B",
                "-m",
                "build",
                "--wheel",
                "--no-isolation",
                "--outdir",
                str(output),
                str(source),
            ],
            env=environment,
            check=True,
            timeout=120,
        )
        wheels = list(output.glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("build must produce exactly one project wheel")
        wheel = wheels[0]
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(wheel, destination / wheel.name)
        checksum = destination / f"{wheel.name}.sha256"
        checksum.write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
        print(destination / wheel.name)
        print(checksum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
