"""Regressions for check coverage and separation from host integrations."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import run_tests, validation

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CheckWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_bash_syntax_checks_a_broken_script_after_a_valid_one(self):
        valid = self.write("first.sh", "#!/bin/bash\ntrue\n")
        invalid = self.write("second.sh", "#!/bin/bash\nif then\n")
        with self.assertRaises(subprocess.CalledProcessError):
            validation.check_bash([valid, invalid])

    def test_syntax_includes_every_python_launcher_and_nested_src(self):
        self.write("install.sh", "#!/bin/bash\ntrue\n")
        names = (
            "bin/podman",
            "bin/compose-guard",
            "bin/devpod",
            "bin/paranoid-podman",
            "src/paranoid_podman/common/example.py",
        )
        for name in names:
            self.write(
                name, "raise RuntimeError('syntax checks must not execute this')\n"
            )
        validation.syntax(self.root)
        for name in names:
            with self.subTest(name=name):
                path = self.write(name, "def broken(:\n")
                with self.assertRaises(SyntaxError):
                    validation.syntax(self.root)
                path.write_text("pass\n", encoding="utf-8")
        self.assertEqual(list(self.root.rglob("__pycache__")), [])

    def test_invalid_generated_bash_fails_the_syntax_gate(self):
        self.write("install.sh", "#!/bin/bash\ntrue\n")
        generated = self.write("generated.sh", "#!/bin/bash\nif then\n")
        with (
            mock.patch.object(
                validation, "generated_launchers", return_value=[generated]
            ),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            validation.syntax(self.root)

    def test_publishable_tree_excludes_private_files_and_symlink_targets(self):
        included = {
            "README.md",
            "bin/podman",
            "src/paranoid_podman/common/example.py",
            "tests/support/synthetic.txt",
            ".github/workflows/ci.yml",
        }
        excluded = {
            "private.md",
            "tmp/notes.md",
            ".git/config",
            ".agents/instructions.md",
            ".ssh/id_ed25519",
            "bin/.env",
            "src/.venv/ignored.py",
            "src/__pycache__/ignored.py",
            "tests/tmp/notes.txt",
        }
        for name in included | excluded:
            self.write(name, "synthetic fixture\n")
        external = self.root / "private"
        external.mkdir()
        (external / "outside.py").write_text("private data\n", encoding="utf-8")
        (self.root / "src" / "linked.py").symlink_to(external / "outside.py")
        (self.root / "src" / "linked").symlink_to(external, target_is_directory=True)
        (self.root / "docs").symlink_to(external, target_is_directory=True)
        actual = {
            str(path.relative_to(self.root))
            for path in validation.publishable_files(self.root)
        }
        self.assertEqual(actual, included)

    def test_local_runner_discards_ambient_secrets_and_integration_opt_ins(self):
        self.write("tests/__init__.py", "")
        self.write(
            "tests/test_environment.py",
            """import os
import stat
import sys
import unittest
from pathlib import Path

class EnvironmentTests(unittest.TestCase):
    def test_isolation(self):
        for key in ('SSH_AUTH_SOCK', 'SYNTHETIC_SECRET', 'PODMAN_GUARD_REAL_PODMAN'):
            self.assertNotIn(key, os.environ)
        for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_RUNTIME_DIR'):
            directory = Path(os.environ[key])
            self.assertTrue(directory.is_dir())
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        for name in ('ROOTLESS', 'SSH_AGENT', 'DEVPOD_SSH', 'COMPOSE_PROVIDER'):
            self.assertEqual(os.environ['PARANOID_PODMAN_RUN_' + name + '_TESTS'], '0')
        self.assertEqual(sys.stdin.read(), '')
""",
        )
        inherited = {
            "SSH_AUTH_SOCK": "/synthetic/agent.sock",
            "SYNTHETIC_SECRET": "synthetic-parent-value",
            "PODMAN_GUARD_REAL_PODMAN": "/synthetic/provider",
            **{flag: "1" for flag, _ in run_tests.INTEGRATIONS.values()},
        }
        with mock.patch.dict(os.environ, inherited):
            self.assertEqual(run_tests.run_local_tests(self.root), 0)
        self.assertEqual(list(self.root.rglob("__pycache__")), [])

    def test_local_runner_propagates_test_failure(self):
        self.write("tests/__init__.py", "")
        self.write(
            "tests/test_failure.py",
            "import unittest\nclass Failure(unittest.TestCase):\n"
            "    def test_failure(self):\n        self.fail('synthetic failure')\n",
        )
        self.assertNotEqual(run_tests.run_local_tests(self.root), 0)

    def test_only_all_mode_requests_network_auditing_and_neither_runs_integrations(
        self,
    ):
        scripts = self.root / "scripts"
        scripts.mkdir()
        shutil.copyfile(PROJECT_ROOT / "scripts/check.sh", scripts / "check.sh")
        for name in ("test", "lint", "audit"):
            path = self.write(
                f"scripts/{name}.sh",
                f'#!/bin/bash\nprintf "%s\\n" "{name} $*" >> calls.txt\n',
            )
            path.chmod(0o755)
        cases = (
            ([], ["test ", "lint ", "audit secrets"]),
            (["local"], ["test ", "lint ", "audit secrets"]),
            (["all"], ["test ", "lint ", "audit secrets", "audit dependencies"]),
        )
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                record = self.root / "calls.txt"
                record.write_text("", encoding="utf-8")
                subprocess.run(
                    ["bash", str(scripts / "check.sh"), *arguments],
                    cwd=self.root,
                    check=True,
                    timeout=10,
                )
                self.assertEqual(
                    record.read_text(encoding="utf-8").splitlines(), expected
                )


if __name__ == "__main__":
    unittest.main()
