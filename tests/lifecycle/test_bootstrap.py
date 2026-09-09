"""Automatic setup keeps dry runs inert and discovers only external providers."""

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from paranoid_podman.lifecycle import bootstrap, cli, launchers, operations, providers
from paranoid_podman.lifecycle.errors import LifecycleError
from tests.support.lifecycle import LifecycleFixture

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bindir = self.root / "bin"
        self.install_root = self.root / "app"

    def test_default_install_dry_run_does_not_prepare_tools_or_create_files(self):
        with (
            mock.patch.dict(os.environ, {"HOME": str(self.root)}, clear=True),
            mock.patch.object(providers, "discover_devpod", return_value=None),
            mock.patch.object(providers, "validate_providers", return_value={}),
            mock.patch.object(bootstrap, "prepare") as prepare,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(cli.main(["install", "--dry-run"]), 0)
        prepare.assert_not_called()
        self.assertIn("no downloads or files changed", output.getvalue())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_failed_preparation_cleans_temporary_tools_before_installation(self):
        temporary_paths = []

        def fail_preparation(_source, scratch):
            temporary_paths.append(scratch)
            (scratch / "partial-download").write_text("unfinished")
            raise LifecycleError("synthetic download failure")

        with (
            mock.patch.object(bootstrap, "prepare", side_effect=fail_preparation),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(LifecycleError, "synthetic download failure"),
        ):
            operations.install(self.bindir, self.install_root, {}, False, False)
        self.assertTrue(temporary_paths)
        self.assertTrue(all(not path.exists() for path in temporary_paths))
        self.assertFalse(self.bindir.exists())
        self.assertFalse(self.install_root.exists())

    def test_declining_backup_keeps_the_existing_command_and_skips_preparation(self):
        self.bindir.mkdir()
        command = self.bindir / "podman"
        command.write_text("existing command")
        with (
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", return_value="n"),
            mock.patch.object(bootstrap, "prepare") as prepare,
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(LifecycleError, "cancelled"),
        ):
            operations.install(self.bindir, self.install_root, {}, False, False)
        prepare.assert_not_called()
        self.assertEqual(command.read_text(), "existing command")

    def test_discovery_supports_devpod_cli_and_user_local_devpod(self):
        external = self.root / "providers"
        external.mkdir()
        self.bindir.mkdir()
        alternate = external / "devpod-cli"
        alternate.write_text("test executable")
        alternate.chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": f"{self.bindir}:{external}"}):
            self.assertEqual(
                providers.discover_devpod(self.install_root, self.bindir),
                str(alternate),
            )
            local = self.bindir / "devpod"
            local.write_text("test executable")
            local.chmod(0o755)
            self.assertEqual(
                providers.discover_devpod(self.install_root, self.bindir), str(local)
            )
            local.unlink()
            self.install_root.mkdir()
            installed = self.install_root / "launch"
            installed.write_text("installed wrapper")
            installed.chmod(0o755)
            local.symlink_to(installed)
            self.assertEqual(
                providers.discover_devpod(self.install_root, self.bindir),
                str(alternate),
            )

    def test_command_changed_while_preparing_is_not_replaced(self):
        self.bindir.mkdir()
        command = self.bindir / "podman"
        command.write_text("original command")

        def prepare_release(*_arguments):
            command.write_text("changed while preparing")
            return self.install_root / "release"

        with (
            mock.patch.object(
                operations.lifecycle_releases,
                "create_release",
                side_effect=prepare_release,
            ),
            mock.patch.object(operations.lifecycle_releases, "switch_current"),
            mock.patch.object(operations.lifecycle_releases, "remove_release"),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(
                LifecycleError, "command changed during installation"
            ),
        ):
            operations.install(
                self.bindir, self.install_root, {}, False, True, delivery=mock.Mock()
            )
        self.assertEqual(command.read_text(), "changed while preparing")
        self.assertFalse((self.install_root / "backups/podman").exists())

    def test_preparation_ignores_ambient_python_and_package_index_configuration(self):
        result = subprocess.CompletedProcess([], 0, "", "")
        with (
            mock.patch.dict(
                os.environ,
                {
                    "PYTHONPATH": "/untrusted",
                    "PIP_INDEX_URL": "https://example.invalid/untrusted",
                    "PIP_NO_INDEX": "1",
                    "PIP_FIND_LINKS": str(self.root),
                },
            ),
            mock.patch.object(bootstrap.subprocess, "run", return_value=result) as run,
        ):
            bootstrap.run(
                Path("/usr/bin/python3"), ["-m", "pip", "download"], self.root
            )
        environment = run.call_args.kwargs["env"]
        self.assertNotIn("PYTHONPATH", environment)
        self.assertNotIn("PIP_INDEX_URL", environment)
        self.assertEqual(environment["PIP_CONFIG_FILE"], os.devnull)
        self.assertEqual(environment["PIP_NO_INDEX"], "1")
        self.assertEqual(environment["PIP_FIND_LINKS"], str(self.root))

    def test_installed_runtime_does_not_search_cwd_for_build_sources(self):
        with (
            mock.patch.object(bootstrap, "source_root", return_value=None),
            self.assertRaisesRegex(LifecycleError, "from the source checkout"),
            bootstrap.installation_delivery(None, False),
        ):
            self.fail("unexpected preparation")


class AutomaticInstallTests(LifecycleFixture):
    """Real source builds and pip installs, with offline wheels and fake engines."""

    wheelhouse = None

    @classmethod
    def setUpClass(cls):
        if cls.wheelhouse is None:
            raise unittest.SkipTest(
                "select the automatic-install gate with --wheelhouse"
            )

    def test_plain_install_update_and_uninstall_with_automatic_devpod_discovery(self):
        self.exercise_install(devpod=True)

    def test_plain_install_without_devpod(self):
        self.exercise_install(devpod=False)

    def test_plain_install_skips_another_installation_on_path(self):
        self.exercise_install(devpod=True, foreign_wrappers=True)

    def test_terminal_menu_installs_updates_and_uninstalls(self):
        self.exercise_install(devpod=False, menu_actions=True)

    def test_uninstall_shortcut_previews_and_removes_through_terminal_menu(self):
        self.exercise_install(devpod=True, removal_shortcut=True)

    def exercise_install(
        self,
        *,
        devpod,
        foreign_wrappers=False,
        menu_actions=False,
        removal_shortcut=False,
    ):
        base_python = Path(sys._base_executable).resolve()
        for name, target in (
            ("python3", base_python),
            ("bash", Path(shutil.which("bash"))),
            ("dirname", Path(shutil.which("dirname"))),
        ):
            (self.provider_directory / name).symlink_to(target)
        if devpod:
            self.write_devpod(self.provider_directory / "devpod-cli")
        for provider in self.provider_directory.iterdir():
            if not provider.is_symlink():
                provider.write_text(
                    provider.read_text().replace(
                        "#!/usr/bin/env python3", f"#!{base_python} -I", 1
                    )
                )
        home = self.test_root / "user-home"
        home.mkdir()
        scratch = self.test_root / "scratch"
        scratch.mkdir()
        environment = {
            "HOME": str(home),
            "PATH": str(self.provider_directory),
            "TMPDIR": str(scratch),
            "LANG": "C.UTF-8",
            "PIP_NO_INDEX": "1",
            "PIP_FIND_LINKS": str(self.wheelhouse),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PARANOID_LIFECYCLE_TEST_OUTPUT": str(self.provider_output),
        }
        if foreign_wrappers:
            old_bin = self.test_root / "old-bin"
            old_bin.mkdir()
            for entry in ("podman", "podman-compose", "devpod"):
                wrapper = old_bin / entry
                wrapper.write_bytes(
                    launchers.command_launcher(self.test_root / "old-install", entry)
                )
                wrapper.chmod(0o755)
            environment["PATH"] = f"{old_bin}:{environment['PATH']}"
        installed_bin = home / ".local/bin"
        installation = home / ".local/share/paranoid-podman"

        def command(*arguments, choice=None, script="install.sh"):
            terminal = os.openpty() if choice is not None else None
            try:
                if terminal is not None:
                    os.write(terminal[0], f"{choice}\n".encode())
                return subprocess.run(
                    [str(PROJECT_ROOT / script), *arguments],
                    cwd=self.test_root,
                    env=environment,
                    stdin=terminal[1] if terminal else subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=180,
                )
            finally:
                if terminal is not None:
                    for descriptor in terminal:
                        os.close(descriptor)

        result = command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not installed", result.stdout)
        self.assertIn("1. install", result.stdout)
        self.assertNotIn("2. uninstall", result.stdout)
        self.assertEqual(list(home.iterdir()), [])
        self.assertEqual(list(scratch.iterdir()), [])
        result = command(script="uninstall.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not installed", result.stdout)
        self.assertIn("0. Exit", result.stdout)
        self.assertNotIn("1. install", result.stdout)
        self.assertEqual(list(home.iterdir()), [])
        self.assertEqual(list(scratch.iterdir()), [])
        result = command("install", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(home.iterdir()), [])
        self.assertEqual(list(scratch.iterdir()), [])
        result = command(choice="1") if menu_actions else command("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(scratch.iterdir()), [])
        release = (installation / "current").resolve()
        manifest = json.loads((release / ".manifest.json").read_text())
        self.assertEqual("devpod" in manifest["providers"], devpod)
        self.assertEqual((installed_bin / "devpod").exists(), devpod)
        record = (installation / ".install.json").read_bytes()
        for choice in (None, "0"):
            result = command(choice=choice)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("installation files are intact", result.stdout)
            self.assertIn("1. update", result.stdout)
            self.assertIn("2. uninstall", result.stdout)
            self.assertEqual((installation / ".install.json").read_bytes(), record)
            self.assertEqual((installation / "current").resolve(), release)
        for choice in (None, "", "0"):
            result = command(choice=choice, script="uninstall.sh")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("installation files are intact", result.stdout)
            self.assertIn("1. uninstall", result.stdout)
            self.assertNotIn("1. update", result.stdout)
            self.assertEqual((installation / ".install.json").read_bytes(), record)
            self.assertEqual((installation / "current").resolve(), release)
        result = command("--dry-run", choice="1", script="uninstall.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((installation / ".install.json").read_bytes(), record)
        self.assertEqual((installation / "current").resolve(), release)
        for action in ("status", "update", "status"):
            result = (
                command(choice="1")
                if menu_actions and action == "update"
                else command(action)
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((installation / "current").resolve(), release)
        self.assertEqual(list(scratch.iterdir()), [])
        for arguments, expected in (
            (["run", "local-test-image"], 0),
            (["run", "--privileged", "local-test-image"], 125),
        ):
            self.provider_output.unlink(missing_ok=True)
            result = subprocess.run(
                [str(installed_bin / "podman"), *arguments],
                cwd=self.test_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            self.assertEqual(result.returncode, expected, result.stderr)
            if expected == 0:
                self.assertIn(
                    "--security-opt=no-new-privileges",
                    json.loads(self.provider_output.read_text()),
                )
            else:
                self.assertFalse(self.provider_output.exists())
        if removal_shortcut:
            result = command(choice="1", script="uninstall.sh")
        else:
            result = command(choice="2") if menu_actions else command("uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(installation.exists())
        self.assertEqual(list(installed_bin.iterdir()), [])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=AutomaticInstallTests.__doc__)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    AutomaticInstallTests.wheelhouse = parser.parse_args().wheelhouse.resolve()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(AutomaticInstallTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
