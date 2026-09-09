"""Install local wheels using external pip; verify the runtime using only stdlib."""

import csv
import json
import os
import subprocess
from pathlib import Path

from paranoid_podman.lifecycle.artifacts import Delivery, file_digest, record_digest
from paranoid_podman.lifecycle.errors import fail

INSTALLER_VERSION = "26.2.1"


def run_python(python: Path, arguments: list[str], directory: Path) -> str:
    environment = {
        "PATH": os.defpath,
        "HOME": str(directory),
        "TMPDIR": str(directory),
        "LANG": "C.UTF-8",
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_NO_INDEX": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        result = subprocess.run(  # nosec B603
            [str(python), "-I", "-B", *arguments],
            cwd=directory,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        fail(f"runtime preparation failed: {error}")
    if result.returncode:
        fail(f"runtime preparation failed: {result.stderr[-4096:]}")
    return result.stdout


def install_runtime(
    release: Path, base_python: Path, delivery: Delivery, artifacts: Path
) -> None:
    installer = delivery.installer_python
    if (
        run_python(
            installer,
            ["-c", "from importlib.metadata import version; print(version('pip'))"],
            artifacts,
        ).strip()
        != INSTALLER_VERSION
    ):
        fail(f"external installer requires pip {INSTALLER_VERSION}")
    runtime = release / "runtime"
    run_python(
        base_python,
        ["-m", "venv", "--without-pip", "--symlinks", str(runtime)],
        artifacts,
    )
    for wheel in delivery.wheels:
        target = artifacts / wheel.path.name
        target.write_bytes(wheel.path.read_bytes())
        if file_digest(target) != wheel.sha256:
            fail("runtime wheel changed after verification")
    lock = artifacts / "runtime.lock"
    lock.write_text(delivery.lock_text(), encoding="utf-8")
    run_python(
        installer,
        [
            "-m",
            "pip",
            "--isolated",
            "--python",
            str(runtime / "bin/python"),
            "install",
            "--no-index",
            "--no-cache-dir",
            "--find-links",
            str(artifacts),
            "--require-hashes",
            "--only-binary=:all:",
            "--no-compile",
            "--requirement",
            str(lock),
        ],
        artifacts,
    )


def verify_runtime(release: Path, expected: dict[str, str], scratch: Path) -> None:
    runtime = release / "runtime"
    output = run_python(
        runtime / "bin/python",
        [
            "-c",
            """
import importlib.metadata as metadata
import json, site, sys, sysconfig
distributions = list(metadata.distributions())
print(json.dumps({
    'inventory': [(d.metadata['Name'].lower().replace('_', '-'), d.version) for d in distributions],
    'purelib': sysconfig.get_path('purelib'),
    'prefix': sys.prefix,
    'user_site': site.ENABLE_USER_SITE,
}))
""",
        ],
        scratch,
    )
    facts = json.loads(output)
    inventory = facts["inventory"]
    if len(inventory) != len(expected) or dict(inventory) != expected:
        fail("installed runtime contains unexpected distributions")
    if Path(facts["prefix"]) != runtime or facts["user_site"] is not False:
        fail("runtime interpreter is not isolated")
    site_packages = Path(facts["purelib"])
    if not site_packages.resolve().is_relative_to(runtime):
        fail("runtime site-packages escapes the release")
    records = sorted(site_packages.glob("*.dist-info/RECORD"))
    if len(records) != len(expected):
        fail("installed runtime RECORD inventory is incomplete")
    for record in records:
        seen: set[Path] = set()
        with record.open(encoding="utf-8", newline="") as source:
            for row in csv.reader(source):
                if len(row) != 3:
                    fail("invalid installed RECORD row")
                relative, checksum, size = row
                path = site_packages / relative
                resolved = path.resolve(strict=True)
                if (
                    Path(relative).is_absolute()
                    or "\\" in relative
                    or not resolved.is_relative_to(runtime)
                    or path.is_symlink()
                    or not path.is_file()
                    or resolved in seen
                ):
                    fail("unsafe or duplicate installed RECORD path")
                seen.add(resolved)
                if resolved == record and not checksum and not size:
                    continue
                content = path.read_bytes()
                if checksum != record_digest(content) or size != str(len(content)):
                    fail("installed RECORD content mismatch")
    run_python(
        runtime / "bin/python",
        [
            "-c",
            """
import yaml, dotenv, paranoid_podman
from paranoid_podman.common.layout import source_root
from paranoid_podman.podman import cli as podman
from paranoid_podman.compose import cli as compose
from paranoid_podman.devpod import cli as devpod
from paranoid_podman.lifecycle import cli as lifecycle
if source_root() is not None:
    raise RuntimeError('runtime imported source checkout')
""",
        ],
        scratch,
    )
    if any(runtime.rglob("*.pyc")):
        fail("installed runtime contains unexpected bytecode")
