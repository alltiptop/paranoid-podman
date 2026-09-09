"""Malformed local artifacts must fail before any installer process is started."""

import contextlib
import csv
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from paranoid_podman.lifecycle.artifacts import (
    inspect_wheel,
    load_delivery,
    record_digest,
)
from paranoid_podman.lifecycle.errors import LifecycleError
from paranoid_podman.lifecycle.integrity import release_inventory, verify_inventory
from paranoid_podman.lifecycle.runtime import INSTALLER_VERSION


def write_wheel(root, name, version, extra=None):
    prefix = f"{name.replace('-', '_')}-{version}.dist-info"
    metadata = f"Name: {name}\nVersion: {version}\n"
    if name == "paranoid-podman":
        metadata += (
            "Requires-Dist: PyYAML<7,>=6.0\nRequires-Dist: python-dotenv<2,>=1.0\n"
        )
    members = {f"{prefix}/METADATA": metadata.encode()}
    members.update(extra or {})
    record = f"{prefix}/RECORD"
    output = io.StringIO()
    writer = csv.writer(output)
    for filename, content in members.items():
        writer.writerow([filename, record_digest(content), len(content)])
    writer.writerow([record, "", ""])
    members[record] = output.getvalue().encode()
    wheel = root / f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for filename, content in members.items():
            archive.writestr(filename, content)
    return wheel


class WheelArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = write_wheel(self.root, "paranoid-podman", "0.1.0.dev0")
        write_wheel(self.root, "pyyaml", "6.0.3")
        write_wheel(self.root, "python-dotenv", "1.2.3")
        self.lock = self.root / "runtime.lock"
        self.lock.write_text(
            "".join(
                f"{w.name}=={w.version} --hash=sha256:{w.sha256}\n"
                for w in map(inspect_wheel, sorted(self.root.glob("*.whl")))
            )
        )
        self.digest = hashlib.sha256(self.project.read_bytes()).hexdigest()

    def load(self, **overrides):
        args = dict(
            project=self.project,
            digest=self.digest,
            wheelhouse=self.root,
            installer_python=Path("/usr/bin/python3"),
        )
        return load_delivery(**(args | overrides))

    def test_exact_runtime_set_and_external_installer_pin(self):
        self.assertEqual(
            set(self.load().inventory), {"paranoid-podman", "pyyaml", "python-dotenv"}
        )
        pin = Path(__file__).resolve().parents[2] / "requirements-installer.txt"
        self.assertEqual(pin.read_text().strip(), f"pip=={INSTALLER_VERSION}")

    def test_wrong_project_digest_and_changed_dependency_are_rejected(self):
        with self.assertRaisesRegex(LifecycleError, "SHA-256"):
            self.load(digest="0" * 64)
        dependency = next(self.root.glob("pyyaml*.whl"))
        dependency.write_bytes(dependency.read_bytes() + b"changed")
        with self.assertRaisesRegex(LifecycleError, "does not match its lock"):
            self.load()

    def test_missing_extra_and_duplicate_runtime_wheels_are_rejected(self):
        dependency = next(self.root.glob("pyyaml*.whl"))
        content = dependency.read_bytes()
        dependency.unlink()
        with self.assertRaises(LifecycleError):
            self.load()
        dependency.write_bytes(content)
        duplicate = self.root / "duplicate.whl"
        duplicate.write_bytes(content)
        with self.assertRaises(LifecycleError):
            self.load()
        duplicate.unlink()
        write_wheel(self.root, "setuptools", "80.9.0")
        with self.assertRaisesRegex(LifecycleError, "non-runtime"):
            self.load()

    def test_lock_cannot_inject_options_urls_or_unhashed_requirements(self):
        for text in (
            "--index-url https://example.invalid\n",
            "pyyaml @ https://example.invalid/x.whl\n",
            "pyyaml==6.0.3\n",
        ):
            with self.subTest(text=text):
                self.lock.write_text(text)
                with self.assertRaises(LifecycleError):
                    self.load()

    def test_symlink_artifacts_are_rejected(self):
        target = self.project.with_suffix(".saved")
        self.project.rename(target)
        self.project.symlink_to(target)
        with self.assertRaisesRegex(LifecycleError, "regular local file"):
            self.load()

    def test_wheel_paths_payload_and_record_must_be_valid(self):
        for filename in (
            "../outside",
            "/absolute",
            "tests/tool.py",
            "paranoid_podman/__pycache__/x.pyc",
        ):
            with self.subTest(filename=filename):
                wheel = write_wheel(
                    self.root, "paranoid-podman", "0.1.0.dev0", {filename: b"test"}
                )
                with self.assertRaises(LifecycleError):
                    inspect_wheel(wheel)
        wheel = write_wheel(self.root, "paranoid-podman", "0.1.0.dev0")
        with zipfile.ZipFile(wheel, "a") as archive:
            archive.writestr("paranoid_podman/unrecorded.py", b"test")
        with self.assertRaisesRegex(LifecycleError, "RECORD"):
            inspect_wheel(wheel)

    def test_runtime_metadata_cannot_request_remote_or_tooling_dependencies(self):
        for requirement in ("build>=1", "click @ https://example.invalid/click.whl"):
            with self.subTest(requirement=requirement):
                path = "pyyaml-6.0.3.dist-info/METADATA"
                wheel = write_wheel(
                    self.root,
                    "pyyaml",
                    "6.0.3",
                    {
                        path: f"Name: pyyaml\nVersion: 6.0.3\nRequires-Dist: {requirement}\n".encode()
                    },
                )
                with self.assertRaisesRegex(LifecycleError, "unreviewed requirement"):
                    inspect_wheel(wheel)

    def test_installed_inventory_detects_changes_and_never_follows_links(self):
        release = self.root / "release"
        release.mkdir()
        code = release / "code.py"
        code.write_text("pass\n")
        (release / "external").symlink_to(self.root, target_is_directory=True)
        manifest = release_inventory(release)
        self.assertEqual(set(manifest["files"]), {"code.py"})
        verify_inventory(release, manifest)
        for change in ("content", "mode", "link", "extra"):
            with self.subTest(change=change):
                if change == "content":
                    code.write_text("changed\n")
                elif change == "mode":
                    code.chmod(0o777)
                elif change == "link":
                    (release / "external").unlink()
                    (release / "external").symlink_to("/nonexistent")
                else:
                    (release / "extra.py").touch()
                with self.assertRaisesRegex(LifecycleError, "modified"):
                    verify_inventory(release, manifest)
                manifest = release_inventory(release)

    def test_unsupported_release_metadata_prevents_removal(self):
        from paranoid_podman.lifecycle.releases import remove_release

        release = self.root / "unsupported"
        release.mkdir()
        outside = self.root / "unowned"
        outside.write_text("keep\n")
        (release / ".manifest.json").write_text(
            json.dumps(
                {
                    "format": 1,
                    "project": "paranoid-podman",
                    "release": "unsupported",
                    "files": {},
                    "symlinks": {"../unowned": "anything"},
                }
            )
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(remove_release(release, False))
        self.assertTrue((release / ".manifest.json").is_file())
        self.assertEqual(outside.read_text(), "keep\n")


if __name__ == "__main__":
    unittest.main()
