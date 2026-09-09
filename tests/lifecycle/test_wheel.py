"""Explicit artifact tests: real offline pip/venv, synthetic external providers."""

import argparse
import contextlib
import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from paranoid_podman.lifecycle import files as lifecycle_files
from paranoid_podman.lifecycle import operations as lifecycle_operations
from paranoid_podman.lifecycle.artifacts import load_delivery, record_digest
from paranoid_podman.lifecycle.errors import LifecycleError
from tests.lifecycle import test_operations
from tests.support import delivery as artifact_delivery
from tests.support import lifecycle as lifecycle_support


class WheelLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prepared = artifact_delivery.require()
        cls.wheelhouse = prepared.wheelhouse
        cls.installer = prepared.delivery.installer_python
        cls.wheel = prepared.delivery.project.path
        cls.digest = prepared.delivery.project.sha256

    def setUp(self):
        self.fixture = lifecycle_support.LifecycleFixture()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        # Exercise spaces and shell metacharacters in both private/public paths.
        self.fixture.install_root = (
            self.fixture.test_root / "private spaces '$`" / "app"
        )
        self.fixture.binary_directory = self.fixture.test_root / "bin spaces '$`"
        for provider in (self.fixture.podman, self.fixture.compose):
            provider.write_text(
                provider.read_text().replace(
                    "#!/usr/bin/env python3", f"#!{sys.executable} -I", 1
                )
            )

    def delivery_options(self, wheelhouse=None, *, delivery=None):
        return (
            "--wheel",
            str(delivery.project.path if delivery else self.wheel),
            "--sha256",
            delivery.project.sha256 if delivery else self.digest,
            "--wheelhouse",
            str(
                wheelhouse
                or (delivery.project.path.parent if delivery else self.wheelhouse)
            ),
            "--installer-python",
            str(self.installer),
        )

    def changed_delivery(self):
        copied = self.fixture.test_root / "changed-wheels"
        shutil.copytree(self.wheelhouse, copied)
        wheel = copied / self.wheel.name
        with zipfile.ZipFile(wheel) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        members["paranoid_podman/__init__.py"] += b"\n# Synthetic next artifact.\n"
        record_name = next(
            name for name in members if name.endswith(".dist-info/RECORD")
        )
        output = io.StringIO()
        writer = csv.writer(output)
        for name, content in members.items():
            if name != record_name:
                writer.writerow([name, record_digest(content), len(content)])
        writer.writerow([record_name, "", ""])
        members[record_name] = output.getvalue().encode()
        with zipfile.ZipFile(wheel, "w") as archive:
            for name, content in members.items():
                archive.writestr(name, content)
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        lock = copied / "runtime.lock"
        lock.write_text(lock.read_text().replace(self.digest, digest))
        return load_delivery(wheel, digest, copied, self.installer)

    def install(self, *extra):
        result = self.fixture.install(*self.delivery_options(), *extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return (self.fixture.install_root / "current").resolve()

    def installed_lifecycle(self, action, *extra):
        return self.run_command(
            [
                str(self.fixture.install_root / "current/launch"),
                "lifecycle",
                action,
                "--bindir",
                str(self.fixture.binary_directory),
                "--libdir",
                str(self.fixture.install_root),
                *extra,
            ]
        )

    def run_command(self, arguments):
        root = self.fixture.test_root
        for name in ("yaml.py", "dotenv.py", "pathlib.py", "paranoid_podman.py"):
            (root / name).write_text("raise RuntimeError('untrusted cwd import')\n")
        environment = {
            "PATH": os.defpath,
            "HOME": str(root / "home"),
            "PYTHONPATH": str(root),
            "PIP_INDEX_URL": "https://example.invalid/forbidden",
            "PODMAN_GUARD_REAL_PODMAN": "/nonexistent",
            "PODMAN_GUARD_INSTALLATION_ID": "caller-must-not-override",
            "PARANOID_LIFECYCLE_TEST_OUTPUT": str(self.fixture.provider_output),
        }
        return subprocess.run(
            arguments,
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def test_installed_commands_status_update_and_removal_without_source_imports(self):
        release = self.install()
        for entry in ("podman", "docker"):
            result = self.run_command(
                [
                    str(self.fixture.binary_directory / entry),
                    "run",
                    "example.invalid/image",
                ]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            args = json.loads(self.fixture.provider_output.read_text())
            self.assertIn("--security-opt=no-new-privileges", args)
            record = json.loads(
                (self.fixture.install_root / ".install.json").read_text()
            )
            self.assertIn(
                f"--label=io.github.paranoid-podman.installation={record['installation_id']}",
                args,
            )
        self.fixture.provider_output.unlink()
        result = self.run_command(
            [
                str(self.fixture.binary_directory / "podman"),
                "run",
                "--privileged",
                "example.invalid/image",
            ]
        )
        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertFalse(self.fixture.provider_output.exists())
        for action, options in (
            ("status", ()),
            ("update", self.delivery_options()),
            ("status", ()),
        ):
            result = self.installed_lifecycle(action, *options)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.fixture.install_root / "current").resolve(), release)
        self.assertFalse(any(release.rglob("*.pyc")))
        removed = self.installed_lifecycle("uninstall")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertFalse(self.fixture.install_root.exists())

    def test_compose_aliases_and_optional_devpod_use_installed_dispatch(self):
        devpod = self.fixture.provider_directory / "devpod"
        self.fixture.write_devpod(devpod)
        devpod.write_text(
            devpod.read_text().replace(
                "#!/usr/bin/env python3", f"#!{sys.executable} -I", 1
            )
        )
        self.install("--devpod", str(devpod))
        compose = self.fixture.compose
        compose.write_text(f"""#!{sys.executable} -I
import json, sys
from pathlib import Path
arguments = sys.argv[1:]
if '--format' in arguments and 'json' in arguments:
    print(json.dumps({{'services': {{'app': {{'image': 'example.invalid/image'}}}}}}))
elif 'config' in arguments:
    print('services:\\n  app:\\n    image: example.invalid/image')
else:
    Path({str(self.fixture.provider_output)!r}).write_text(json.dumps(arguments))
""")
        project = self.fixture.test_root / "compose-project"
        project.mkdir()
        source = project / "compose.yaml"
        source.write_text("services:\n  app:\n    image: example.invalid/image\n")
        for entry, prefix in (
            ("compose-guard", []),
            ("podman-compose", []),
            ("docker-compose", []),
            ("podman", ["compose"]),
            ("docker", ["compose"]),
        ):
            result = self.run_command(
                [
                    str(self.fixture.binary_directory / entry),
                    *prefix,
                    "-f",
                    str(source),
                    "config",
                    "--quiet",
                ]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_command(
            [str(self.fixture.binary_directory / "devpod"), "version"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0.6.15", result.stdout)
        result = self.run_command(
            [
                str(self.fixture.binary_directory / "paranoid-podman"),
                "build-context",
                "audit",
                str(project),
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_repeated_wheel_update_and_backup_restoration(self):
        previous = self.install()
        record_path = self.fixture.install_root / ".install.json"
        provenance = json.loads(record_path.read_text())["installation_id"]
        delivery = self.changed_delivery()
        for _ in range(2):
            result = self.fixture.run_lifecycle(
                "update", *self.delivery_options(delivery=delivery)
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        current = (self.fixture.install_root / "current").resolve()
        self.assertNotEqual(current, previous)
        self.assertEqual(
            json.loads(record_path.read_text())["installation_id"], provenance
        )
        self.assertTrue(previous.exists())
        removed = self.installed_lifecycle("uninstall")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertFalse(self.fixture.install_root.exists())
        self.fixture.binary_directory.mkdir(parents=True, exist_ok=True)
        original_launcher = self.fixture.binary_directory / "docker"
        original_launcher.write_text("original user launcher\n")
        self.install("--backup-existing")
        removed = self.installed_lifecycle("uninstall")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual(original_launcher.read_text(), "original user launcher\n")

    def test_update_rollback_restores_wheel_release_on_metadata_failure(self):
        self.install()
        record = self.fixture.install_root / ".install.json"
        original = record.read_bytes()
        previous = (self.fixture.install_root / "current").resolve()
        delivery = self.changed_delivery()
        atomic_json = lifecycle_files.atomic_json

        def fail_record(path, value):
            if path == record:
                raise OSError("synthetic metadata write failure")
            return atomic_json(path, value)

        with (
            contextlib.redirect_stdout(io.StringIO()),
            mock.patch.object(lifecycle_files, "atomic_json", fail_record),
        ):
            with self.assertRaisesRegex(OSError, "synthetic"):
                lifecycle_operations.update(
                    self.fixture.binary_directory,
                    self.fixture.install_root,
                    None,
                    None,
                    False,
                    delivery=delivery,
                )
        self.assertEqual((self.fixture.install_root / "current").resolve(), previous)
        self.assertEqual(record.read_bytes(), original)
        self.assertEqual(
            list((self.fixture.install_root / "releases").iterdir()), [previous]
        )

    def test_failed_installer_preserves_current_and_cleans_incomplete_release(
        self,
    ):
        self.install()
        record = self.fixture.install_root / ".install.json"
        original = record.read_bytes()
        previous = (self.fixture.install_root / "current").resolve()
        broken = self.fixture.test_root / "installer-python"
        broken.write_text(f"#!{sys.executable}\nraise SystemExit(7)\n")
        broken.chmod(0o755)
        options = list(self.delivery_options(delivery=self.changed_delivery()))
        options[-1] = str(broken)
        result = self.fixture.run_lifecycle("update", *options)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.fixture.install_root / "current").resolve(), previous)
        self.assertEqual(record.read_bytes(), original)
        self.assertEqual(
            list((self.fixture.install_root / "releases").iterdir()), [previous]
        )
        self.assertFalse(list(self.fixture.install_root.glob(".artifacts-*")))

    def test_runtime_verification_failure_never_activates_new_release(self):
        self.install()
        previous = (self.fixture.install_root / "current").resolve()
        delivery = self.changed_delivery()
        with (
            contextlib.redirect_stdout(io.StringIO()),
            mock.patch(
                "paranoid_podman.lifecycle.wheel_release.verify_runtime",
                side_effect=LifecycleError("synthetic integrity failure"),
            ),
            self.assertRaisesRegex(LifecycleError, "integrity failure"),
        ):
            lifecycle_operations.update(
                self.fixture.binary_directory,
                self.fixture.install_root,
                None,
                None,
                False,
                delivery=delivery,
            )
        self.assertEqual((self.fixture.install_root / "current").resolve(), previous)
        self.assertEqual(
            list((self.fixture.install_root / "releases").iterdir()), [previous]
        )

    def test_changed_wheel_creates_new_release_and_preserves_provenance(self):
        previous = self.install()
        record = self.fixture.install_root / ".install.json"
        provenance = json.loads(record.read_text())["installation_id"]
        delivery = self.changed_delivery()
        result = self.installed_lifecycle(
            "update", *self.delivery_options(delivery=delivery)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual((self.fixture.install_root / "current").resolve(), previous)
        self.assertEqual(json.loads(record.read_text())["installation_id"], provenance)
        result = self.installed_lifecycle("uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.fixture.install_root.exists())

    def test_dry_run_missing_wheel_and_hash_failure_never_create_installation(self):
        result = self.fixture.install(*self.delivery_options(), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.fixture.install_root.exists())
        copied = self.fixture.test_root / "wheelhouse"
        shutil.copytree(self.wheelhouse, copied)
        dependency = next(copied.glob("pyyaml*.whl"))
        dependency.write_bytes(dependency.read_bytes() + b"corrupt")
        result = self.fixture.install(*self.delivery_options(copied))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.fixture.install_root.exists())
        dependency.unlink()
        result = self.fixture.install(*self.delivery_options(copied))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.fixture.install_root.exists())

    def test_status_and_uninstall_reject_modified_runtime_before_changes(self):
        release = self.install()
        package = next(
            (release / "runtime/lib").glob(
                "python*/site-packages/paranoid_podman/__init__.py"
            )
        )
        package.write_text("# locally modified\n")
        for action in ("status", "uninstall"):
            result = self.fixture.run_lifecycle(action)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("modified", result.stderr)
            self.assertTrue((self.fixture.binary_directory / "podman").exists())
        self.assertEqual((self.fixture.install_root / "current").resolve(), release)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--installer-python", type=Path, required=True)
    options = parser.parse_args()
    artifact_delivery.configure(options.wheelhouse, options.installer_python)
    suite = unittest.TestSuite(
        [
            unittest.defaultTestLoader.loadTestsFromTestCase(
                test_operations.LifecycleIntegrationTests
            ),
            unittest.defaultTestLoader.loadTestsFromTestCase(WheelLifecycleTests),
        ]
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
