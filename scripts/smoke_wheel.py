"""Inspect a built wheel and smoke-test an already prepared isolated runtime."""

import argparse
import base64
import csv
import hashlib
import io
import json
import subprocess  # nosec B404
import sys
import tempfile
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath


def wheel_metadata(wheel: Path, digest: str) -> str:
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != digest:
        raise ValueError("project wheel SHA-256 mismatch")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_names = [
            name for name in names if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1 or len(set(names)) != len(names):
            raise ValueError("ambiguous wheel members/metadata")
        metadata_name = metadata_names[0]
        dist_info = metadata_name.removesuffix("METADATA")
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError("unsafe wheel member path")
            if not name.startswith(("paranoid_podman/", dist_info)):
                raise ValueError("unexpected wheel payload")
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                raise ValueError("bytecode must not be packaged")
        metadata = BytesParser().parsebytes(archive.read(metadata_name))
        dependencies = set(metadata.get_all("Requires-Dist", []))
        if dependencies != {"PyYAML<7,>=6.0", "python-dotenv<2,>=1.0"}:
            raise ValueError(f"unexpected runtime dependencies: {sorted(dependencies)}")
        if (
            metadata.get_all("Provides-Extra")
            or dist_info + "entry_points.txt" in names
        ):
            raise ValueError(
                "project wheel must not expose tooling extras or public scripts"
            )
        record_name = dist_info + "RECORD"
        records = list(csv.reader(io.StringIO(archive.read(record_name).decode())))
        if {row[0] for row in records} != set(names) or len(records) != len(names):
            raise ValueError("wheel RECORD does not describe the complete archive")
        for name, checksum, size in records:
            if name == record_name and not checksum and not size:
                continue
            content = archive.read(name)
            expected = (
                base64.urlsafe_b64encode(hashlib.sha256(content).digest())
                .rstrip(b"=")
                .decode()
            )
            if checksum != f"sha256={expected}" or size != str(len(content)):
                raise ValueError("wheel RECORD content mismatch")
        return metadata["Version"]


def run(
    python: Path,
    arguments: list[str],
    root: Path,
    environment: dict[str, str],
    status: int = 0,
) -> str:
    result = subprocess.run(  # nosec B603
        [str(python), "-I", "-B", *arguments],
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    if result.returncode != status:
        raise RuntimeError(
            f"runtime smoke failed ({result.returncode}): {result.stderr}"
        )
    return result.stdout


def smoke(python: Path, expected: dict[str, str]) -> None:
    with tempfile.TemporaryDirectory(prefix="pp-wheel-smoke-") as temporary:
        root = Path(temporary)
        provider = root / "provider"
        provider.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "open(os.environ['SMOKE_PROVIDER_CALLED'], 'w').close()\n"
            "print(json.dumps(sys.argv[1:]))\n",
            encoding="utf-8",
        )
        provider.chmod(0o755)
        marker = root / "provider-called"
        package = root / "paranoid_podman"
        package.mkdir()
        for path in (
            package / "__init__.py",
            root / "yaml.py",
            root / "dotenv.py",
            root / "pathlib.py",
        ):
            path.write_text(
                "raise RuntimeError('untrusted cwd import')\n", encoding="utf-8"
            )
        environment = {
            "HOME": str(root),
            "PATH": "/nonexistent",
            "PYTHONPATH": str(root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PODMAN_GUARD_REAL_PODMAN": str(provider),
            "PODMAN_GUARD_COMPOSE_PROVIDER": str(provider),
            "PODMAN_GUARD_INSTALLATION_ID": "a" * 64,
            "SMOKE_PROVIDER_CALLED": str(marker),
        }
        inventory = json.loads(
            run(
                python,
                [
                    "-c",
                    "import importlib.metadata as m, json; print(json.dumps({d.metadata['Name'].lower().replace('_', '-'): d.version for d in m.distributions()}))",
                ],
                root,
                environment,
            )
        )
        if inventory != expected:
            raise ValueError(f"unexpected runtime distribution inventory: {inventory}")
        run(
            python,
            [
                "-c",
                "import yaml, dotenv, paranoid_podman; from paranoid_podman.common.layout import source_root; assert source_root() is None",
            ],
            root,
            environment,
        )
        module = ["-m", "paranoid_podman"]
        run(python, module, root, environment, status=126)
        run(
            python,
            [*module, "podman", "run", "--privileged", "example.invalid/image"],
            root,
            environment,
            status=125,
        )
        if marker.exists():
            raise ValueError("denied arguments reached the provider")
        for entry in ("podman", "docker"):
            arguments = json.loads(
                run(
                    python,
                    [*module, entry, "run", "example.invalid/image"],
                    root,
                    environment,
                )
            )
            if (
                "--security-opt=no-new-privileges" not in arguments
                or f"--label=io.github.paranoid-podman.installation={'a' * 64}"
                not in arguments
            ):
                raise ValueError("runtime hardening/provenance was not preserved")
        for entry in ("compose-guard", "podman-compose", "docker-compose"):
            run(python, [*module, entry, "--unknown"], root, environment, status=125)
        run(
            python,
            [*module, "podman", "compose", "--unknown"],
            root,
            environment,
            status=125,
        )
        run(
            python,
            [*module, "paranoid-podman", "build-context", "audit", str(root)],
            root,
            environment,
        )
        run(
            python,
            [
                *module,
                "lifecycle",
                "status",
                "--bindir",
                str(root / "bin"),
                "--libdir",
                str(root / "installation"),
            ],
            root,
            environment,
            status=1,
        )
        if any(root.rglob("__pycache__")):
            raise ValueError("runtime wrote bytecode into the caller's directory")
        print(
            "Isolated runtime smoke passed; inventory:",
            json.dumps(inventory, sort_keys=True),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    options = parser.parse_args()
    version = wheel_metadata(options.wheel, options.sha256)
    inventory = json.loads(options.inventory.read_text(encoding="utf-8"))
    if (
        set(inventory) != {"paranoid-podman", "pyyaml", "python-dotenv"}
        or inventory["paranoid-podman"] != version
    ):
        raise ValueError(
            "expected inventory must contain only the project and runtime dependencies"
        )
    smoke(options.python.absolute(), inventory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
