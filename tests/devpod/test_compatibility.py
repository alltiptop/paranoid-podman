import io
import json
import os
import shutil
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from paranoid_podman.devpod import agent as devpod_agent
from paranoid_podman.devpod import arguments as devpod_arguments
from paranoid_podman.devpod import cli as devpod_cli
from paranoid_podman.devpod import context as devpod_context
from paranoid_podman.devpod import errors as devpod_errors
from paranoid_podman.devpod import keys as devpod_keys
from paranoid_podman.devpod import locks as devpod_locks
from paranoid_podman.devpod import models as devpod_models
from paranoid_podman.devpod import modes as devpod_modes
from paranoid_podman.devpod import paths as devpod_paths
from paranoid_podman.devpod import process as devpod_process
from paranoid_podman.devpod import provider as devpod_provider
from paranoid_podman.devpod import session as devpod_session
from paranoid_podman.devpod import ssh_config as devpod_ssh_config
from paranoid_podman.devpod import terminal as devpod_terminal
from tests.support import devpod as support

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class DevPodCompatibilityTests(support.DevPodFixture):
    def test_workspace_parser_preserves_global_flags_and_uses_explicit_id(self):
        workspace = devpod_arguments.workspace_from_arguments(
            [
                "--context",
                "local",
                "up",
                "--recreate",
                "--id",
                "example-workspace",
                ".",
                "--open-ide=false",
            ],
            2,
        )

        self.assertEqual(workspace.context, "local")
        self.assertEqual(workspace.name, "example-workspace")
        self.assertEqual(workspace.ssh_config, self.home / ".ssh/config")

    def test_global_option_value_named_ssh_is_not_treated_as_the_command(self):
        command, index = devpod_arguments.devpod_subcommand(
            ["--provider", "ssh", "up", "example-workspace"]
        )

        self.assertEqual((command, index), ("up", 2))

    def test_boolean_flags_follow_devpod_pflag_syntax(self):
        cases = (
            (["--stdio", "false"], True),
            (["--stdio=false"], False),
            (["--stdio=True"], True),
            (["--stdio=T"], True),
            (["--stdio=F"], False),
            (["--stdio", "--stdio=false"], False),
            (["--stdio", "--", "--stdio=false"], True),
            (["--command", "--stdio", "example-workspace"], False),
        )
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                self.assertEqual(
                    devpod_arguments.boolean_option_value(
                        arguments, "--stdio", default=False
                    ),
                    expected,
                )
        for value in ("yes", "no", "tRuE", ""):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    devpod_errors.DevPodGuardError, "requires a boolean value"
                ):
                    devpod_arguments.boolean_option_value(
                        [f"--stdio={value}"], "--stdio", default=False
                    )

    def test_forwarding_cannot_be_disabled_by_an_operand_or_command_value(self):
        for arguments in (
            ["up", "--gpg-agent-forwarding", "false"],
            ["up", "--gpg-agent-forwarding", "--", "--gpg-agent-forwarding=false"],
            [
                "ssh",
                "example-workspace",
                "--gpg-agent-forwarding",
                "--command",
                "--gpg-agent-forwarding=false",
            ],
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(
                    devpod_errors.DevPodGuardError, "GPG-agent forwarding is blocked"
                ):
                    devpod_arguments.reject_unreviewed_credential_forwarding(arguments)

    def test_boolean_rewrite_preserves_values_and_the_argument_terminator(self):
        arguments = [
            "up",
            "--open-ide",
            "false",
            "--ide-option",
            "--open-ide",
            "--",
            "--open-ide=true",
        ]
        self.assertEqual(
            devpod_arguments.replace_boolean_option(
                arguments, "--open-ide", value=False
            ),
            [
                "up",
                "false",
                "--ide-option",
                "--open-ide",
                "--open-ide=false",
                "--",
                "--open-ide=true",
            ],
        )
        self.assertEqual(
            devpod_arguments.append_devpod_options(
                ["ssh", "--command", "--", "--", "example-workspace"], "--context=work"
            ),
            ["ssh", "--command", "--", "--context=work", "--", "example-workspace"],
        )

    def test_workspace_and_context_respect_the_argument_terminator(self):
        arguments = [
            "ssh",
            "--context=work",
            "--",
            "example-workspace",
            "--context=other",
        ]
        workspace = devpod_arguments.workspace_from_arguments(arguments, 0)

        self.assertEqual(workspace.name, "example-workspace")
        self.assertEqual(workspace.context, "work")
        self.assertIsNone(
            devpod_arguments.option_value(
                ["ssh", "--command", "--context=other"], "--context"
            )
        )

    def test_remote_command_named_help_does_not_bypass_interception(self):
        workspace = self.workspace()
        arguments = [
            "ssh",
            "example-workspace",
            "--context=default",
            "--ssh-config",
            str(workspace.ssh_config),
            "--command",
            "--help",
        ]
        with (
            mock.patch.object(
                devpod_provider,
                "discover_real_devpod",
                return_value=self.root / "real-devpod",
            ),
            mock.patch.object(devpod_provider, "validate_devpod_version"),
            mock.patch.object(
                devpod_session, "run_intercepted_devpod", return_value=0
            ) as intercepted,
            mock.patch.object(os, "execve") as passthrough,
        ):
            self.assertEqual(devpod_cli.run_devpod(arguments), 0)

        passthrough.assert_not_called()
        intercepted.assert_called_once()

    def test_workspace_parser_handles_generated_proxy_command_shape(self):
        arguments = [
            "ssh",
            "--stdio",
            "--context",
            "default",
            "--user",
            "vscode",
            "example-workspace",
        ]

        workspace = devpod_arguments.workspace_from_arguments(arguments, 0)

        self.assertEqual(workspace.name, "example-workspace")
        self.assertEqual(workspace.context, "default")

    def test_workspace_parser_skips_short_port_forward_values(self):
        arguments = [
            "ssh",
            "-L",
            "127.0.0.1:8080:127.0.0.1:80",
            "-R",
            "127.0.0.1:9000:127.0.0.1:9000",
            "example-workspace",
        ]

        workspace = devpod_arguments.workspace_from_arguments(arguments, 0)

        self.assertEqual(workspace.name, "example-workspace")

    def test_selected_devpod_context_is_resolved_when_no_flag_was_given(self):
        workspace = self.workspace()
        response = type(
            "Result",
            (),
            {
                "stdout": json.dumps(
                    [
                        {"name": "default", "default": False},
                        {"name": "work", "default": True},
                    ]
                ),
                "returncode": 0,
            },
        )()

        with mock.patch.object(devpod_process, "run_checked", return_value=response):
            resolved = devpod_context.resolve_selected_context(
                self.root / "devpod", workspace
            )

        self.assertEqual(resolved.context, "work")
        self.assertEqual(resolved.name, workspace.name)

    def test_only_the_marked_devpod_block_is_rewritten_for_ide_only_mode(self):
        workspace = self.workspace()
        workspace.ssh_config.parent.mkdir(mode=0o700)
        workspace.ssh_config.write_text(
            "Include ~/.ssh/conf.d/*\n"
            + self.devpod_block(workspace.name)
            + "Host private\n  ForwardAgent yes\n",
            encoding="utf-8",
        )
        workspace.ssh_config.chmod(0o600)
        wrapper = self.executable("devpod-wrapper", "#!/bin/sh\nexit 0\n")

        found = devpod_ssh_config.patch_ssh_block(
            workspace, devpod_models.SSHMode.IDE_ONLY, wrapper
        )

        self.assertTrue(found)
        content = workspace.ssh_config.read_text(encoding="utf-8")
        self.assertIn('  ForwardAgent no\n  IdentityAgent "none"\n', content)
        self.assertIn(f"ProxyCommand {wrapper} ssh --stdio", content)
        self.assertIn("Host private\n  ForwardAgent yes\n", content)
        self.assertEqual(
            devpod_modes.infer_mode(workspace), devpod_models.SSHMode.IDE_ONLY
        )

    def test_project_mode_names_exactly_one_agent_socket(self):
        workspace = self.workspace()
        workspace.ssh_config.parent.mkdir(mode=0o700)
        workspace.ssh_config.write_text(
            self.devpod_block(workspace.name), encoding="utf-8"
        )
        workspace.ssh_config.chmod(0o600)
        wrapper = self.executable("devpod-wrapper", "#!/bin/sh\nexit 0\n")
        socket = self.runtime / "project agent.sock"

        devpod_ssh_config.patch_ssh_block(
            workspace, devpod_models.SSHMode.PROJECT_KEY, wrapper, socket
        )

        content = workspace.ssh_config.read_text(encoding="utf-8")
        self.assertIn("  ForwardAgent yes\n", content)
        self.assertIn(f'  IdentityAgent "{socket}"\n', content)
        self.assertNotIn("/usr/bin/devpod", content)

        if shutil.which("ssh"):
            parsed = subprocess.run(
                [
                    shutil.which("ssh"),
                    "-G",
                    "-F",
                    str(workspace.ssh_config),
                    workspace.host,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(parsed.returncode, 0, parsed.stderr)
            self.assertIn(f"identityagent {socket}\n", parsed.stdout.lower())

    @unittest.skipUnless(shutil.which("ssh"), "OpenSSH client is required")
    def test_ide_only_block_is_accepted_by_openssh_without_an_agent(self):
        workspace = self.workspace()
        workspace.ssh_config.parent.mkdir(mode=0o700)
        workspace.ssh_config.write_text(
            self.devpod_block(workspace.name), encoding="utf-8"
        )
        workspace.ssh_config.chmod(0o600)
        wrapper = self.executable("devpod-wrapper", "#!/bin/sh\nexit 0\n")

        devpod_ssh_config.patch_ssh_block(
            workspace, devpod_models.SSHMode.IDE_ONLY, wrapper
        )
        parsed = subprocess.run(
            [
                shutil.which("ssh"),
                "-G",
                "-F",
                str(workspace.ssh_config),
                workspace.host,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(parsed.returncode, 0, parsed.stderr)
        self.assertIn("forwardagent no\n", parsed.stdout.lower())
        self.assertIn("identityagent none\n", parsed.stdout.lower())

    def test_ide_only_block_overrides_a_saved_project_key_without_deleting_it(self):
        workspace = self.workspace()
        workspace.ssh_config.parent.mkdir(mode=0o700)
        workspace.ssh_config.write_text(
            self.devpod_block(workspace.name, forwarding="no").replace(
                "  ForwardAgent no\n", "  ForwardAgent no\n  IdentityAgent none\n"
            ),
            encoding="utf-8",
        )
        workspace.ssh_config.chmod(0o600)

        with mock.patch.object(
            devpod_keys, "managed_identity", return_value=self.root / "saved-key"
        ):
            mode = devpod_modes.infer_mode(workspace)

        self.assertEqual(mode, devpod_models.SSHMode.IDE_ONLY)

    def test_unmarked_host_block_is_never_modified(self):
        workspace = self.workspace()
        workspace.ssh_config.parent.mkdir(mode=0o700)
        original = (
            f"Host {workspace.host}\n  ForwardAgent yes\n  ProxyCommand custom proxy\n"
        )
        workspace.ssh_config.write_text(original, encoding="utf-8")
        workspace.ssh_config.chmod(0o600)
        wrapper = self.executable("devpod-wrapper", "#!/bin/sh\nexit 0\n")

        found = devpod_ssh_config.patch_ssh_block(
            workspace, devpod_models.SSHMode.IDE_ONLY, wrapper
        )

        self.assertFalse(found)
        self.assertEqual(workspace.ssh_config.read_text(encoding="utf-8"), original)

    def test_malformed_markers_fail_closed_without_writing(self):
        workspace = self.workspace()
        workspace.ssh_config.parent.mkdir(mode=0o700)
        original = f"# DevPod Start {workspace.host}\nHost {workspace.host}\n"
        workspace.ssh_config.write_text(original, encoding="utf-8")
        workspace.ssh_config.chmod(0o600)
        wrapper = self.executable("devpod-wrapper", "#!/bin/sh\nexit 0\n")

        with self.assertRaisesRegex(
            devpod_errors.DevPodGuardError, "malformed DevPod-managed"
        ):
            devpod_ssh_config.patch_ssh_block(
                workspace, devpod_models.SSHMode.IDE_ONLY, wrapper
            )

        self.assertEqual(workspace.ssh_config.read_text(encoding="utf-8"), original)

    def test_nested_devpod_markers_fail_closed(self):
        workspace = self.workspace()
        text = self.devpod_block(workspace.name).replace(
            f"Host {workspace.host}\n",
            f"Host {workspace.host}\n# DevPod Start nested.devpod\n",
        )

        with self.assertRaisesRegex(
            devpod_errors.DevPodGuardError, "nested DevPod-managed"
        ):
            devpod_ssh_config.devpod_block_bounds(text, workspace.host)

    def test_uniform_crlf_line_endings_are_preserved(self):
        workspace = self.workspace()
        workspace.ssh_config.parent.mkdir(mode=0o700)
        original = (
            self.devpod_block(workspace.name) + "Host unrelated\n  User test\n"
        ).replace("\n", "\r\n")
        workspace.ssh_config.write_bytes(original.encode())
        workspace.ssh_config.chmod(0o600)
        wrapper = self.executable("devpod-wrapper", "#!/bin/sh\nexit 0\n")

        devpod_ssh_config.patch_ssh_block(
            workspace, devpod_models.SSHMode.IDE_ONLY, wrapper
        )

        updated = workspace.ssh_config.read_bytes()
        self.assertNotIn(b"\n", updated.replace(b"\r\n", b""))
        self.assertIn(b"Host unrelated\r\n  User test\r\n", updated)

    def test_general_identity_at_ssh_root_is_rejected(self):
        ssh_directory = self.home / ".ssh"
        ssh_directory.mkdir(mode=0o700)
        private_key = ssh_directory / "id_ed25519"
        private_key.write_text("not inspected", encoding="utf-8")
        private_key.chmod(0o600)

        with self.assertRaisesRegex(devpod_errors.DevPodGuardError, "general identity"):
            devpod_keys.validate_private_key(private_key, reject_general_identity=True)

    def test_managed_key_directory_accepts_an_existing_private_ssh_directory(self):
        ssh_directory = self.home / ".ssh"
        ssh_directory.mkdir(mode=0o700)
        managed = ssh_directory / "paranoid-podman/default/project"

        devpod_paths.ensure_private_directory(managed)

        self.assertTrue(managed.is_dir())
        self.assertEqual(stat.S_IMODE(managed.stat().st_mode), 0o700)

    @unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen is required")
    def test_existing_project_key_is_referenced_and_never_copied(self):
        workspace = self.workspace()
        source_directory = self.home / "project-keys/example-workspace"
        source_directory.mkdir(parents=True, mode=0o700)
        private_key = source_directory / "deploy"
        generated = subprocess.run(
            [
                shutil.which("ssh-keygen"),
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-f",
                str(private_key),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        original_private_key = private_key.read_bytes()

        selected = devpod_keys.select_project_key(workspace, str(private_key))

        managed_directory = devpod_paths.project_key_directory(workspace)
        managed_link = managed_directory / "selected"
        self.assertEqual(selected, private_key)
        self.assertTrue(managed_link.is_symlink())
        self.assertEqual(managed_link.resolve(strict=True), private_key)
        self.assertEqual(devpod_keys.managed_identity(workspace), private_key)
        self.assertTrue((managed_directory / "selected.pub").is_file())
        self.assertEqual(
            private_key.read_bytes(),
            original_private_key,
            "private key was changed",
        )

    def test_public_key_validation_accepts_openssh_security_key_types(self):
        public_key = "sk-ssh-ed25519@openssh.com AAAAC3NzaC1lZDI1NTE5AAAAIA==\n"

        self.assertEqual(
            devpod_keys.validated_public_key(public_key), public_key.strip()
        )
        with self.assertRaisesRegex(
            devpod_errors.DevPodGuardError, "unrecognized public key"
        ):
            devpod_keys.validated_public_key(public_key + "ProxyCommand unsafe\n")

    def test_runtime_lock_is_private_and_scoped(self):
        with devpod_locks.runtime_lock("agent", "default/example-workspace"):
            lock_files = list(
                (self.runtime / "paranoid-podman/locks").glob("agent-*.lock")
            )
            self.assertEqual(len(lock_files), 1)
            self.assertEqual(stat.S_IMODE(lock_files[0].stat().st_mode), 0o600)

    def test_stale_agent_pid_never_signals_an_unrelated_process(self):
        directory = self.runtime / "agent-state"
        directory.mkdir(mode=0o700)
        paths = devpod_models.AgentPaths(
            directory,
            directory / "agent.sock",
            directory / "agent.pid",
        )
        paths.pid_file.write_text(f"{os.getpid()}\n", encoding="ascii")
        paths.pid_file.chmod(0o600)

        with mock.patch.object(os, "kill") as kill:
            devpod_agent.stop_agent_unlocked(paths)

        kill.assert_not_called()
        self.assertFalse(paths.pid_file.exists())

    def test_ide_only_up_never_passes_the_ambient_agent_and_repatches_devpod(self):
        workspace = self.workspace()
        workspace.ssh_config.parent.mkdir(mode=0o700)
        workspace.ssh_config.write_text(
            self.devpod_block(workspace.name, forwarding="no").replace(
                "  ForwardAgent no\n", "  ForwardAgent no\n  IdentityAgent none\n"
            ),
            encoding="utf-8",
        )
        workspace.ssh_config.chmod(0o600)
        log_path = self.root / "devpod-log.json"
        fake_devpod = self.executable(
            "real-devpod",
            """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

if sys.argv[1:3] == ["context", "options"]:
    values = {
        name: {"value": value}
        for name, value in {
            "GIT_SSH_SIGNATURE_FORWARDING": "false",
            "GPG_AGENT_FORWARDING": "false",
            "SSH_ADD_PRIVATE_KEYS": "false",
            "SSH_AGENT_FORWARDING": "true",
            "SSH_INJECT_DOCKER_CREDENTIALS": "false",
            "SSH_INJECT_GIT_CREDENTIALS": "false",
        }.items()
    }
    print(json.dumps(values))
    raise SystemExit(0)
if sys.argv[1] == "up":
    config = Path(os.environ["TEST_SSH_CONFIG"])
    config.write_text(os.environ["TEST_DEVPOD_BLOCK"], encoding="utf-8")
    Path(os.environ["TEST_LOG"]).write_text(
        json.dumps({"sock": os.environ.get("SSH_AUTH_SOCK")}), encoding="utf-8"
    )
    raise SystemExit(0)
raise SystemExit(2)
""",
        )
        wrapper = self.executable("devpod-wrapper", "#!/bin/sh\nexit 0\n")
        with mock.patch.dict(
            os.environ,
            {
                "SSH_AUTH_SOCK": "/tmp/ambient-agent.sock",
                "TEST_SSH_CONFIG": str(workspace.ssh_config),
                "TEST_DEVPOD_BLOCK": self.devpod_block(workspace.name),
                "TEST_LOG": str(log_path),
                "PARANOID_PODMAN_DEVPOD_WRAPPER": str(wrapper),
            },
            clear=False,
        ):
            result = devpod_session.run_intercepted_devpod(
                fake_devpod,
                ["up", workspace.name, "--open-ide=false"],
                "up",
                workspace,
            )

        self.assertEqual(result, 0)
        self.assertIsNone(json.loads(log_path.read_text(encoding="utf-8"))["sock"])
        content = workspace.ssh_config.read_text(encoding="utf-8")
        self.assertIn('  ForwardAgent no\n  IdentityAgent "none"\n', content)
        self.assertIn(f"ProxyCommand {wrapper} ssh --stdio", content)

    def test_up_opens_ide_only_after_the_ssh_block_is_protected(self):
        workspace = self.workspace()
        workspace.ssh_config.parent.mkdir(mode=0o700)
        workspace.ssh_config.write_text(
            self.devpod_block(workspace.name, forwarding="no").replace(
                "  ForwardAgent no\n", "  ForwardAgent no\n  IdentityAgent none\n"
            ),
            encoding="utf-8",
        )
        workspace.ssh_config.chmod(0o600)
        log_path = self.root / "devpod-up-log.json"
        fake_devpod = self.executable(
            "real-devpod",
            """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

if sys.argv[1:3] == ["context", "options"]:
    values = {
        name: {"value": value}
        for name, value in {
            "GIT_SSH_SIGNATURE_FORWARDING": "false",
            "GPG_AGENT_FORWARDING": "false",
            "SSH_ADD_PRIVATE_KEYS": "false",
            "SSH_AGENT_FORWARDING": "true",
            "SSH_INJECT_DOCKER_CREDENTIALS": "false",
            "SSH_INJECT_GIT_CREDENTIALS": "false",
        }.items()
    }
    print(json.dumps(values))
    raise SystemExit(0)
if "up" in sys.argv[1:]:
    config = Path(os.environ["TEST_SSH_CONFIG"])
    if "--open-ide=false" in sys.argv:
        config.write_text(os.environ["TEST_DEVPOD_BLOCK"], encoding="utf-8")
    log = Path(os.environ["TEST_LOG"])
    events = json.loads(log.read_text(encoding="utf-8")) if log.exists() else []
    events.append(
        {
            "arguments": sys.argv[1:],
            "config": config.read_text(encoding="utf-8"),
            "sock": os.environ.get("SSH_AUTH_SOCK"),
        }
    )
    log.write_text(json.dumps(events), encoding="utf-8")
    raise SystemExit(0)
raise SystemExit(2)
""",
        )
        wrapper = self.executable("devpod-wrapper", "#!/bin/sh\nexit 0\n")
        with mock.patch.dict(
            os.environ,
            {
                "SSH_AUTH_SOCK": "/tmp/ambient-agent.sock",
                "TEST_SSH_CONFIG": str(workspace.ssh_config),
                "TEST_DEVPOD_BLOCK": self.devpod_block(workspace.name),
                "TEST_LOG": str(log_path),
                "PARANOID_PODMAN_DEVPOD_WRAPPER": str(wrapper),
            },
            clear=False,
        ):
            result = devpod_session.run_intercepted_devpod(
                fake_devpod,
                ["up", workspace.name],
                "up",
                workspace,
            )

        self.assertEqual(result, 0)
        events = json.loads(log_path.read_text(encoding="utf-8"))
        self.assertEqual(len(events), 2)
        self.assertIn("--open-ide=false", events[0]["arguments"])
        self.assertIsNone(events[0]["sock"])
        self.assertIn("--open-ide=true", events[1]["arguments"])
        self.assertIn("--configure-ssh=false", events[1]["arguments"])
        self.assertIn('IdentityAgent "none"', events[1]["config"])
        self.assertIn(str(wrapper), events[1]["config"])
        self.assertIsNone(events[1]["sock"])

    def test_context_changes_are_minimal_and_use_no_shell(self):
        current = {
            name: {"value": value}
            for name, value in devpod_context.SAFE_CONTEXT_OPTIONS.items()
        }
        current["SSH_ADD_PRIVATE_KEYS"] = {"value": "true"}
        responses = [
            type(
                "Result",
                (),
                {"stdout": json.dumps(current), "returncode": 0},
            )(),
            type("Result", (), {"stdout": "", "returncode": 0})(),
            type(
                "Result",
                (),
                {
                    "stdout": json.dumps(
                        {
                            name: {"value": value}
                            for name, value in devpod_context.SAFE_CONTEXT_OPTIONS.items()
                        }
                    ),
                    "returncode": 0,
                },
            )(),
        ]
        fake_devpod = self.executable("real-devpod", "#!/bin/sh\nexit 0\n")

        with mock.patch.object(
            devpod_process, "run_checked", side_effect=responses
        ) as run:
            devpod_context.apply_safe_context_options(fake_devpod, self.workspace())

        self.assertEqual(run.call_count, 3)
        command = run.call_args_list[1].args[0]
        self.assertEqual(
            command,
            [
                str(fake_devpod),
                "context",
                "set-options",
                "--context",
                "default",
                "--option",
                "SSH_ADD_PRIVATE_KEYS=false",
            ],
        )

    def test_context_changes_must_be_retained_before_workspace_access(self):
        unsafe = {
            name: {"value": value}
            for name, value in devpod_context.SAFE_CONTEXT_OPTIONS.items()
        }
        unsafe["SSH_INJECT_DOCKER_CREDENTIALS"] = {"value": "true"}
        responses = [
            type(
                "Result",
                (),
                {"stdout": json.dumps(unsafe), "returncode": 0},
            )(),
            type("Result", (), {"stdout": "", "returncode": 0})(),
            type(
                "Result",
                (),
                {"stdout": json.dumps(unsafe), "returncode": 0},
            )(),
        ]

        with mock.patch.object(devpod_process, "run_checked", side_effect=responses):
            with self.assertRaisesRegex(
                devpod_errors.DevPodGuardError,
                "did not retain required protected context options",
            ):
                devpod_context.apply_safe_context_options(
                    self.root / "devpod", self.workspace()
                )

    def test_unconfigured_build_defaults_to_no_host_credentials(self):
        workspace = self.workspace()
        with mock.patch.object(devpod_modes, "infer_mode", return_value=None):
            mode = devpod_modes.choose_command_mode(
                workspace,
                "build",
                allow_interactive_configuration=False,
            )

        self.assertEqual(mode, devpod_models.SSHMode.IDE_ONLY)

    def test_second_ide_phase_preserves_connection_options(self):
        workspace = self.workspace()

        arguments = devpod_arguments.ide_open_arguments(
            [
                "up",
                workspace.name,
                "--disable-daemon",
                "--machine",
                "local-machine",
                "--provider-option",
                "ENGINE=podman",
                "--ide-option=PORT=3000",
            ],
            workspace,
        )

        self.assertIn("--disable-daemon", arguments)
        self.assertIn("local-machine", arguments)
        self.assertIn("ENGINE=podman", arguments)
        self.assertIn("PORT=3000", arguments)
        self.assertIn("--configure-ssh=false", arguments)
        up_index = arguments.index("up")
        self.assertGreater(arguments.index("--disable-daemon"), up_index)
        self.assertGreater(arguments.index("--machine"), up_index)

    def test_context_owned_ssh_config_path_is_resolved(self):
        workspace = self.workspace()
        configured = self.root / "custom-ssh-config"
        response = type(
            "Result",
            (),
            {
                "stdout": json.dumps({"SSH_CONFIG_PATH": {"value": str(configured)}}),
                "returncode": 0,
            },
        )()

        with mock.patch.object(devpod_process, "run_checked", return_value=response):
            resolved = devpod_context.resolve_context_ssh_config(
                self.root / "devpod", workspace
            )

        self.assertEqual(resolved.ssh_config, configured)

    def test_unconfigured_noninteractive_workspace_fails_with_a_command(self):
        workspace = self.workspace()
        with (
            mock.patch.object(devpod_modes, "infer_mode", return_value=None),
            mock.patch.object(devpod_terminal, "interactive_stream", return_value=None),
        ):
            with self.assertRaisesRegex(
                devpod_errors.DevPodGuardError,
                "paranoid-podman devpod configure example-workspace",
            ):
                devpod_modes.choose_mode(workspace)

    def test_closed_menu_input_cancels_without_creating_a_key(self):
        with mock.patch.object(devpod_keys, "create_project_key") as create:
            with self.assertRaisesRegex(
                devpod_errors.DevPodGuardError, "input was closed"
            ):
                devpod_modes.prompt_choice(self.workspace(), io.StringIO(""))

        create.assert_not_called()
        self.assertFalse((self.home / ".ssh").exists())

    def test_unconfigured_stdio_proxy_never_prompts(self):
        workspace = self.workspace()
        with (
            mock.patch.object(devpod_modes, "infer_mode", return_value=None),
            mock.patch.object(devpod_terminal, "interactive_stream") as interactive,
        ):
            with self.assertRaisesRegex(
                devpod_errors.DevPodGuardError,
                "paranoid-podman devpod configure example-workspace",
            ):
                devpod_modes.choose_mode(
                    workspace, allow_interactive_configuration=False
                )

        interactive.assert_not_called()

    def test_stdio_project_key_reload_is_explicitly_noninteractive(self):
        workspace = self.workspace()
        identity = self.root / "project-key"
        expected_paths = devpod_models.AgentPaths(
            self.runtime, self.runtime / "agent.sock", self.runtime / "agent.pid"
        )
        with (
            mock.patch.object(devpod_context, "apply_safe_context_options"),
            mock.patch.object(devpod_keys, "managed_identity", return_value=identity),
            mock.patch.object(
                devpod_agent, "ensure_agent", return_value=expected_paths
            ) as ensure,
        ):
            paths = devpod_modes.prepare_mode(
                self.root / "devpod",
                workspace,
                devpod_models.SSHMode.PROJECT_KEY,
                allow_agent_prompt=False,
            )

        self.assertEqual(paths, expected_paths)
        ensure.assert_called_once_with(workspace, identity, allow_prompt=False)

    def test_agent_reload_prompts_only_on_a_real_terminal(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=True):
            self.assertTrue(
                devpod_modes.agent_prompt_allowed(["up", "example-workspace"])
            )
            for option in ("--stdio", "--stdio=true", "--proxy=true"):
                with self.subTest(option=option):
                    self.assertFalse(
                        devpod_modes.agent_prompt_allowed(
                            ["ssh", option, "example-workspace"]
                        )
                    )
        with mock.patch.object(sys.stdin, "isatty", return_value=False):
            self.assertFalse(
                devpod_modes.agent_prompt_allowed(["build", "example-workspace"])
            )

    def test_context_setup_cannot_write_to_ssh_protocol_stdout(self):
        workspace = self.workspace()
        changed = self.root / "context-updated"
        baseline = dict(devpod_context.SAFE_CONTEXT_OPTIONS)
        fake_devpod = self.executable(
            "context-provider",
            f"""#!/usr/bin/env python3
import json
import sys
from pathlib import Path

marker = Path({str(changed)!r})
if sys.argv[1:3] == ["context", "options"]:
    values = {baseline!r}
    if not marker.exists():
        values["SSH_ADD_PRIVATE_KEYS"] = "true"
    print(json.dumps({{name: {{"value": value}} for name, value in values.items()}}))
elif sys.argv[1:3] == ["context", "set-options"]:
    marker.touch()
    print("synthetic provider setup output")
else:
    raise SystemExit(2)
""",
        )
        script = (
            "import sys\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, {str(PROJECT_ROOT / 'src')!r})\n"
            "from paranoid_podman.devpod.models import Workspace\n"
            "from paranoid_podman.devpod.context import apply_safe_context_options\n"
            f"workspace = Workspace('default', {workspace.name!r}, "
            f"Path({str(workspace.ssh_config)!r}))\n"
            f"apply_safe_context_options(Path({str(fake_devpod)!r}), workspace)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(changed.exists())
        self.assertEqual(result.stdout, "")

    def test_unreviewed_signing_agents_cannot_override_the_safe_context(self):
        with self.assertRaisesRegex(
            devpod_errors.DevPodGuardError, "GPG-agent forwarding is blocked"
        ):
            devpod_arguments.reject_unreviewed_credential_forwarding(
                ["up", "example-workspace", "--gpg-agent-forwarding"]
            )
        with self.assertRaisesRegex(
            devpod_errors.DevPodGuardError, "SSH signing-key forwarding is blocked"
        ):
            devpod_arguments.reject_unreviewed_credential_forwarding(
                ["up", "example-workspace", "--git-ssh-signing-key", "key.pub"]
            )
        devpod_arguments.reject_unreviewed_credential_forwarding(
            ["up", "example-workspace", "--gpg-agent-forwarding=false"]
        )

    @unittest.skipUnless(
        all(shutil.which(tool) for tool in ("ssh-add", "ssh-agent", "ssh-keygen")),
        "OpenSSH client tools are required",
    )
    def test_real_dedicated_agent_contains_only_the_project_identity(self):
        if os.environ.get("PARANOID_PODMAN_RUN_SSH_AGENT_TESTS") != "1":
            self.skipTest(
                "set PARANOID_PODMAN_RUN_SSH_AGENT_TESTS=1 for the real-agent test"
            )
        workspace = self.workspace()
        key_directory = self.home / "keys"
        key_directory.mkdir(mode=0o700)
        private_key = key_directory / "project"
        generated = subprocess.run(
            [
                shutil.which("ssh-keygen"),
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-f",
                str(private_key),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)

        try:
            paths = devpod_agent.ensure_agent(workspace, private_key)
            expected = devpod_keys.key_fingerprint(private_key)

            self.assertEqual(devpod_agent.agent_fingerprints(paths), [expected])
            self.assertTrue(paths.socket.exists())
            first_pid = devpod_agent.read_agent_pid(paths)
            reused = devpod_agent.ensure_agent(workspace, private_key)
            self.assertEqual(devpod_agent.read_agent_pid(reused), first_pid)
        finally:
            devpod_agent.stop_agent(workspace)
        self.assertFalse(paths.socket.exists())

    def test_provider_self_check_accepts_versions_beyond_the_tested_release(self):
        for version in ("0.6.14", "0.6.15", "0.6.16", "0.7.0", "1.0.0"):
            with self.subTest(version=version):
                executable = self.executable(
                    "real-devpod", f"#!{sys.executable}\nprint('v{version}')\n"
                )
                self.assertEqual(
                    devpod_provider.validate_devpod_version(executable), version
                )

    def test_provider_self_check_still_rejects_invalid_or_failed_version_output(self):
        with self.assertRaisesRegex(devpod_errors.DevPodGuardError, "could not parse"):
            devpod_provider.parse_devpod_version("devpod version unknown")
        executable = self.executable(
            "real-devpod",
            f"#!{sys.executable}\nprint('v0.6.16')\nraise SystemExit(7)\n",
        )
        with self.assertRaisesRegex(
            devpod_errors.DevPodGuardError, "self-check failed"
        ):
            devpod_provider.validate_devpod_version(executable)

    def test_ssh_config_mode_is_preserved_after_atomic_rewrite(self):
        workspace = self.workspace()
        workspace.ssh_config.parent.mkdir(mode=0o700)
        workspace.ssh_config.write_text(
            self.devpod_block(workspace.name), encoding="utf-8"
        )
        workspace.ssh_config.chmod(0o640)
        wrapper = self.executable("devpod-wrapper", "#!/bin/sh\nexit 0\n")

        devpod_ssh_config.patch_ssh_block(
            workspace, devpod_models.SSHMode.IDE_ONLY, wrapper
        )

        self.assertEqual(stat.S_IMODE(workspace.ssh_config.stat().st_mode), 0o640)
