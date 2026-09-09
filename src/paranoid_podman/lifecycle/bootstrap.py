"""Prepare a source checkout for installation without user packaging steps."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from paranoid_podman.common.layout import source_root
from paranoid_podman.lifecycle.artifacts import Delivery, inspect_wheel, load_delivery
from paranoid_podman.lifecycle.errors import fail
from paranoid_podman.lifecycle.reporting import report


def run(python: Path, arguments: list[str], scratch: Path) -> None:
    """Keep preparation tools and their configuration out of the user's Python."""
    environment = {
        "PATH": os.defpath,
        "HOME": str(scratch),
        "TMPDIR": str(scratch),
        "LANG": "C.UTF-8",
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "SOURCE_DATE_EPOCH": "315532800",
    }
    # Standard pip controls allow a prepared local wheel directory to be used
    # offline. Ignore ambient indexes, credentials and Python import settings.
    for name in ("PIP_NO_INDEX", "PIP_FIND_LINKS"):
        if value := os.environ.get(name):
            environment[name] = value
    try:
        result = subprocess.run(  # nosec B603
            [str(python), "-I", "-B", *arguments],
            cwd=scratch,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        fail(f"automatic preparation failed: {error}")
    if result.returncode:
        fail(
            "automatic preparation failed; check network access and Python venv "
            "support, then retry. No system Python packages were changed.\n"
            + result.stderr[-4096:]
        )


def prepare(root: Path, scratch: Path) -> Delivery:
    python = Path(sys.executable)
    builder = scratch / "build/bin/python"
    installer = scratch / "installer/bin/python"
    report("preparing installation tools in a temporary Python environment")
    for directory, interpreter, requirements in (
        ("build", builder, "requirements-build.txt"),
        ("installer", installer, "requirements-installer.txt"),
    ):
        run(python, ["-m", "venv", str(scratch / directory)], scratch)
        run(
            interpreter,
            [
                "-m",
                "pip",
                "install",
                "--no-cache-dir",
                "--only-binary=:all:",
                "--requirement",
                str(root / requirements),
            ],
            scratch,
        )
    wheels = scratch / "wheels"
    report("building the application from this checkout")
    run(
        builder,
        [str(root / "scripts/build_wheel.py"), "--outdir", str(wheels)],
        scratch,
    )
    project_paths = list(wheels.glob("*.whl"))
    if len(project_paths) != 1:
        fail("automatic build must produce exactly one application wheel")
    project = inspect_wheel(project_paths[0])
    report("preparing the application's Python dependencies")
    run(
        installer,
        [
            "-m",
            "pip",
            "download",
            "--no-cache-dir",
            "--only-binary=:all:",
            "--dest",
            str(wheels),
            "--requirement",
            str(root / "requirements.txt"),
        ],
        scratch,
    )
    run(python, [str(root / "scripts/wheelhouse_lock.py"), str(wheels)], scratch)
    return load_delivery(project.path, project.sha256, wheels, installer)


@contextmanager
def installation_delivery(
    delivery: Delivery | None, dry_run: bool
) -> Iterator[Delivery | None]:
    if delivery is not None:
        yield delivery
        return
    root = source_root()
    if root is None:
        fail(
            "run ./install.sh update from the source checkout, or supply wheel options"
        )
    if dry_run:
        report(
            "would prepare tools and dependencies, build this checkout and install it"
        )
        report("dry run complete; no downloads or files changed")
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="pp-install-") as temporary:
        yield prepare(root, Path(temporary))
