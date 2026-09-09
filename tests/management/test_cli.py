import io
from pathlib import Path
from unittest import mock

from paranoid_podman.devpod import errors as devpod_errors
from paranoid_podman.devpod import modes as devpod_modes
from paranoid_podman.devpod import paths as devpod_paths
from paranoid_podman.devpod import terminal as devpod_terminal
from paranoid_podman.management import build_context as management_build_context
from paranoid_podman.management import cli as management_cli
from paranoid_podman.management import devpod as management_devpod
from tests.support import devpod as support

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ManagementTests(support.DevPodFixture):
    def test_management_cli_does_not_force_the_default_context(self):
        options = management_cli.parse_management_arguments(
            ["devpod", "audit", "example-workspace"]
        )

        self.assertIsNone(options.context)
        workspace = management_devpod.management_workspace(options)
        self.assertEqual(workspace.context, "default")

    def test_ide_only_setup_does_not_claim_to_save_a_missing_ssh_block(self):
        workspace = self.workspace()
        with (
            mock.patch.object(
                devpod_terminal, "interactive_stream", return_value=io.StringIO("2\n")
            ),
            mock.patch.object(devpod_modes, "prepare_mode", return_value=None),
            mock.patch.object(
                devpod_paths, "wrapper_path", return_value=self.root / "wrapper"
            ),
            self.assertRaisesRegex(
                devpod_errors.DevPodGuardError, "IDE-only choice was not saved"
            ),
        ):
            management_devpod.configure_workspace(self.root / "real-devpod", workspace)

        self.assertFalse(workspace.ssh_config.exists())
        self.assertIsNone(devpod_modes.infer_mode(workspace))

    def test_closed_ignore_selection_does_not_edit_build_inputs(self):
        context = self.root / "project"
        context.mkdir()
        (context / ".env.local").write_text("VALUE=synthetic\n", encoding="utf-8")
        input_stream = io.StringIO("")
        with mock.patch.object(input_stream, "isatty", return_value=True):
            with self.assertRaisesRegex(
                devpod_errors.DevPodGuardError, "input was closed"
            ):
                management_build_context.protect_build_context(
                    context, protect_all=False, input_stream=input_stream
                )

        self.assertFalse((context / ".containerignore").exists())

    def test_build_context_protection_updates_the_active_ignore_file(self):
        context = self.root / "project"
        context.mkdir()
        (context / ".env.local").write_text("VALUE=hidden\n", encoding="utf-8")
        (context / ".ssh").mkdir()
        (context / ".git").mkdir()
        dockerignore = context / ".dockerignore"
        dockerignore.write_text("node_modules\n", encoding="utf-8")

        result = management_build_context.protect_build_context(
            context, protect_all=True
        )

        self.assertEqual(result, 0)
        content = dockerignore.read_text(encoding="utf-8")
        self.assertIn(".env.local\n", content)
        self.assertIn(".ssh\n", content)
        self.assertNotIn(".git\n", content)
        active, findings = management_build_context.build_context_findings(context)
        self.assertEqual(active, dockerignore)
        self.assertEqual(findings, [])
        self.assertFalse((context / ".containerignore").exists())

    def test_build_context_selection_has_no_policy_bypass(self):
        findings = [".env", ".ssh", ".npmrc"]

        self.assertEqual(
            management_build_context.selected_findings(findings, "1, 3"),
            [".env", ".npmrc"],
        )
        with self.assertRaisesRegex(
            devpod_errors.DevPodGuardError, "selection must contain"
        ):
            management_build_context.selected_findings(findings, "ignore")

    def test_build_context_protect_closes_descendant_exceptions(self):
        context = self.root / "project"
        context.mkdir()
        (context / ".ssh").mkdir()
        ignore = context / ".containerignore"
        ignore.write_text("*\n!.ssh/project-key\n", encoding="utf-8")
        self.assertEqual(
            management_build_context.build_context_findings(context)[1], [".ssh"]
        )
        self.assertEqual(
            management_build_context.protect_build_context(context, protect_all=True), 0
        )
        self.assertEqual(
            management_build_context.build_context_findings(context)[1], []
        )
        self.assertTrue(ignore.read_text(encoding="utf-8").endswith(".ssh\n"))
