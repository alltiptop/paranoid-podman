"""Installer menus require an explicit terminal choice before changing files."""

import contextlib
import io
import os
import unittest
from unittest import mock

from paranoid_podman.lifecycle import arguments, bootstrap, cli
from paranoid_podman.lifecycle.errors import LifecycleError
from tests.support.lifecycle import LifecycleFixture


class InstallerMenuTests(LifecycleFixture):
    def setUp(self):
        super().setUp()
        environment = {
            "HOME": str(self.test_root / "home"),
            "XDG_BIN_HOME": str(self.binary_directory),
            "XDG_DATA_HOME": str(self.install_root.parent),
            "PATH": f"{self.provider_directory}{os.pathsep}{os.defpath}",
        }
        contexts = contextlib.ExitStack()
        self.addCleanup(contexts.close)
        contexts.enter_context(mock.patch.dict(os.environ, environment, clear=True))
        contexts.enter_context(
            mock.patch.object(
                bootstrap,
                "prepare",
                side_effect=AssertionError("menu must not prepare installation tools"),
            )
        )

    def run_menu(self, selection, *options, interactive=True, uninstall_only=False):
        stdin = io.StringIO(selection)
        with (
            mock.patch("sys.stdin", stdin),
            mock.patch.object(stdin, "isatty", return_value=interactive),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(cli.main(list(options), uninstall_only=uninstall_only), 0)
        if not interactive:
            self.assertEqual(stdin.tell(), 0, "non-terminal input was consumed")
        return output.getvalue()

    def test_no_action_with_piped_input_only_reports_status_and_actions(self):
        output = self.run_menu("1\n", interactive=False)
        self.assertIn("not installed", output)
        self.assertIn("1. install", output)
        self.assertNotIn("1. update", output)
        self.assertIn("no interactive terminal", output)
        self.assertFalse(self.binary_directory.exists())
        self.assertFalse(self.install_root.exists())

    def test_empty_input_eof_and_exit_cancel_without_changes(self):
        for selection in ("\n", "", "0\n", "q\n", "exit\n"):
            with self.subTest(selection=selection):
                output = self.run_menu(selection)
                self.assertIn("cancelled; no changes made", output)
        self.assertFalse(self.binary_directory.exists())
        self.assertFalse(self.install_root.exists())

    def test_keyboard_interrupt_cancels_without_a_traceback(self):
        with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            output = self.run_menu("")
        self.assertIn("cancelled; no changes made", output)
        self.assertFalse(self.install_root.exists())

    def test_invalid_choice_reprompts_and_install_preview_keeps_files_unchanged(self):
        for selection in ("delete\n9\n1\n", "install\n"):
            with self.subTest(selection=selection):
                output = self.run_menu(selection, "--dry-run", "--without-devpod")
                self.assertIn("no downloads or files changed", output)
                if "9" in selection:
                    self.assertIn("choose one of the listed actions", output)
        self.assertFalse(self.binary_directory.exists())
        self.assertFalse(self.install_root.exists())

    def test_damaged_installation_is_reported_before_offering_actions(self):
        self.install_root.mkdir(parents=True)
        marker = self.install_root / "current"
        marker.symlink_to("missing-release")
        with self.assertRaisesRegex(LifecycleError, "no installation found"):
            self.run_menu("1\n")
        self.assertTrue(marker.is_symlink())
        self.assertFalse(self.binary_directory.exists())

    def test_custom_paths_override_menu_defaults(self):
        custom_bin = self.test_root / "custom-bin"
        custom_root = self.test_root / "custom-app"
        output = self.run_menu(
            "0\n", "--bindir", str(custom_bin), "--libdir", str(custom_root)
        )
        self.assertIn(f"binary directory: {custom_bin}", output)
        self.assertIn(f"installation root: {custom_root}", output)
        self.assertFalse(custom_bin.exists())
        self.assertFalse(custom_root.exists())

    def test_delete_is_rejected_by_the_parser(self):
        with (
            contextlib.redirect_stderr(io.StringIO()) as error,
            self.assertRaises(SystemExit) as caught,
        ):
            cli.main(["delete"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("invalid choice", error.getvalue())
        self.assertFalse(self.install_root.exists())

    def test_help_describes_actions_and_option_scopes(self):
        with (
            contextlib.redirect_stdout(io.StringIO()) as output,
            self.assertRaises(SystemExit) as caught,
        ):
            arguments.parse_arguments(["--help"])
        self.assertEqual(caught.exception.code, 0)
        help_text = output.getvalue()
        for text in (
            "General options",
            "Install / update options",
            "install only:",
            "Uninstall options",
            "Supply all four together",
            "Remove managed files and restore",
        ):
            self.assertIn(text, help_text)
        self.assertNotIn("delete", help_text)

    def test_uninstall_shortcut_without_installation_only_offers_exit(self):
        for interactive in (True, False):
            with self.subTest(interactive=interactive):
                output = self.run_menu(
                    "1\n0\n", interactive=interactive, uninstall_only=True
                )
                self.assertIn("not installed", output)
                self.assertIn("0. Exit", output)
                self.assertNotIn("1. install", output)
                self.assertNotIn("1. update", output)
                self.assertNotIn("1. uninstall", output)
        self.assertFalse(self.binary_directory.exists())
        self.assertFalse(self.install_root.exists())

    def test_uninstall_shortcut_rejects_actions_and_setup_options(self):
        for options in (
            ["install"],
            ["update"],
            ["delete"],
            ["--podman", "/unused/podman"],
            ["--wheel", "unused.whl"],
            ["--backup-existing"],
        ):
            with (
                self.subTest(options=options),
                contextlib.redirect_stderr(io.StringIO()) as error,
                self.assertRaises(SystemExit) as caught,
            ):
                cli.main(options, uninstall_only=True)
            self.assertEqual(caught.exception.code, 2)
            self.assertIn("unrecognized arguments", error.getvalue())
        self.assertFalse(self.install_root.exists())

    def test_uninstall_help_lists_only_removal_options(self):
        with (
            contextlib.redirect_stdout(io.StringIO()) as output,
            self.assertRaises(SystemExit) as caught,
        ):
            arguments.parse_arguments(["--help"], uninstall_only=True)
        self.assertEqual(caught.exception.code, 0)
        help_text = output.getvalue()
        self.assertIn("usage: uninstall.sh", help_text)
        self.assertIn("uninstall / Exit menu", help_text)
        for option in ("--bindir", "--libdir", "--dry-run", "--devpod-ssh-config"):
            self.assertIn(option, help_text)
        for option in ("--wheel", "--podman", "--backup-existing"):
            self.assertNotIn(option, help_text)


if __name__ == "__main__":
    unittest.main()
