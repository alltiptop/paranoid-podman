import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import delivery as artifact_delivery

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE = PROJECT_ROOT / "tools" / "lifecycle.py"
ENTRY_POINTS = (
    "podman",
    "docker",
    "compose-guard",
    "podman-compose",
    "docker-compose",
    "paranoid-podman",
)
DEVPOD_ENTRY_POINTS = ("devpod",)


class LifecycleFixture(unittest.TestCase):
    def delivery(self):
        return artifact_delivery.require().delivery

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temporary_directory.name)
        self.binary_directory = self.test_root / "bin"
        self.install_root = self.test_root / "share" / "paranoid-podman"
        self.provider_directory = self.test_root / "providers"
        self.provider_directory.mkdir()
        self.podman = self.provider_directory / "podman"
        self.compose = self.provider_directory / "podman-compose"
        self.provider_output = self.test_root / "provider.json"
        self.malicious_output = self.test_root / "unexpected-provider"
        self.write_provider(self.podman, "podman")
        self.write_provider(self.compose, "compose")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def write_provider(self, destination, provider_type, version=None):
        version = version or ("6.1.0" if provider_type == "podman" else "1.6.0")
        source = f"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
if arguments == ["--version"]:
    print("{provider_type} version {version}")
elif (
    "{provider_type}" == "compose"
    and len(arguments) == 4
    and arguments[0] == "--podman-path"
    and arguments[2:] == ["version", "--short"]
):
    print("{version}")
elif arguments == ["info", "--format", "{{{{.Host.Security.Rootless}}}}"]:
    print("true")
    print("synthetic runtime warning", file=sys.stderr)
else:
    output = os.environ.get("PARANOID_LIFECYCLE_TEST_OUTPUT")
    if output:
        Path(output).write_text(json.dumps(arguments), encoding="utf-8")
"""
        destination.write_text(source, encoding="utf-8")
        destination.chmod(0o755)

    @staticmethod
    def write_devpod(destination, version="0.6.15"):
        destination.write_text(
            f"""#!/usr/bin/env python3
import json
import os
import sys

if sys.argv[1:] == ["version"]:
    print("v{version}")
    raise SystemExit(0)
if sys.argv[1:] == ["context", "list", "--output", "json"]:
    configured = os.environ.get("PARANOID_LIFECYCLE_TEST_SSH_CONFIG")
    print(json.dumps([{{"name": "default", "default": True}}] if configured else []))
    raise SystemExit(0)
if sys.argv[1:] == [
    "context", "options", "--context", "default", "--output", "json"
]:
    configured = os.environ.get("PARANOID_LIFECYCLE_TEST_SSH_CONFIG", "")
    print(json.dumps({{"SSH_CONFIG_PATH": {{"value": configured}}}}))
    raise SystemExit(0)
raise SystemExit(2)
""",
            encoding="utf-8",
        )
        destination.chmod(0o755)

    def run_lifecycle(self, action, *arguments, with_delivery=True):
        environment = os.environ.copy()
        environment["HOME"] = str(self.test_root / "user-home")
        environment["PIP_NO_INDEX"] = "1"
        environment["PIP_FIND_LINKS"] = ""
        environment["PATH"] = os.pathsep.join(
            (
                str(self.binary_directory),
                str(self.provider_directory),
                "/usr/bin",
                "/bin",
            )
        )
        if (
            action in {"install", "update"}
            and "--wheel" not in arguments
            and with_delivery
        ):
            arguments = (*arguments, *artifact_delivery.require().arguments())
        if action == "install" and "--devpod" not in arguments:
            arguments = (*arguments, "--without-devpod")
        return subprocess.run(
            [
                sys.executable,
                str(LIFECYCLE),
                action,
                "--bindir",
                str(self.binary_directory),
                "--libdir",
                str(self.install_root),
                *arguments,
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def provider_arguments(self, entry_point="podman"):
        environment = os.environ.copy()
        environment.update(
            {
                "PARANOID_LIFECYCLE_TEST_OUTPUT": str(self.provider_output),
                "PODMAN_GUARD_REAL_PODMAN": str(self.malicious_output),
            }
        )
        result = subprocess.run(
            [
                str(self.binary_directory / entry_point),
                "run",
                "example.invalid/image",
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        arguments = None
        if self.provider_output.exists():
            arguments = json.loads(self.provider_output.read_text(encoding="utf-8"))
        return result, arguments

    def install(self, *extra_arguments):
        return self.run_lifecycle(
            "install",
            "--podman",
            str(self.podman),
            "--compose-provider",
            str(self.compose),
            *extra_arguments,
        )

    def lifecycle_providers(self):
        return {
            "python": str(Path(sys._base_executable).resolve()),
            "python_version": ".".join(map(str, sys.version_info[:3])),
            "podman": str(self.podman),
            "podman_version": "6.1.0",
            "compose": str(self.compose),
            "compose_version": "1.6.0",
        }
