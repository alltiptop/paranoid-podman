"""Contracts affected by relocating implementation into the package."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from paranoid_podman.common.executables import is_external_executable
from scripts.build_wheel import copy_build_source

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PackageLayoutTests(unittest.TestCase):
    def source_python(self, code, *, environment=None):
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                f"import sys; sys.path.insert(0, {str(PROJECT_ROOT / 'src')!r}); {code}",
            ],
            env=environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()

    def test_package_and_dispatcher_do_not_eagerly_import_interfaces(self):
        loaded = json.loads(
            self.source_python(
                "import json, paranoid_podman, paranoid_podman.__main__; print(json.dumps(sorted(sys.modules)))"
            )
        )
        for name in (
            "yaml",
            "dotenv",
            "subprocess",
            "paranoid_podman.devpod.cli",
            "paranoid_podman.compose.cli",
        ):
            self.assertNotIn(name, loaded)

    def test_source_provenance_seed_and_explicit_installation_id_are_preserved(self):
        code = "from paranoid_podman.common.provenance import GUARD_INSTALLATION_ID; print(GUARD_INSTALLATION_ID)"
        environment = {
            key: value
            for key, value in os.environ.items()
            if key != "PODMAN_GUARD_INSTALLATION_ID"
        }
        expected = hashlib.sha256(
            f"source:{PROJECT_ROOT / 'bin'}:{os.getuid()}".encode()
        ).hexdigest()
        self.assertEqual(self.source_python(code, environment=environment), expected)
        environment["PODMAN_GUARD_INSTALLATION_ID"] = "b" * 64
        self.assertEqual(self.source_python(code, environment=environment), "b" * 64)

    def test_shared_policy_does_not_import_interfaces_or_process_tools(self):
        loaded = json.loads(
            self.source_python(
                "import json; from paranoid_podman.common import build_inputs, diagnostics, environment, paths, provenance; "
                "print(json.dumps(sorted(sys.modules)))"
            )
        )
        self.assertNotIn("subprocess", loaded)
        for domain in ("podman", "compose", "devpod", "lifecycle"):
            prefix = f"paranoid_podman.{domain}"
            self.assertFalse(
                any(name == prefix or name.startswith(prefix + ".") for name in loaded)
            )

    def test_podman_policy_imports_do_not_load_execution(self):
        loaded = json.loads(
            self.source_python(
                "import json; "
                "from paranoid_podman.podman import build, commands, runtime; "
                "print(json.dumps(sorted(sys.modules)))"
            )
        )
        self.assertNotIn("subprocess", loaded)
        self.assertNotIn("paranoid_podman.podman.execution", loaded)
        self.assertNotIn("paranoid_podman.podman.cli", loaded)

    def test_compose_policy_imports_do_not_load_provider_execution(self):
        loaded = json.loads(
            self.source_python(
                "import json; from paranoid_podman.compose import policy, source; "
                "print(json.dumps(sorted(sys.modules)))"
            )
        )
        self.assertNotIn("subprocess", loaded)
        self.assertNotIn("paranoid_podman.compose.provider", loaded)
        self.assertNotIn("paranoid_podman.compose.cli", loaded)

    def test_ssh_configuration_imports_do_not_load_agents_or_process_tools(self):
        loaded = json.loads(
            self.source_python(
                "import json, paranoid_podman.devpod.ssh_config; "
                "print(json.dumps(sorted(sys.modules)))"
            )
        )
        for name in (
            "subprocess",
            "paranoid_podman.devpod.agent",
            "paranoid_podman.devpod.provider",
            "paranoid_podman.devpod.session",
            "paranoid_podman.management.cli",
        ):
            self.assertNotIn(name, loaded)

    def test_missing_compose_dependencies_keep_guard_diagnostics(self):
        cases = (
            (
                "yaml",
                "from paranoid_podman.compose.source import load_resolved_config; "
                "load_resolved_config('services: {}')",
                "PyYAML is required for structured Compose validation",
            ),
            (
                "dotenv",
                "from pathlib import Path; "
                "from paranoid_podman.compose.dotenv import _read_dotenv_bindings; "
                "_read_dotenv_bindings(Path('.env'), 'test input', required=True)",
                "python-dotenv is required for guarded Compose interpolation",
            ),
        )
        for dependency, invocation, expected in cases:
            with self.subTest(dependency=dependency):
                result = self.source_python(
                    f"sys.modules[{dependency!r}] = None; "
                    "from paranoid_podman.compose.errors import PolicyViolation\n"
                    f"try:\n    {invocation}\n"
                    "except PolicyViolation as error:\n    print(error)\n"
                )
                self.assertEqual(result, expected)

    def test_source_launcher_ignores_same_name_modules_in_cwd_and_pythonpath(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "podman_guard.py").write_text(
                "raise RuntimeError('cwd module loaded')\n", encoding="utf-8"
            )
            package = root / "paranoid_podman"
            package.mkdir()
            (package / "__init__.py").write_text(
                "raise RuntimeError('cwd package loaded')\n", encoding="utf-8"
            )
            environment = {
                **os.environ,
                "PYTHONPATH": str(root),
                "PODMAN_GUARD_REAL_PODMAN": str(
                    PROJECT_ROOT / "tests/support/fake_provider.py"
                ),
            }
            result = subprocess.run(
                [
                    str(PROJECT_ROOT / "bin/podman"),
                    "run",
                    "--privileged",
                    "example.invalid/image",
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(result.returncode, 125, result.stderr)
            self.assertNotIn("cwd module loaded", result.stderr)
            self.assertNotIn("cwd package loaded", result.stderr)

    def test_provider_recursion_rejects_symlink_and_hardlink_aliases(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wrappers = root / "bin"
            wrappers.mkdir()
            wrapper = wrappers / "podman"
            wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            wrapper.chmod(0o755)
            symbolic = root / "symbolic"
            symbolic.symlink_to(wrapper)
            hardlink = root / "hardlink"
            hardlink.hardlink_to(wrapper)
            provider = root / "provider"
            provider.write_bytes(wrapper.read_bytes())
            provider.chmod(0o755)
            for candidate in (wrapper, symbolic, hardlink):
                with self.subTest(candidate=candidate.name):
                    self.assertFalse(is_external_executable(str(candidate), wrappers))
            self.assertTrue(is_external_executable(str(provider), wrappers))

    def test_build_source_excludes_tooling_tests_and_unlisted_package_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, staging = root / "project", root / "staging"
            package = source / "src/paranoid_podman"
            package.mkdir(parents=True)
            for name in ("pyproject.toml", "README.md", "VERSION", "LICENSE"):
                (source / name).write_text("synthetic input\n", encoding="utf-8")
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "py.typed").touch()
            hidden = package / ".private"
            hidden.mkdir()
            (hidden / "config.py").write_text("private = True\n", encoding="utf-8")
            (package / "private.txt").write_text(
                "synthetic private input", encoding="utf-8"
            )
            (source / "requirements-dev.txt").write_text(
                "synthetic tooling", encoding="utf-8"
            )
            copy_build_source(source, staging)
            self.assertTrue((staging / "src/paranoid_podman/__init__.py").is_file())
            self.assertTrue((staging / "src/paranoid_podman/py.typed").is_file())
            self.assertFalse((staging / "src/paranoid_podman/private.txt").exists())
            self.assertFalse((staging / "src/paranoid_podman/.private").exists())
            self.assertFalse((staging / "requirements-dev.txt").exists())
            (package / "external.py").symlink_to(source / "README.md")
            with self.assertRaisesRegex(ValueError, "symlink"):
                copy_build_source(source, root / "rejected")


if __name__ == "__main__":
    unittest.main()
