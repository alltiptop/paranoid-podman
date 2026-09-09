"""Validate a local, hashed runtime wheel set without invoking an installer."""

import base64
import csv
import hashlib
import io
import re
import stat
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

from paranoid_podman.lifecycle.errors import fail

RUNTIME_NAMES = {"paranoid-podman", "pyyaml", "python-dotenv"}
LOCK_LINE = re.compile(
    r"(paranoid-podman|pyyaml|python-dotenv)==([0-9][A-Za-z0-9.!+]*) "
    r"--hash=sha256:([0-9a-f]{64})"
)
PROJECT_DEPENDENCIES = {"PyYAML<7,>=6.0", "python-dotenv<2,>=1.0"}


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def record_digest(content: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(
        hashlib.sha256(content).digest()
    ).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class Wheel:
    path: Path
    name: str
    version: str
    sha256: str


def inspect_wheel(path: Path) -> Wheel:
    if path.is_symlink() or not path.is_file() or path.suffix != ".whl":
        fail(f"wheel must be a regular local file: {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            metadata_paths = [n for n in names if n.endswith(".dist-info/METADATA")]
            if len(metadata_paths) != 1 or len(names) != len(set(names)):
                fail("ambiguous wheel metadata or members")
            for member in archive.infolist():
                relative = PurePosixPath(member.filename)
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or "\\" in member.filename
                    or stat.S_ISLNK(member.external_attr >> 16)
                    or "__pycache__" in relative.parts
                    or relative.suffix == ".pyc"
                ):
                    fail("unsafe wheel member")
            metadata_path = metadata_paths[0]
            metadata = BytesParser().parsebytes(archive.read(metadata_path))
            name = str(metadata.get("Name", "")).lower().replace("_", "-")
            version = str(metadata.get("Version", ""))
            if name not in RUNTIME_NAMES or not version:
                fail("wheelhouse contains a non-runtime distribution")
            if name == "paranoid-podman":
                dist_info = metadata_path.removesuffix("METADATA")
                if (
                    set(metadata.get_all("Requires-Dist", [])) != PROJECT_DEPENDENCIES
                    or metadata.get_all("Provides-Extra")
                    or dist_info + "entry_points.txt" in names
                    or any(
                        not n.startswith(("paranoid_podman/", dist_info)) for n in names
                    )
                ):
                    fail(
                        "project wheel violates runtime dependency or payload boundaries"
                    )
            else:
                # The reviewed closure has no transitive runtime requirements.
                # Do not let dependency metadata turn --no-index into URL fetching.
                allowed = (
                    {'click>=5.0;extra=="cli"'} if name == "python-dotenv" else set()
                )
                requirements = {
                    value.replace(" ", "")
                    for value in metadata.get_all("Requires-Dist", [])
                }
                if not requirements <= allowed:
                    fail("runtime dependency wheel declares an unreviewed requirement")
            record = metadata_path.removesuffix("METADATA") + "RECORD"
            rows = list(csv.reader(io.StringIO(archive.read(record).decode("utf-8"))))
            files = {n for n in names if not n.endswith("/")}
            if (
                any(len(row) != 3 for row in rows)
                or len(rows) != len(files)
                or {row[0] for row in rows} != files
            ):
                fail("wheel RECORD must describe every file exactly once")
            for filename, checksum, size in rows:
                if filename == record and not checksum and not size:
                    continue
                content = archive.read(filename)
                if checksum != record_digest(content) or size != str(len(content)):
                    fail("wheel RECORD content mismatch")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        fail(f"cannot inspect wheel {path}: {error}")
    return Wheel(path, name, version, file_digest(path))


@dataclass(frozen=True)
class Delivery:
    wheels: tuple[Wheel, ...]
    installer_python: Path

    @property
    def project(self) -> Wheel:
        return next(wheel for wheel in self.wheels if wheel.name == "paranoid-podman")

    @property
    def inventory(self) -> dict[str, str]:
        return {wheel.name: wheel.version for wheel in self.wheels}

    def lock_text(self) -> str:
        return "".join(
            f"{w.name}=={w.version} --hash=sha256:{w.sha256}\n" for w in self.wheels
        )


def load_delivery(
    project: Path, digest: str, wheelhouse: Path, installer_python: Path
) -> Delivery:
    """Only accept the generated lock grammar; never pass caller pip syntax through."""
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        fail("--sha256 must be the trusted project wheel SHA-256")
    if not installer_python.is_absolute() or not installer_python.is_file():
        fail("--installer-python must be an absolute external Python executable")
    lock = wheelhouse / "runtime.lock"
    if lock.is_symlink() or not lock.is_file():
        fail("wheelhouse requires a regular runtime.lock")
    expected: dict[str, tuple[str, str]] = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        match = LOCK_LINE.fullmatch(line)
        if match is None or match[1] in expected:
            fail("runtime.lock requires one pinned SHA-256 entry per runtime package")
        expected[match[1]] = (match[2], match[3])
    if set(expected) != RUNTIME_NAMES:
        fail("runtime.lock must contain only the project and runtime dependencies")
    selected = inspect_wheel(project)
    if selected.name != "paranoid-podman" or selected.sha256 != digest:
        fail("project wheel SHA-256 or distribution mismatch")
    wheels = [selected]
    for path in sorted(wheelhouse.glob("*.whl")):
        wheel = inspect_wheel(path)
        if wheel.name == "paranoid-podman" and wheel.sha256 == selected.sha256:
            continue
        wheels.append(wheel)
    if len(wheels) != len(RUNTIME_NAMES) or {w.name for w in wheels} != RUNTIME_NAMES:
        fail("wheelhouse requires exactly one wheel per runtime distribution")
    for wheel in wheels:
        if expected[wheel.name] != (wheel.version, wheel.sha256):
            fail(f"runtime wheel does not match its lock: {wheel.name}")
    return Delivery(tuple(sorted(wheels, key=lambda w: w.name)), installer_python)
