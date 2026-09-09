"""Opt-in tests against a disposable rootless Podman environment."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUARD = PROJECT_ROOT / "bin" / "podman"


class RootlessPodmanIntegrationTests(unittest.TestCase):
    """Exercise real mount enforcement without touching existing resources."""

    @classmethod
    def setUpClass(cls) -> None:
        if os.environ.get("PARANOID_PODMAN_RUN_ROOTLESS_TESTS") != "1":
            raise unittest.SkipTest(
                "set PARANOID_PODMAN_RUN_ROOTLESS_TESTS=1 for real Podman tests"
            )
        cls.real_podman = Path(
            os.environ.get("PARANOID_PODMAN_REAL_PODMAN", "/usr/bin/podman")
        )
        cls.compose_provider = Path(
            os.environ.get(
                "PARANOID_PODMAN_REAL_COMPOSE_PROVIDER",
                "/usr/bin/podman-compose",
            )
        )
        cls.image = os.environ.get("PARANOID_PODMAN_TEST_IMAGE", "")
        if not cls.real_podman.is_file() or not os.access(cls.real_podman, os.X_OK):
            raise RuntimeError("PARANOID_PODMAN_REAL_PODMAN is not executable")
        if not cls.compose_provider.is_file() or not os.access(
            cls.compose_provider, os.X_OK
        ):
            raise RuntimeError(
                "PARANOID_PODMAN_REAL_COMPOSE_PROVIDER is not executable"
            )
        if not cls.image:
            raise RuntimeError("PARANOID_PODMAN_TEST_IMAGE is required")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]{0,511}", cls.image):
            raise RuntimeError("PARANOID_PODMAN_TEST_IMAGE is malformed")
        result = subprocess.run(
            [str(cls.real_podman), "image", "exists", cls.image],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "PARANOID_PODMAN_TEST_IMAGE must already exist locally; "
                "the test suite never pulls images"
            )
        cls.installation_id = uuid.uuid4().hex + uuid.uuid4().hex

    def setUp(self) -> None:
        self.test_id = f"pp-test-{uuid.uuid4().hex[:12]}"
        self.resources: list[str] = []

    def tearDown(self) -> None:
        for container in reversed(self.resources):
            subprocess.run(
                [str(self.real_podman), "rm", "--force", container],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30,
            )

    def run_guard(
        self, arguments: list[str], *, cwd: Path, timeout: int = 60
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "PODMAN_GUARD_REAL_PODMAN": str(self.real_podman),
                "PODMAN_GUARD_COMPOSE_PROVIDER": str(self.compose_provider),
                "PODMAN_GUARD_INSTALLATION_ID": self.installation_id,
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/synthetic-dbus",
                "DISPLAY": ":99",
                "SSH_AUTH_SOCK": "/tmp/synthetic-agent.sock",
                "SYNTHETIC_API_TOKEN": "synthetic-runtime-secret",
            }
        )
        return subprocess.run(
            [str(GUARD), *arguments],
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

    @staticmethod
    def prepare_project(project: Path) -> None:
        (project / ".devcontainer").mkdir()
        (project / ".git").mkdir()
        (project / ".devcontainer" / "devcontainer.json").write_text(
            "{}\n", encoding="utf-8"
        )
        (project / ".git" / "config").write_text(
            "[core]\n\trepositoryformatversion = 0\n", encoding="utf-8"
        )
        (project / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (project / ".devcontainer.json").write_text("{}\n", encoding="utf-8")
        (project / ".devcontainer" / "Dockerfile").write_text(
            "FROM scratch\n", encoding="utf-8"
        )
        (project / ".devcontainer" / "compose.yaml").write_text(
            "services: {}\n", encoding="utf-8"
        )
        (project / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        (project / "source.txt").write_text("before\n", encoding="utf-8")

    @staticmethod
    def protected_write_probe() -> str:
        protected = (
            "/workspace/.devcontainer/devcontainer.json",
            "/workspace/.git/config",
            "/workspace/.devcontainer.json",
            "/workspace/.devcontainer/Dockerfile",
            "/workspace/.devcontainer/compose.yaml",
        )
        return "\n".join(
            (
                "set -eu",
                'test -z "${SSH_AUTH_SOCK+x}"',
                'test -z "${DBUS_SESSION_BUS_ADDRESS+x}"',
                'test -z "${DISPLAY+x}"',
                'test -z "${SYNTHETIC_API_TOKEN+x}"',
                "printf 'after\\n' > /workspace/source.txt",
                "printf '# edited\\n' >> /workspace/Dockerfile",
                "printf '# edited\\n' >> /workspace/compose.yaml",
                f"for protected in {' '.join(protected)}",
                "do",
                "  if printf 'forbidden\\n' > \"$protected\" 2>/dev/null; then",
                "    echo 'protected write unexpectedly succeeded' >&2",
                "    exit 71",
                "  fi",
                "done",
            )
        )

    def test_direct_mounts_are_enforced_by_real_podman(self) -> None:
        with tempfile.TemporaryDirectory(prefix="paranoid-podman-runtime-") as root:
            project = Path(root) / "project"
            project.mkdir()
            self.prepare_project(project)
            result = self.run_guard(
                [
                    "run",
                    "--rm",
                    "--mount",
                    f"type=bind,src={project},target=/workspace",
                    "--env=SSH_AUTH_SOCK=/tmp/synthetic-agent.sock",
                    "--env=SYNTHETIC_API_TOKEN=synthetic-runtime-secret",
                    self.image,
                    "sh",
                    "-c",
                    self.protected_write_probe(),
                ],
                cwd=project,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (project / "source.txt").read_text(encoding="utf-8"), "after\n"
            )
            self.assertEqual(
                (project / "Dockerfile").read_text(encoding="utf-8"),
                "FROM scratch\n# edited\n",
            )
            denied = self.run_guard(
                ["run", "--rm", "--volume", "/:/host", self.image], cwd=project
            )
            self.assertEqual(denied.returncode, 125, denied.stderr)

    def test_real_lifecycle_requires_guard_provenance(self) -> None:
        with tempfile.TemporaryDirectory(prefix="paranoid-podman-runtime-") as root:
            project = Path(root)
            guarded = f"{self.test_id}-guarded"
            legacy = f"{self.test_id}-legacy"
            self.resources.extend((guarded, legacy))
            created = self.run_guard(
                [
                    "run",
                    "--name",
                    guarded,
                    "--detach",
                    self.image,
                    "sh",
                    "-c",
                    "sleep 300",
                ],
                cwd=project,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            executed = self.run_guard(
                ["exec", guarded, "sh", "-c", "test -r /etc/passwd"], cwd=project
            )
            self.assertEqual(executed.returncode, 0, executed.stderr)
            legacy_created = subprocess.run(
                [
                    str(self.real_podman),
                    "run",
                    "--name",
                    legacy,
                    "--detach",
                    "--security-opt=no-new-privileges",
                    "--cap-drop=all",
                    self.image,
                    "sh",
                    "-c",
                    "sleep 300",
                ],
                cwd=project,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(legacy_created.returncode, 0, legacy_created.stderr)
            denied = self.run_guard(["exec", legacy, "sh", "-c", "true"], cwd=project)
            self.assertEqual(denied.returncode, 125, denied.stderr)
            self.assertIn("not created by the current guard policy", denied.stderr)

    def test_compose_mounts_and_exec_use_current_provenance(self) -> None:
        with tempfile.TemporaryDirectory(prefix="paranoid-podman-runtime-") as root:
            project = Path(root) / "project"
            project.mkdir()
            self.prepare_project(project)
            project_name = self.test_id.replace("-", "")
            container_name = f"{self.test_id}-compose"
            self.resources.append(container_name)
            (project / "compose.yaml").write_text(
                "services:\n"
                "  app:\n"
                f"    image: {self.image}\n"
                f"    container_name: {container_name}\n"
                "    pull_policy: never\n"
                # Mount/provenance checks do not need DNS or a systemd user bus.
                "    network_mode: none\n"
                "    command: [sh, -c, 'sleep 300']\n"
                "    volumes:\n"
                "      - .:/workspace\n",
                encoding="utf-8",
            )
            started = self.run_guard(
                ["compose", "--project-name", project_name, "up", "--detach"],
                cwd=project,
                timeout=120,
            )
            try:
                self.assertEqual(started.returncode, 0, started.stderr)
                executed = self.run_guard(
                    [
                        "compose",
                        "--project-name",
                        project_name,
                        "exec",
                        "-T",
                        "app",
                        "sh",
                        "-c",
                        self.protected_write_probe(),
                    ],
                    cwd=project,
                )
                self.assertEqual(executed.returncode, 0, executed.stderr)
                self.assertEqual(
                    (project / "source.txt").read_text(encoding="utf-8"),
                    "after\n",
                )
            finally:
                stopped = self.run_guard(
                    ["compose", "--project-name", project_name, "down"],
                    cwd=project,
                    timeout=120,
                )
                if stopped.returncode == 0:
                    self.resources.remove(container_name)
            self.assertEqual(stopped.returncode, 0, stopped.stderr)


if __name__ == "__main__":
    unittest.main()
