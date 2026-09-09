import contextlib
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from paranoid_podman.lifecycle import cli as lifecycle_cli
from paranoid_podman.lifecycle import errors as lifecycle_errors
from paranoid_podman.lifecycle import files as lifecycle_files
from paranoid_podman.lifecycle import operations as lifecycle_operations
from paranoid_podman.lifecycle import paths as lifecycle_paths
from paranoid_podman.lifecycle import providers as lifecycle_providers
from paranoid_podman.lifecycle import releases as lifecycle_releases
from tests.support import delivery as artifact_delivery
from tests.support import lifecycle as support

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


class LifecycleIntegrationTests(support.LifecycleFixture):
    @classmethod
    def setUpClass(cls):
        artifact_delivery.require()

    def test_missing_management_launcher_metadata_is_rejected(self):
        installed = self.install()
        self.assertEqual(installed.returncode, 0, installed.stderr)
        record_path = self.install_root / ".install.json"
        record = json.loads(record_path.read_text())
        record["launchers"].pop("paranoid-podman")
        record_path.write_text(json.dumps(record))

        for action in ("status", "update", "uninstall"):
            with self.subTest(action=action):
                result = self.run_lifecycle(action)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "installed launcher metadata is incomplete", result.stderr
                )
        self.assertTrue((self.binary_directory / "paranoid-podman").exists())

    def test_installed_devpod_management_requires_the_optional_wrapper(self):
        installed = self.install()
        self.assertEqual(installed.returncode, 0, installed.stderr)

        result = subprocess.run(
            [
                str(self.binary_directory / "paranoid-podman"),
                "devpod",
                "configure",
                "example-workspace",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

        self.assertEqual(result.returncode, 125)
        self.assertIn("DevPod integration is not installed", result.stderr)
        self.assertIn("--devpod", result.stderr)

    def test_dry_run_describes_install_without_writing(self):
        result = self.install("--dry-run")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dry run complete", result.stdout)
        self.assertFalse(self.binary_directory.exists())
        self.assertFalse(self.install_root.exists())

        installed = self.install()
        self.assertEqual(installed.returncode, 0, installed.stderr)
        previewed_release = next(
            line
            for line in result.stdout.splitlines()
            if "would prepare wheel release" in line
        )
        self.assertIn(
            previewed_release.replace("would prepare", "verified"), installed.stdout
        )

    def test_install_uses_verified_provider_and_update_is_idempotent(self):
        result = self.install()

        self.assertEqual(result.returncode, 0, result.stderr)
        for entry_point in ENTRY_POINTS:
            self.assertTrue((self.binary_directory / entry_point).is_file())
            self.assertTrue(os.access(self.binary_directory / entry_point, os.X_OK))

        guard_result, arguments = self.provider_arguments()
        self.assertEqual(guard_result.returncode, 0, guard_result.stderr)
        self.assertIsNotNone(arguments)
        self.assertIn("--security-opt=no-new-privileges", arguments)
        record = json.loads(
            (self.install_root / ".install.json").read_text(encoding="utf-8")
        )
        installation_id = record["installation_id"]
        self.assertRegex(installation_id, r"^[0-9a-f]{64}$")
        self.assertIn(
            f"--label=io.github.paranoid-podman.installation={installation_id}",
            arguments,
        )
        current = (self.install_root / "current").resolve()
        manifest = json.loads((current / ".manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["installation_id_sha256"],
            hashlib.sha256(installation_id.encode()).hexdigest(),
        )
        self.assertEqual(stat.S_IMODE(self.install_root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((current / "launch").stat().st_mode), 0o700)
        self.assertFalse(self.malicious_output.exists())

        update = self.run_lifecycle("update")
        self.assertEqual(update.returncode, 0, update.stderr)
        self.assertIn("already current", update.stdout)

        status = self.run_lifecycle("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("installation files are intact", status.stdout)

    def test_status_rejects_tampered_installation_provenance(self):
        installed = self.install()
        self.assertEqual(installed.returncode, 0, installed.stderr)
        record_path = self.install_root / ".install.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["installation_id"] = "b" * 64
        record_path.write_text(json.dumps(record), encoding="utf-8")

        status = self.run_lifecycle("status")

        self.assertNotEqual(status.returncode, 0)
        self.assertIn("provenance does not match", status.stderr)

    def test_update_rejects_missing_installation_provenance_without_changes(self):
        installed = self.install()
        self.assertEqual(installed.returncode, 0, installed.stderr)
        release = (self.install_root / "current").resolve()
        manifest_path = release / ".manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.pop("installation_id_sha256")
        manifest_path.write_text(json.dumps(manifest))
        record_path = self.install_root / ".install.json"
        record = json.loads(record_path.read_text())
        record.pop("installation_id")
        record_path.write_text(json.dumps(record))
        previous_record = record_path.read_bytes()
        for action in ("status", "update"):
            with self.subTest(action=action):
                result = self.run_lifecycle(action)
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn("provenance is missing or invalid", result.stderr)
                self.assertEqual(record_path.read_bytes(), previous_record)
                self.assertEqual((self.install_root / "current").resolve(), release)
                self.assertEqual(
                    list((self.install_root / "releases").iterdir()), [release]
                )

    def test_failed_initial_install_rolls_back_all_created_files(self):
        original_atomic_write = lifecycle_files.atomic_write

        def fail_on_second_launcher(path, content, mode, **kwargs):
            if path == self.binary_directory / "docker":
                raise OSError("synthetic launcher write failure")
            return original_atomic_write(path, content, mode, **kwargs)

        with (
            contextlib.redirect_stdout(io.StringIO()),
            mock.patch.object(lifecycle_files, "atomic_write", fail_on_second_launcher),
        ):
            with self.assertRaisesRegex(OSError, "synthetic launcher write failure"):
                lifecycle_operations.install(
                    self.binary_directory,
                    self.install_root,
                    self.lifecycle_providers(),
                    False,
                    False,
                    delivery=self.delivery(),
                )

        self.assertFalse(self.binary_directory.exists())
        self.assertFalse(self.install_root.exists())

    def test_failed_update_restores_previous_release(self):
        providers = self.lifecycle_providers()
        with contextlib.redirect_stdout(io.StringIO()):
            lifecycle_operations.install(
                self.binary_directory,
                self.install_root,
                providers,
                False,
                False,
                delivery=self.delivery(),
            )
        previous_release = lifecycle_releases.current_release(self.install_root)
        next_providers = dict(providers)
        next_providers["python_version"] = f"{providers['python_version']}-next"
        original_atomic_json = lifecycle_files.atomic_json

        def fail_on_install_record(path, value):
            if path == self.install_root / ".install.json":
                raise OSError("synthetic metadata write failure")
            return original_atomic_json(path, value)

        with (
            contextlib.redirect_stdout(io.StringIO()),
            mock.patch.object(
                lifecycle_providers, "validate_providers", return_value=next_providers
            ),
            mock.patch.object(lifecycle_files, "atomic_json", fail_on_install_record),
        ):
            with self.assertRaisesRegex(OSError, "synthetic metadata write failure"):
                lifecycle_operations.update(
                    None, self.install_root, None, None, False, delivery=self.delivery()
                )

        self.assertEqual(
            lifecycle_releases.current_release(self.install_root), previous_release
        )
        self.assertEqual(
            {path for path in (self.install_root / "releases").iterdir()},
            {previous_release},
        )

    def test_existing_commands_require_explicit_backup_and_are_restored(self):
        self.binary_directory.mkdir()
        original = {}
        for index, entry_point in enumerate(ENTRY_POINTS):
            target = self.binary_directory / entry_point
            content = f"original {entry_point}\n".encode() + b"\x00\xff\x80"
            mode = 0o700 if index % 2 else 0o755
            target.write_bytes(content)
            target.chmod(mode)
            original[entry_point] = (content, mode)
        linked_entry = "docker"
        link_target = self.test_root / "original-docker"
        (self.binary_directory / linked_entry).replace(link_target)
        (self.binary_directory / linked_entry).symlink_to("../original-docker")

        def assert_original(directory):
            for entry_point, (content, mode) in original.items():
                target = directory / entry_point
                if entry_point == linked_entry:
                    self.assertTrue(target.is_symlink())
                    self.assertEqual(os.readlink(target), "../original-docker")
                    target = link_target
                self.assertEqual(target.read_bytes(), content)
                self.assertEqual(target.stat().st_mode & 0o777, mode)

        refused = self.install()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("--backup-existing", refused.stderr)
        assert_original(self.binary_directory)

        installed = self.install("--backup-existing")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        assert_original(self.install_root / "backups")
        removed = self.run_lifecycle("uninstall")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        assert_original(self.binary_directory)
        self.assertFalse(self.install_root.exists())

    def test_discovery_never_selects_a_command_that_install_will_replace(self):
        self.binary_directory.mkdir()
        old_podman = self.binary_directory / "podman"
        old_compose = self.binary_directory / "podman-compose"
        self.write_provider(old_podman, "podman")
        self.write_provider(old_compose, "compose")

        installed = self.run_lifecycle("install", "--backup-existing")

        self.assertEqual(installed.returncode, 0, installed.stderr)
        current = (self.install_root / "current").resolve()
        manifest = json.loads((current / ".manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["providers"]["podman"], str(self.podman))
        self.assertEqual(manifest["providers"]["compose"], str(self.compose))

        removed = self.run_lifecycle("uninstall")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertTrue(old_podman.is_file())
        self.assertTrue(old_compose.is_file())

    def test_uninstall_refuses_to_remove_a_modified_launcher(self):
        installed = self.install()
        self.assertEqual(installed.returncode, 0, installed.stderr)
        modified = self.binary_directory / "podman"
        modified.write_text("user modification\n", encoding="utf-8")

        removed = self.run_lifecycle("uninstall")

        self.assertNotEqual(removed.returncode, 0)
        self.assertIn("refuse to remove modified", removed.stdout)
        self.assertEqual(modified.read_text(encoding="utf-8"), "user modification\n")
        self.assertTrue((self.install_root / ".install.json").is_file())

    def test_uninstall_preflights_every_backup_before_removing_launchers(self):
        self.binary_directory.mkdir()
        for entry_point in ENTRY_POINTS:
            target = self.binary_directory / entry_point
            target.write_text(f"original {entry_point}\n", encoding="utf-8")
            target.chmod(0o755)
        installed = self.install("--backup-existing")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        managed_contents = {
            entry_point: (self.binary_directory / entry_point).read_bytes()
            for entry_point in ENTRY_POINTS
        }
        (self.install_root / "backups" / ENTRY_POINTS[-1]).write_text(
            "modified backup\n", encoding="utf-8"
        )

        removed = self.run_lifecycle("uninstall")

        self.assertNotEqual(removed.returncode, 0)
        self.assertIn("saved launcher backup is missing or modified", removed.stderr)
        for entry_point, content in managed_contents.items():
            self.assertEqual(
                (self.binary_directory / entry_point).read_bytes(), content
            )
        self.assertTrue((self.install_root / ".install.json").is_file())

    def test_unsupported_provider_version_fails_before_writing(self):
        self.write_provider(self.podman, "podman", version="7.0.0")

        result = self.install()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported Podman series", result.stderr)
        self.assertFalse(self.install_root.exists())

    def test_optional_devpod_in_bindir_is_wrapped_updated_and_restored(self):
        self.binary_directory.mkdir()
        devpod = self.binary_directory / "devpod"
        self.write_devpod(devpod)
        original = devpod.read_bytes()

        installed = self.install("--devpod", str(devpod), "--backup-existing")

        self.assertEqual(installed.returncode, 0, installed.stderr)
        for entry_point in ENTRY_POINTS + DEVPOD_ENTRY_POINTS:
            self.assertTrue((self.binary_directory / entry_point).is_file())
        manifest = json.loads(
            (self.install_root / "current/.manifest.json").read_text(encoding="utf-8")
        )
        saved_devpod = self.install_root / "backups/devpod"
        self.assertEqual(manifest["providers"]["devpod"], str(saved_devpod))

        version = subprocess.run(
            [str(devpod), "version"],
            env={**os.environ, "HOME": str(self.test_root / "user-home")},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertEqual(version.stdout.strip(), "v0.6.15")

        updated = self.run_lifecycle("update")
        self.assertEqual(updated.returncode, 0, updated.stderr)
        removed = self.run_lifecycle("uninstall")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual(devpod.read_bytes(), original)
        self.assertFalse((self.binary_directory / "paranoid-podman").exists())

    def test_optional_devpod_installs_and_updates_with_other_versions(self):
        devpod = self.provider_directory / "devpod"
        self.write_devpod(devpod, "0.6.16")

        result = self.install("--devpod", str(devpod))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("real DevPod:", result.stdout)
        self.assertIn("(0.6.16)", result.stdout)
        self.write_devpod(devpod, "1.0.0")
        updated = self.run_lifecycle("update")
        self.assertEqual(updated.returncode, 0, updated.stderr)
        manifest = json.loads(
            (self.install_root / "current/.manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["providers"]["devpod_version"], "1.0.0")
        version = subprocess.run(
            [str(self.binary_directory / "devpod"), "version"],
            env={**os.environ, "HOME": str(self.test_root / "user-home")},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertEqual(version.stdout.strip(), "v1.0.0")
        removed = self.run_lifecycle("uninstall")
        self.assertEqual(removed.returncode, 0, removed.stderr)

    def test_optional_devpod_still_rejects_unparseable_version_before_install(self):
        devpod = self.provider_directory / "devpod"
        self.write_devpod(devpod, "unknown")
        result = self.install("--devpod", str(devpod))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not parse DevPod version", result.stderr)
        self.assertFalse(self.install_root.exists())

    def test_devpod_reinstall_preserves_the_project_key_tree(self):
        devpod = self.provider_directory / "devpod"
        self.write_devpod(devpod)
        key_directory = (
            self.test_root
            / "user-home/.ssh/paranoid-podman/default--test/example-workspace--test"
        )
        key_directory.mkdir(parents=True, mode=0o700)
        private_key = key_directory / "id_ed25519"
        private_key.write_text("synthetic private key fixture\n", encoding="utf-8")
        private_key.chmod(0o600)

        installed = self.install("--devpod", str(devpod))
        self.assertEqual(installed.returncode, 0, installed.stderr)
        removed = self.run_lifecycle("uninstall")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual(
            private_key.read_text(encoding="utf-8"),
            "synthetic private key fixture\n",
        )

        reinstalled = self.install("--devpod", str(devpod))
        self.assertEqual(reinstalled.returncode, 0, reinstalled.stderr)
        self.assertTrue(private_key.is_file())

        removed_again = self.run_lifecycle("uninstall")
        self.assertEqual(removed_again.returncode, 0, removed_again.stderr)
        self.assertTrue(private_key.is_file())

    def test_uninstall_restores_external_devpod_in_managed_ssh_blocks(self):
        devpod = self.provider_directory / "devpod"
        self.write_devpod(devpod)
        installed = self.install("--devpod", str(devpod))
        self.assertEqual(installed.returncode, 0, installed.stderr)

        ssh_config = self.test_root / "user-home/.ssh/config"
        ssh_config.parent.mkdir(parents=True, mode=0o700)
        wrapper = self.binary_directory / "devpod"
        ssh_config.write_text(
            "# DevPod Start example-workspace.devpod\n"
            "Host example-workspace.devpod\n"
            "  ForwardAgent no\n"
            "  IdentityAgent none\n"
            f"  ProxyCommand {wrapper} ssh --stdio --context default "
            "--user vscode example-workspace\n"
            "# DevPod End example-workspace.devpod\n"
            "Host unrelated\n"
            f"  ProxyCommand {wrapper} custom\n",
            encoding="utf-8",
        )
        ssh_config.chmod(0o600)

        preview = self.run_lifecycle("uninstall", "--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertIn("restore 1 DevPod SSH ProxyCommand", preview.stdout)
        self.assertIn(str(wrapper), ssh_config.read_text(encoding="utf-8"))

        removed = self.run_lifecycle("uninstall")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        content = ssh_config.read_text(encoding="utf-8")
        self.assertIn(f"ProxyCommand {devpod} ssh --stdio", content)
        self.assertNotIn("IdentityAgent", content)
        self.assertIn(f"ProxyCommand {wrapper} custom", content)

    def test_uninstall_discovers_custom_context_ssh_config(self):
        devpod = self.provider_directory / "devpod"
        self.write_devpod(devpod)
        installed = self.install("--devpod", str(devpod))
        self.assertEqual(installed.returncode, 0, installed.stderr)

        ssh_config = self.test_root / "custom/ssh/config"
        ssh_config.parent.mkdir(parents=True, mode=0o700)
        wrapper = self.binary_directory / "devpod"
        ssh_config.write_text(
            "# DevPod Start example-workspace.devpod\n"
            "Host example-workspace.devpod\n"
            "  ForwardAgent yes\n"
            "  IdentityAgent /run/user/1000/paranoid-podman/ssh-agent/test.sock\n"
            f"  ProxyCommand {wrapper} ssh --stdio --context default "
            "--user vscode example-workspace\n"
            "# DevPod End example-workspace.devpod\n",
            encoding="utf-8",
        )
        ssh_config.chmod(0o600)

        with mock.patch.dict(
            os.environ,
            {"PARANOID_LIFECYCLE_TEST_SSH_CONFIG": str(ssh_config)},
            clear=False,
        ):
            removed = self.run_lifecycle("uninstall")

        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertIn(
            f"ProxyCommand {devpod} ssh --stdio",
            ssh_config.read_text(encoding="utf-8"),
        )
        self.assertNotIn("IdentityAgent", ssh_config.read_text(encoding="utf-8"))

    def test_uninstall_refuses_an_independently_changed_identity_agent(self):
        devpod = self.provider_directory / "devpod"
        self.write_devpod(devpod)
        installed = self.install("--devpod", str(devpod))
        self.assertEqual(installed.returncode, 0, installed.stderr)

        ssh_config = self.test_root / "user-home/.ssh/config"
        ssh_config.parent.mkdir(parents=True, mode=0o700)
        wrapper = self.binary_directory / "devpod"
        ssh_config.write_text(
            "# DevPod Start example-workspace.devpod\n"
            "Host example-workspace.devpod\n"
            "  ForwardAgent yes\n"
            "  IdentityAgent /tmp/user-selected-agent.sock\n"
            f"  ProxyCommand {wrapper} ssh --stdio --context default "
            "--user vscode example-workspace\n"
            "# DevPod End example-workspace.devpod\n",
            encoding="utf-8",
        )
        ssh_config.chmod(0o600)

        removed = self.run_lifecycle("uninstall")

        self.assertNotEqual(removed.returncode, 0)
        self.assertIn("independently changed", removed.stderr)
        self.assertTrue(wrapper.exists())

    def test_tampered_metadata_cannot_redirect_lifecycle_to_a_system_prefix(self):
        installed = self.install()
        self.assertEqual(installed.returncode, 0, installed.stderr)
        record_path = self.install_root / ".install.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["binary_directory"] = "/usr/local/bin"
        record_path.write_text(json.dumps(record), encoding="utf-8")

        status = self.run_lifecycle("status")

        self.assertNotEqual(status.returncode, 0)
        self.assertIn("user-owned, not system prefixes", status.stderr)
        for entry_point in ENTRY_POINTS:
            self.assertTrue((self.binary_directory / entry_point).is_file())

    def test_manifest_does_not_allow_release_path_traversal(self):
        installed = self.install()
        self.assertEqual(installed.returncode, 0, installed.stderr)
        current = (self.install_root / "current").resolve()
        manifest_path = current / ".manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"]["../../outside"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        removed = self.run_lifecycle("uninstall")

        self.assertNotEqual(removed.returncode, 0)
        self.assertIn("unsafe release file entry", removed.stderr)
        self.assertFalse((self.test_root / "outside").exists())
        for entry_point in ENTRY_POINTS:
            self.assertTrue((self.binary_directory / entry_point).is_file())


class LifecycleUnitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.test_root = Path(temporary.name)
        self.binary_directory = self.test_root / "bin"
        self.install_root = self.test_root / "installation"
        self.podman = self.test_root / "unused-podman"
        self.compose = self.test_root / "unused-compose"

    def test_partial_wheel_inputs_are_rejected_before_probing(self):
        with mock.patch.object(
            lifecycle_providers,
            "validate_providers",
            side_effect=AssertionError("unexpected provider probe"),
        ):
            for action in ("install", "update"):
                for extra in (["--wheel", "unavailable.whl"],):
                    with self.subTest(action=action, extra=extra):
                        with self.assertRaisesRegex(
                            lifecycle_errors.LifecycleError, "require --wheel"
                        ):
                            lifecycle_cli.main(
                                [
                                    action,
                                    "--bindir",
                                    str(self.binary_directory),
                                    "--libdir",
                                    str(self.install_root),
                                    "--dry-run",
                                    *extra,
                                ]
                            )
        self.assertFalse(self.binary_directory.exists())
        self.assertFalse(self.install_root.exists())

    def test_new_launcher_creation_never_replaces_an_existing_file(self):
        target = self.test_root / "existing-command"
        target.write_bytes(b"user content")
        with self.assertRaises(FileExistsError):
            lifecycle_files.atomic_write(target, b"replacement", 0o755, replace=False)
        self.assertEqual(target.read_bytes(), b"user content")
        self.assertEqual(list(self.test_root.glob(".existing-command.*")), [])

    def test_compose_version_parser_ignores_podman_version_output(self):

        version = lifecycle_providers.parse_version(
            "podman version 6.1.0\npodman-compose version 1.6.0\n",
            "podman-compose",
        )

        self.assertEqual(version, (1, 6, 0))

    def test_system_installation_prefix_is_always_rejected(self):
        environment = os.environ.copy()
        environment["HOME"] = str(self.test_root / "user-home")
        result = subprocess.run(
            [
                sys.executable,
                str(LIFECYCLE),
                "install",
                "--bindir",
                "/usr/local/bin",
                "--libdir",
                str(self.install_root),
                "--podman",
                str(self.podman),
                "--compose-provider",
                str(self.compose),
                "--dry-run",
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("user-owned, not system prefixes", result.stderr)
        self.assertFalse(self.install_root.exists())

    def test_every_lifecycle_action_refuses_root_execution(self):
        with mock.patch.object(os, "geteuid", return_value=0):
            with self.assertRaisesRegex(
                lifecycle_errors.LifecycleError, "refusing lifecycle actions as root"
            ):
                lifecycle_cli.main(
                    [
                        "install",
                        "--bindir",
                        str(self.binary_directory),
                        "--libdir",
                        str(self.install_root),
                        "--dry-run",
                    ]
                )

    def test_installation_paths_cannot_overlap_source_or_each_other(self):
        cases = (
            (PROJECT_ROOT / "bin", self.install_root),
            (self.binary_directory, self.binary_directory / "payload"),
            (self.install_root / "bin", self.install_root),
        )

        for binary_directory, install_root in cases:
            with self.subTest(
                binary_directory=binary_directory, install_root=install_root
            ):
                with self.assertRaises(lifecycle_errors.LifecycleError):
                    lifecycle_paths.validate_install_paths(
                        binary_directory, install_root
                    )


if __name__ == "__main__":
    unittest.main()
