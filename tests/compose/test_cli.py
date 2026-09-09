import json
import os
import pty
import pwd
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUARD = PROJECT_ROOT / "bin" / "compose-guard"
FAKE_FRONTEND = PROJECT_ROOT / "tests" / "support" / "fake_compose_frontend.py"
COMPOSE_ENTRY_POINTS = (
    (PROJECT_ROOT / "bin" / "podman", ["compose"]),
    (PROJECT_ROOT / "bin" / "docker", ["compose"]),
    (PROJECT_ROOT / "bin" / "podman-compose", []),
    (PROJECT_ROOT / "bin" / "docker-compose", []),
)

SAFE_CONFIG = """services:
  app:
    image: example.invalid/image
"""


class ComposeGuardIntegrationTests(unittest.TestCase):
    def run_guard(
        self,
        arguments,
        config=SAFE_CONFIG,
        *,
        config_error="",
        config_exit_code=0,
        execution_exit_code=0,
        guard=GUARD,
        prefix=(),
        add_compose_file=True,
        confirmation=None,
        prepare_project=None,
        extra_env=None,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            project_dir = temp_path / "project"
            project_dir.mkdir()
            compose_file = project_dir / "compose.yaml"
            compose_file.write_text(SAFE_CONFIG, encoding="utf-8")
            if prepare_project:
                prepare_project(project_dir)
            if callable(config):
                config = config(project_dir)

            output_path = temp_path / "calls.jsonl"
            env = os.environ.copy()
            env.update(
                {
                    "PARANOID_COMPOSE_TEST_CONFIG": config,
                    "PARANOID_COMPOSE_TEST_CONFIG_ERROR": config_error,
                    "PARANOID_COMPOSE_TEST_CONFIG_EXIT_CODE": str(config_exit_code),
                    "PARANOID_COMPOSE_TEST_EXIT_CODE": str(execution_exit_code),
                    "PARANOID_COMPOSE_TEST_OUTPUT": str(output_path),
                    "PODMAN_GUARD_REAL_PODMAN": str(FAKE_FRONTEND),
                    "PODMAN_GUARD_COMPOSE_PROVIDER": str(FAKE_FRONTEND),
                    "PODMAN_GUARD_INSTALLATION_ID": "a" * 64,
                    "SSH_AUTH_SOCK": "/tmp/synthetic-agent.sock",
                    "SYNTHETIC_API_TOKEN": "synthetic-host-secret",
                }
            )
            for key in (
                "PODMAN_GUARD_CAP_DROP",
                "PODMAN_GUARD_DEBUG",
                "PODMAN_GUARD_KEEP_ID",
                "PODMAN_GUARD_NO_NEW_PRIVS",
                "PODMAN_GUARD_PROTECT_GIT",
            ):
                env.pop(key, None)
            env.update(extra_env or {})

            command_arguments = [*prefix]
            if add_compose_file:
                command_arguments.extend(["-f", str(compose_file)])
            command_arguments.extend(arguments)
            command = [str(guard), *command_arguments]

            if confirmation is None:
                result = subprocess.run(
                    command,
                    cwd=project_dir,
                    env=env,
                    # Capturing output does not disconnect a parent's terminal.
                    # This branch deliberately exercises non-interactive callers.
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
            else:
                master_fd, slave_fd = pty.openpty()
                try:
                    process = subprocess.Popen(
                        command,
                        cwd=project_dir,
                        env=env,
                        stdin=slave_fd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        start_new_session=True,
                    )
                    os.close(slave_fd)
                    slave_fd = -1
                    os.write(master_fd, f"{confirmation}\n".encode())
                    try:
                        stdout, stderr = process.communicate(timeout=10)
                    except BaseException:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.communicate(timeout=5)
                        raise
                    result = subprocess.CompletedProcess(
                        command, process.returncode, stdout, stderr
                    )
                finally:
                    os.close(master_fd)
                    if slave_fd >= 0:
                        os.close(slave_fd)

            if result.returncode == 125 and execution_exit_code != 125:
                self.assertIn("compose-guard: next step:", result.stderr)

            records = []
            if output_path.exists():
                records = [
                    json.loads(line)
                    for line in output_path.read_text(encoding="utf-8").splitlines()
                ]
            return result, records, compose_file

    def test_unsafe_structured_config_is_denied_without_printing_values(self):
        marker = "synthetic-secret-that-must-not-be-logged"
        cases = {
            "privileged container": f"""services:
  app:
    image: example.invalid/image
    privileged: YES
    command: [\"{marker}\"]
""",
            "host namespace": f"""services:
  app:
    image: example.invalid/image
    network_mode: host
    command: [\"{marker}\"]
""",
            "capability": f"""services:
  app:
    image: example.invalid/image
    cap_add: [SYS_ADMIN]
    command: [\"{marker}\"]
""",
            "environment file": f"""services:
  app:
    image: example.invalid/image
    env_file: .env
    command: [\"{marker}\"]
""",
            "engine socket": f"""services:
  app:
    image: example.invalid/image
    volumes:
      - /run/podman/podman.sock:/run/podman/podman.sock
    command: [\"{marker}\"]
""",
            "image file transport": f"""services:
  app:
    image: docker-archive:/tmp/untrusted-image.tar
    command: [\"{marker}\"]
""",
            "unknown setting": f"""services:
  app:
    image: example.invalid/image
    some_future_option: true
    command: [\"{marker}\"]
""",
        }
        for label, config in cases.items():
            with self.subTest(label=label):
                result, records, _ = self.run_guard(["up"], config)
                self.assertEqual(result.returncode, 125)
                if label == "unknown setting":
                    self.assertIn("UNSUPPORTED [unsupported]", result.stderr)
                    self.assertIn("some_future_option", result.stderr)
                else:
                    self.assertIn("blocked resolved configuration", result.stderr)
                self.assertNotIn(marker, result.stdout + result.stderr)
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["argv"][-1], "config")

    def test_literal_secrets_in_compose_source_are_denied_without_printing_values(self):
        marker = "synthetic-compose-secret"
        cases = {
            "environment mapping": f"""services:
  app:
    image: example.invalid/image
    environment:
      APP_API_TOKEN: {marker}
""",
            "environment list": f"""services:
  app:
    image: example.invalid/image
    environment:
      - APP_API_TOKEN={marker}
""",
            "environment anchor": f"""x-application-environment: &application-environment
  APP_API_TOKEN: {marker}
services:
  app:
    image: example.invalid/image
    environment: *application-environment
""",
        }

        for label, config in cases.items():
            with self.subTest(label=label):

                def prepare(project_dir, source_config=config):
                    (project_dir / "compose.yaml").write_text(
                        source_config, encoding="utf-8"
                    )

                result, records, _ = self.run_guard(
                    ["up"], config, prepare_project=prepare
                )

                self.assertEqual(result.returncode, 125)
                self.assertIn("literal secret in Compose source", result.stderr)
                self.assertIn("APP_API_TOKEN", result.stderr)
                self.assertNotIn(marker, result.stdout + result.stderr)
                self.assertEqual(records, [])

    def test_project_dotenv_interpolates_application_values_and_filters_controls(self):
        marker = "synthetic-project-secret"
        source = """services:
  app:
    image: example.invalid/image
    environment:
      APP_ENV: "${APP_ENV:?required}"
      APP_API_TOKEN: "${APP_API_TOKEN:?required}"
"""
        resolved = f"""services:
  app:
    image: example.invalid/image
    environment:
      APP_ENV: development
      APP_API_TOKEN: {marker}
"""

        def prepare(project_dir):
            (project_dir / "compose.yaml").write_text(source, encoding="utf-8")
            (project_dir / ".env").write_text(
                "APP_ENV=development\n"
                f"APP_API_TOKEN={marker}\n"
                "PODMAN_HOST=ssh://untrusted.invalid/run/podman.sock\n"
                "COMPOSE_PROJECT_NAME=untrusted-name\n",
                encoding="utf-8",
            )

        result, records, source_file = self.run_guard(
            ["ps"], resolved, prepare_project=prepare
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(records), 2)
        render = records[0]
        self.assertIn("APP_ENV=development", render["env_file"])
        self.assertIn("APP_API_TOKEN=", render["env_file"])
        self.assertNotIn("PODMAN_HOST", render["env_file"])
        self.assertNotIn("COMPOSE_PROJECT_NAME", render["env_file"])
        self.assertNotIn(str(source_file.parent / ".env"), render["argv"])
        self.assertNotIn(marker, result.stdout + result.stderr)
        self.assertEqual(records[1]["env_file"], "")

    def test_project_dotenv_must_be_regular_and_have_explicit_values(self):
        def symlinked(project_dir):
            outside = project_dir.parent / "outside.env"
            outside.write_text("APP_ENV=development\n", encoding="utf-8")
            (project_dir / ".env").symlink_to(outside)

        result, records, _ = self.run_guard(["ps"], prepare_project=symlinked)
        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertIn("regular non-symlink", result.stderr)
        self.assertEqual(records, [])

        result, records, _ = self.run_guard(
            ["--env-file", ".env", "ps"], prepare_project=symlinked
        )
        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertIn("regular non-symlink", result.stderr)
        self.assertEqual(records, [])

        def implicit(project_dir):
            (project_dir / ".env").write_text("APP_ENV\n", encoding="utf-8")

        result, records, _ = self.run_guard(["ps"], prepare_project=implicit)
        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertIn("without a value at line 1", result.stderr)
        self.assertEqual(records, [])

    def test_project_local_service_env_file_is_copied_into_private_snapshot(self):
        marker = "synthetic-service-env-secret"
        config = """services:
  app:
    image: example.invalid/image
    env_file: .service.env
"""

        def prepare(project_dir):
            (project_dir / "compose.yaml").write_text(config, encoding="utf-8")
            (project_dir / ".service.env").write_text(
                f"APP_API_TOKEN={marker}\nAPP_MODE=development\n",
                encoding="utf-8",
            )

        result, records, source_file = self.run_guard(
            ["ps"], config, prepare_project=prepare
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(records), 2)
        snapshot = yaml.safe_load(records[1]["snapshot"])
        private_env_file = snapshot["services"]["app"]["env_file"][0]
        self.assertNotEqual(private_env_file, str(source_file.parent / ".service.env"))
        self.assertFalse(Path(private_env_file).exists())
        self.assertIn(marker, records[1]["service_env_files"][0])
        self.assertNotIn(marker, result.stdout + result.stderr)

    def test_service_env_file_cannot_leave_the_project(self):
        config = """services:
  app:
    image: example.invalid/image
    env_file: ../outside.env
"""

        def prepare(project_dir):
            (project_dir / "compose.yaml").write_text(config, encoding="utf-8")
            (project_dir.parent / "outside.env").write_text(
                "APP_MODE=development\n", encoding="utf-8"
            )

        result, records, _ = self.run_guard(["ps"], config, prepare_project=prepare)

        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertIn("env_file outside the project", result.stderr)
        self.assertEqual(records, [])

    def test_noninteractive_mutation_runs_reviewed_snapshot_without_values(self):
        marker = "synthetic-value-that-must-not-be-logged"
        config = f"""services:
  app:
    image: example.invalid/image
    environment:
      MESSAGE: {marker}
"""
        result, records, _ = self.run_guard(["up"], config)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertNotIn(marker, result.stdout + result.stderr)
        self.assertEqual(len(records), 2)

    def test_provider_error_is_hidden(self):
        marker = "synthetic-provider-error-secret"
        result, records, _ = self.run_guard(
            ["up"],
            config_error=marker,
            config_exit_code=17,
        )

        self.assertEqual(result.returncode, 125)
        self.assertIn("provider output hidden", result.stderr)
        self.assertNotIn(marker, result.stdout + result.stderr)
        self.assertEqual(len(records), 1)

    def test_render_failure_reports_source_paths_without_values(self):
        marker = "synthetic-required-value-that-must-stay-hidden"

        def prepare(project_dir):
            (project_dir / "compose.yaml").write_text(
                f"""services:
  app:
    environment:
      APP_SETTING: "${{REQUIRED_SETTING:?{marker}}}"
    image: example.invalid/image
""",
                encoding="utf-8",
            )

        result, records, _ = self.run_guard(
            ["up"],
            config_error=f"required variable: {marker}",
            config_exit_code=17,
            prepare_project=prepare,
        )

        self.assertEqual(result.returncode, 125)
        self.assertIn("category: environment interpolation failed", result.stderr)
        self.assertIn("compose.yaml:3", result.stderr)
        self.assertIn("services.<service>.environment", result.stderr)
        self.assertIn("compose.yaml:4", result.stderr)
        self.assertIn("services.<service>.environment.<variable>", result.stderr)
        self.assertNotIn(marker, result.stdout + result.stderr)
        self.assertEqual(len(records), 1)

    def test_debug_lists_relevant_source_fields_without_values(self):
        config = """services:
  app:
    image: example.invalid/image
    security_opt:
      - no-new-privileges
"""

        def prepare(project_dir):
            (project_dir / "compose.yaml").write_text(config, encoding="utf-8")

        result, records, _ = self.run_guard(
            ["up"],
            config,
            confirmation="",
            prepare_project=prepare,
            extra_env={"PODMAN_GUARD_DEBUG": "1"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("compose.yaml:4", result.stderr)
        self.assertIn("services.<service>.security_opt", result.stderr)
        self.assertEqual(len(records), 2)

    def test_read_only_command_uses_a_reviewed_snapshot(self):
        result, records, source_file = self.run_guard(["ps"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["argv"][-1], "config")
        execution = records[1]
        self.assertEqual(execution["argv"][-1], "ps")
        self.assertNotIn(str(source_file), execution["argv"])
        self.assertEqual(execution["forwarded_sensitive_environment"], [])
        snapshot = yaml.safe_load(execution["snapshot"])
        service = snapshot["services"]["app"]
        self.assertNotIn("cap_drop", service)
        self.assertEqual(service["security_opt"], ["no-new-privileges"])
        self.assertEqual(service["pids_limit"], 512)
        self.assertNotIn("userns_mode", service)
        self.assertNotIn("http_proxy", service)
        self.assertNotIn("pull_policy", service)
        self.assertNotIn("restart", service)

    def test_explicit_development_pid_limit_is_bounded(self):
        allowed = """services:
  app:
    image: example.invalid/image
    pids_limit: 8192
"""
        result, records, _ = self.run_guard(["ps"], allowed)
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = yaml.safe_load(records[1]["snapshot"])
        self.assertEqual(snapshot["services"]["app"]["pids_limit"], 8192)

        denied = allowed.replace("8192", "32769")
        result, records, _ = self.run_guard(["ps"], denied)
        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertIn("PID limit outside", result.stderr)
        self.assertEqual(len(records), 1)

    def test_provider_receives_the_policy_owned_real_podman_path(self):
        result, records, _ = self.run_guard(["ps"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(records), 2)
        self.assertEqual(
            records[0]["argv"][:3],
            ["--dry-run", "--podman-path", str(FAKE_FRONTEND)],
        )
        self.assertEqual(
            records[1]["argv"][:2],
            ["--podman-path", str(FAKE_FRONTEND)],
        )

        for command in ("help", "version"):
            with self.subTest(command=command):
                result, records, _ = self.run_guard([command], add_compose_file=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(records), 1)
                self.assertEqual(
                    records[0]["argv"][:3],
                    ["--dry-run", "--podman-path", str(FAKE_FRONTEND)],
                )

    def test_provider_paths_cannot_resolve_to_package_entry_points(self):
        cases = (
            {"PODMAN_GUARD_REAL_PODMAN": str(PROJECT_ROOT / "bin" / "podman")},
            {
                "PODMAN_GUARD_COMPOSE_PROVIDER": str(
                    PROJECT_ROOT / "bin" / "podman-compose"
                )
            },
        )
        for environment in cases:
            with self.subTest(environment=environment):
                result, records, _ = self.run_guard(["ps"], extra_env=environment)
                self.assertEqual(result.returncode, 125, result.stderr)
                self.assertEqual(records, [])

    def test_confirmed_up_executes_only_the_reviewed_snapshot(self):
        result, records, source_file = self.run_guard(
            ["up", "--build", "app"], confirmation="y"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(records), 2)
        arguments = records[1]["argv"]
        self.assertIn("--build", arguments)
        self.assertNotIn("--no-build", arguments)
        self.assertNotIn("--pull=never", arguments)
        self.assertNotIn(str(source_file), arguments)
        self.assertLess(arguments.index("up"), arguments.index("--build"))
        self.assertEqual(arguments[-1], "app")
        self.assertIsNotNone(records[1]["snapshot"])

    def test_common_compose_lifecycle_commands_use_reviewed_snapshot(self):
        cases = (
            ["build", "--no-cache", "app"],
            ["up", "-d", "--force-recreate", "--remove-orphans", "app"],
            ["up", "-d", "--renew-anon-volumes", "app"],
            ["down", "--timeout", "10"],
            ["down", "--volumes", "--remove-orphans"],
            ["pause", "app"],
            ["pull", "app"],
            ["restart", "--timeout", "10", "app"],
            ["start", "--wait", "--wait-timeout", "10", "app"],
            ["stop", "--timeout", "10", "app"],
            ["unpause", "app"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result, records, source_file = self.run_guard(
                    arguments, confirmation="y"
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(records), 2)
                self.assertNotIn(str(source_file), records[1]["argv"])
                self.assertEqual(records[1]["argv"][-1], arguments[-1])
                self.assertIsNotNone(records[1]["snapshot"])

    def test_compose_up_replace_explains_the_supported_recreation_command(self):
        cases = (
            ["up", "--replace"],
            ["up", "-d", "--replace", "app"],
            ["up", "app", "--replace"],
            ["up", "--replace=synthetic-secret"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result, records, _ = self.run_guard(arguments)
                self.assertEqual(result.returncode, 125, result.stderr)
                self.assertEqual(records, [])
                self.assertIn("ERROR [input]", result.stderr)
                self.assertIn(
                    "podman-compose up does not support --replace", result.stderr
                )
                self.assertIn("podman compose up --force-recreate", result.stderr)
                self.assertIn("select it with -p NAME", result.stderr)
                self.assertNotIn("UNSUPPORTED", result.stderr)
                self.assertNotIn("synthetic-secret", result.stdout + result.stderr)

    def test_replace_hint_preserves_option_values_and_container_arguments(self):
        cases = (
            ["up", "--build-arg", "MODE=--replace", "app"],
            ["exec", "app", "echo", "--replace"],
            ["run", "--rm", "app", "echo", "--replace"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result, records, _ = self.run_guard(arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(records), 2)
                self.assertEqual(records[1]["argv"][-len(arguments) :], arguments)
                self.assertNotIn("--force-recreate", result.stderr)

    def test_compose_exec_uses_reviewed_snapshot_and_keeps_command_arguments(self):
        arguments = [
            "exec",
            "--user",
            "developer",
            "--workdir=/workspace",
            "--env",
            "APP_MODE=development",
            "app",
            "sh",
            "-lc",
            "printf ready",
        ]

        result, records, source_file = self.run_guard(arguments, confirmation="y")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(records), 2)
        self.assertNotIn(str(source_file), records[1]["argv"])
        self.assertEqual(records[1]["argv"][-len(arguments) :], arguments)
        snapshot = yaml.safe_load(records[1]["snapshot"])
        self.assertEqual(
            snapshot["services"]["app"]["labels"]["io.github.paranoid-podman.policy"],
            "1",
        )

    def test_compose_exec_rejects_privilege_and_ambient_secret_options(self):
        cases = (
            ["exec", "--privileged", "app", "sh"],
            ["exec", "--env", "SSH_AUTH_SOCK=/tmp/agent", "app", "sh"],
            ["exec", "--env", "APP_MODE", "app", "sh"],
            ["exec", "--index", "11", "app", "sh"],
            ["exec", "app"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result, records, _ = self.run_guard(arguments)
                self.assertEqual(result.returncode, 125, result.stderr)
                self.assertEqual(records, [])

    def test_one_off_run_preserves_profile_and_container_command(self):
        config = """services:
  importer:
    build: .
    profiles: [data-build]
    cpus: '1.0'
    mem_limit: 1g
    memswap_limit: 1g
    userns_mode: keep-id:uid=10001,gid=10001
"""

        def prepare(project_dir):
            (project_dir / "Containerfile").write_text(
                "FROM scratch\n", encoding="utf-8"
            )

        arguments = [
            "--profile",
            "data-build",
            "run",
            "--rm",
            "--build",
            "importer",
            "build",
            "--privileged",
            "two words",
        ]
        result, records, _ = self.run_guard(arguments, config, prepare_project=prepare)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("data-build", records[0]["argv"])
        self.assertEqual(records[1]["argv"][-2:], ["build", "importer"])
        self.assertEqual(records[2]["argv"][-6:], ["run", "--rm", *arguments[-4:]])
        service = yaml.safe_load(records[2]["snapshot"])["services"]["importer"]
        self.assertNotIn("profiles", service)
        self.assertEqual(service["userns_mode"], "keep-id:uid=10001,gid=10001")
        self.assertEqual(service["mem_limit"], "1g")
        self.assertEqual(service["security_opt"], ["no-new-privileges"])
        self.assertEqual(service["labels"]["io.github.paranoid-podman.policy"], "1")

    def test_one_off_run_accepts_default_command_and_reviewed_overrides(self):
        cases = [
            ["run", "--rm", "app"],
            ["run", "--no-deps", "--", "app", "sh", "-c", "printf '$HOME'"],
            [
                "run",
                "-T",
                "--name",
                "test-task",
                "--entrypoint",
                "sh -c",
                "-u",
                "10001:10001",
                "-w",
                "/workspace",
                "-e",
                "APP_MODE=dev",
                "-p",
                "8080:80",
                "--service-ports",
                "app",
                "echo ready",
            ],
            ["run", "--detach", "--env=APP_MODE=dev", "app"],
            [
                "run",
                "-eAPP_MODE=dev",
                "-u10001:10001",
                "-w/workspace",
                "-p8080:80",
                "app",
            ],
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result, records, _ = self.run_guard(arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(records[1]["argv"][-len(arguments) :], arguments)
        result, _, _ = self.run_guard(["run", "app", "false"], execution_exit_code=23)
        self.assertEqual(result.returncode, 23)

    def test_run_build_failure_does_not_start_the_task(self):
        result, records, _ = self.run_guard(
            ["run", "--build", "app", "build"], execution_exit_code=19
        )
        self.assertEqual(result.returncode, 19, result.stderr)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[-1]["argv"][-2:], ["build", "app"])

    def test_build_option_inside_container_command_is_preserved(self):
        arguments = ["run", "--rm", "app", "task", "--build"]
        result, records, _ = self.run_guard(arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[-1]["argv"][-len(arguments) :], arguments)

    def test_devpod_compose_hostname_uses_workspace_label_only(self):
        for explicit in (None, "custom-host"):
            config = {
                "services": {
                    "workspace": {
                        "image": "example.invalid/image",
                        "labels": {"devpod.sh/id": "example-workspace"},
                    },
                    "database": {"image": "example.invalid/database"},
                }
            }
            if explicit is not None:
                config["services"]["workspace"]["hostname"] = explicit
            result, records, _ = self.run_guard(["up", "-d"], yaml.safe_dump(config))
            self.assertEqual(result.returncode, 0, result.stderr)
            services = yaml.safe_load(records[-1]["snapshot"])["services"]
            self.assertEqual(
                services["workspace"]["hostname"], explicit or "example-workspace"
            )
            self.assertNotIn("hostname", services["database"])

    def test_denial_highlights_the_service_and_only_relevant_settings(self):
        config = """services:
  database:
    image: example.invalid/database
    ports: ['5432:5432']
  app:
    image: example.invalid/image
    privileged: true
"""

        def prepare(project):
            (project / "compose.yaml").write_text(config, encoding="utf-8")

        result, records, _ = self.run_guard(["up"], config, prepare_project=prepare)
        self.assertEqual(result.returncode, 125)
        self.assertIn("BLOCKED [privilege]", result.stderr)
        self.assertIn("service 'app'", result.stderr)
        self.assertIn("compose.yaml:7", result.stderr)
        self.assertNotIn("compose.yaml:4", result.stderr)
        self.assertEqual(len(records), 1)

    def test_one_off_run_rejects_unsafe_options_and_unknown_service(self):
        cases = [
            ["run"],
            ["run", "--rm=true", "app"],
            ["run", "--privileged", "app"],
            ["run", "--volume", "/:/host", "app"],
            ["run", "--label", "io.github.paranoid-podman.policy=1", "app"],
            ["run", "--env", "SSH_AUTH_SOCK=/synthetic/agent", "app"],
            ["run", "--env", "APP_MODE", "app"],
            ["run", "--env"],
            ["run", "--publish", "65536:80", "app"],
            ["run", "--user", "--privileged", "app"],
            ["run", "--workdir", "relative", "app"],
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result, records, _ = self.run_guard(arguments)
                self.assertEqual(result.returncode, 125, result.stderr)
                self.assertEqual(records, [])
        result, records, _ = self.run_guard(["run", "missing", "build"])
        self.assertEqual(result.returncode, 125)
        self.assertEqual(len(records), 1)

    def test_malformed_resource_limits_and_keep_id_options_are_denied(self):
        cases = [
            ("cpus", True),
            ("cpus", "nan"),
            ("cpus", -1),
            ("cpus", []),
            ("mem_limit", "--privileged"),
            ("mem_limit", True),
            ("mem_limit", -1),
            ("shm_size", "../host"),
            ("ulimits", {"memlock": -1}),
            ("ulimits", {"nofile": True}),
            ("ulimits", {"nofile": {"soft": 2048, "hard": 1024}}),
            ("ulimits", {"nofile": {"soft": 1024, "hard": 2048, "path": "/host"}}),
            ("extra_hosts", ["unreviewed.example:host-gateway"]),
            ("extra_hosts", {"host.docker.internal": "host-gateway\n--privileged"}),
            ("userns_mode", "host"),
            ("userns_mode", "keep-id:"),
            ("userns_mode", "keep-id:uid=-1"),
            ("userns_mode", "keep-id:uid=4294967295"),
            ("userns_mode", "keep-id:uid=999,uid=10001"),
            ("userns_mode", "keep-id:uid=999,gidmapping=0:0:1"),
            ("userns_mode", "keep-id:size=0"),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                model = yaml.safe_load(SAFE_CONFIG)
                model["services"]["app"][key] = value
                result, records, _ = self.run_guard(
                    ["run", "app"], yaml.safe_dump(model)
                )
                self.assertEqual(result.returncode, 125, result.stderr)
                self.assertEqual(len(records), 1)

    def test_active_compose_lifecycle_rejects_legacy_project_containers(self):
        container_id = "a" * 64
        environment = {
            "PARANOID_COMPOSE_TEST_PROJECT_CONTAINERS": json.dumps([container_id]),
            "PARANOID_COMPOSE_TEST_POLICY_VERSIONS": json.dumps({container_id: "0"}),
        }
        active_forms = (
            ["up"],
            ["run", "--rm", "app", "sh"],
            ["start", "app"],
            ["restart", "app"],
            ["unpause", "app"],
            ["exec", "app", "sh"],
        )
        for arguments in active_forms:
            with self.subTest(arguments=arguments):
                result, records, _ = self.run_guard(
                    arguments,
                    confirmation="y",
                    extra_env=environment,
                )
                self.assertEqual(result.returncode, 125, result.stderr)
                self.assertIn("not created by the current guard policy", result.stderr)
                self.assertEqual(len(records), 1)

        result, records, _ = self.run_guard(
            ["down"], confirmation="y", extra_env=environment
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(records), 2)

    def test_active_compose_lifecycle_accepts_current_project_containers(self):
        container_id = "b" * 64
        environment = {
            "PARANOID_COMPOSE_TEST_PROJECT_CONTAINERS": json.dumps([container_id])
        }

        result, records, _ = self.run_guard(
            ["exec", "app", "sh"],
            confirmation="y",
            extra_env=environment,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(records), 2)

    def test_active_compose_lifecycle_rejects_another_installation(self):
        container_id = "c" * 64
        environment = {
            "PARANOID_COMPOSE_TEST_PROJECT_CONTAINERS": json.dumps([container_id]),
            "PARANOID_COMPOSE_TEST_INSTALLATION_ID": "b" * 64,
        }

        result, records, _ = self.run_guard(
            ["exec", "app", "sh"],
            confirmation="y",
            extra_env=environment,
        )

        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertIn("not created by the current guard policy", result.stderr)
        self.assertEqual(len(records), 1)

    def test_literal_secret_build_args_are_denied(self):
        cases = (["build", "--build-arg", "APP_API_TOKEN=literal"],)
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result, records, _ = self.run_guard(arguments)
                self.assertEqual(result.returncode, 125, result.stderr)
                self.assertEqual(records, [])

    def test_project_local_build_is_normalized_and_preserved(self):
        config = """services:
  app:
    image: example.invalid/image
    build:
      context: .
      dockerfile: Dockerfile.dev
      args:
        APP_MODE: development
      target: runtime
"""

        def prepare(project_dir):
            (project_dir / "compose.yaml").write_text(config, encoding="utf-8")
            (project_dir / "Dockerfile.dev").write_text(
                "FROM scratch AS runtime\n", encoding="utf-8"
            )

        result, records, source_file = self.run_guard(
            ["ps"], config, prepare_project=prepare
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = yaml.safe_load(records[1]["snapshot"])
        build = snapshot["services"]["app"]["build"]
        self.assertEqual(build["context"], str(source_file.parent))
        self.assertEqual(
            build["dockerfile"], str(source_file.parent / "Dockerfile.dev")
        )
        self.assertEqual(build["args"], {"APP_MODE": "development"})
        self.assertEqual(build["target"], "runtime")

    def test_build_paths_cannot_leave_the_project(self):
        config = """services:
  app:
    image: example.invalid/image
    build:
      context: ../outside
"""

        def prepare(project_dir):
            outside = project_dir.parent / "outside"
            outside.mkdir()
            (outside / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            (project_dir / "compose.yaml").write_text(config, encoding="utf-8")

        result, records, _ = self.run_guard(["ps"], config, prepare_project=prepare)

        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertIn("build path outside the project", result.stderr)
        self.assertEqual(len(records), 1)

    def test_build_context_contents_are_left_to_the_container_builder(self):
        config = """services:
  app:
    image: example.invalid/image
    build: .
"""

        def prepare(project_dir):
            (project_dir / "compose.yaml").write_text(config, encoding="utf-8")
            (project_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            (project_dir / ".env").write_text(
                "APP_MODE=development\n", encoding="utf-8"
            )
            (project_dir / ".git").mkdir()
            (project_dir / ".dockerignore").write_text(".env\n", encoding="utf-8")

        result, records, _ = self.run_guard(["ps"], config, prepare_project=prepare)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(records), 2)

    def test_cors_credential_switches_are_allowed_in_mapping_and_list_forms(self):
        for environment in (
            '      CORS_CREDENTIALS: "false"\n      CORS_ALLOW_CREDENTIALS: "true"\n',
            "      - CORS_CREDENTIALS=false\n      - CORS_ALLOW_CREDENTIALS=true\n",
        ):
            config = (
                "services:\n  app:\n    image: example.invalid/image\n"
                "    environment:\n" + environment
            )

            def prepare(project, source=config):
                (project / "compose.yaml").write_text(source, encoding="utf-8")

            with self.subTest(environment=environment):
                resolved = yaml.safe_load(config)
                resolved["services"]["app"]["environment"] = {
                    "CORS_CREDENTIALS": "false",
                    "CORS_ALLOW_CREDENTIALS": "true",
                }
                result, records, _ = self.run_guard(
                    ["up", "--build"], yaml.safe_dump(resolved), prepare_project=prepare
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(records), 2)
                self.assertEqual(
                    yaml.safe_load(records[-1]["snapshot"])["services"]["app"][
                        "environment"
                    ],
                    {"CORS_CREDENTIALS": "false", "CORS_ALLOW_CREDENTIALS": "true"},
                )

    def test_build_ignore_descendant_exception_blocks_compose_execution(self):
        config = "services:\n  app:\n    image: example.invalid/image\n    build: .\n"

        def prepare(project):
            (project / "compose.yaml").write_text(config, encoding="utf-8")
            (project / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            (project / ".ssh").mkdir()
            (project / ".containerignore").write_text(
                "*\n!.ssh/project-key\n", encoding="utf-8"
            )

        result, records, _ = self.run_guard(
            ["up", "--build"], config, prepare_project=prepare
        )
        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertIn("sensitive build-context", result.stderr)
        self.assertEqual(len(records), 1, "denied build reached provider execution")

    def test_dotenv_templates_do_not_block_a_project_build(self):
        config = """services:
  app:
    image: example.invalid/image
    build: .
"""

        def prepare(project_dir):
            (project_dir / "compose.yaml").write_text(config, encoding="utf-8")
            (project_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            (project_dir / ".env.example").write_text(
                "APP_API_TOKEN=replace-me\n", encoding="utf-8"
            )

        result, records, _ = self.run_guard(["ps"], config, prepare_project=prepare)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(records), 2)

    def test_dockerfile_literal_secrets_are_denied_without_printing_values(self):
        marker = "synthetic-dockerfile-secret"
        config = """services:
  app:
    image: example.invalid/image
    build: .
"""
        cases = {
            "ARG": f"ARG APP_API_TOKEN={marker}",
            "ENV": f"ENV APP_API_TOKEN={marker}",
            "LABEL": f"LABEL APP_API_TOKEN={marker}",
            "RUN assignment": f"RUN APP_API_TOKEN={marker} command",
            "RUN env assignment": f"RUN env APP_API_TOKEN={marker} command",
            "RUN export assignment": f"RUN export APP_API_TOKEN={marker} command",
        }

        for label, instruction in cases.items():
            with self.subTest(label=label):

                def prepare(project_dir, source_instruction=instruction):
                    (project_dir / "compose.yaml").write_text(config, encoding="utf-8")
                    (project_dir / "Dockerfile").write_text(
                        f"FROM scratch\n{source_instruction}\n",
                        encoding="utf-8",
                    )

                result, records, _ = self.run_guard(
                    ["ps"], config, prepare_project=prepare
                )

                self.assertEqual(result.returncode, 125, result.stderr)
                self.assertIn("literal secret", result.stderr)
                self.assertNotIn(marker, result.stdout + result.stderr)
                self.assertEqual(len(records), 1)

    def test_dockerfile_base_image_is_left_to_the_container_builder(self):
        config = """services:
  app:
    image: example.invalid/image
    build: .
"""

        def prepare(project_dir):
            (project_dir / "compose.yaml").write_text(config, encoding="utf-8")
            (project_dir / "Dockerfile").write_text(
                "FROM docker-archive:/tmp/untrusted-image.tar\n",
                encoding="utf-8",
            )

        result, records, _ = self.run_guard(["ps"], config, prepare_project=prepare)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("/tmp/untrusted-image.tar", result.stderr)
        self.assertEqual(len(records), 2)

    def test_interpolated_dockerfile_secret_is_allowed(self):
        marker = "synthetic-interpolated-build-secret"
        source = """services:
  app:
    image: example.invalid/image
    build:
      context: .
      args:
        APP_API_TOKEN: "${APP_API_TOKEN:?required}"
"""
        resolved = f"""services:
  app:
    image: example.invalid/image
    build:
      context: .
      args:
        APP_API_TOKEN: {marker}
"""

        def prepare(project_dir):
            (project_dir / "compose.yaml").write_text(source, encoding="utf-8")
            (project_dir / "Dockerfile").write_text(
                "FROM scratch\nARG APP_API_TOKEN\nENV APP_API_TOKEN=${APP_API_TOKEN}\n",
                encoding="utf-8",
            )
            (project_dir / ".env").write_text(
                f"APP_API_TOKEN={marker}\n", encoding="utf-8"
            )
            (project_dir / ".dockerignore").write_text(".env\n", encoding="utf-8")

        result, records, _ = self.run_guard(["ps"], resolved, prepare_project=prepare)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(records), 2)
        self.assertNotIn(marker, result.stdout + result.stderr)

    def test_literal_secret_in_compose_build_args_is_denied_before_provider(self):
        marker = "synthetic-build-arg-secret"
        config = f"""services:
  app:
    image: example.invalid/image
    build:
      context: .
      args:
        APP_API_TOKEN: {marker}
"""

        def prepare(project_dir):
            (project_dir / "compose.yaml").write_text(config, encoding="utf-8")
            (project_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")

        result, records, _ = self.run_guard(["ps"], config, prepare_project=prepare)

        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertIn("literal secret in Compose source", result.stderr)
        self.assertNotIn(marker, result.stdout + result.stderr)
        self.assertEqual(records, [])

    def test_generic_extensions_are_removed_but_provider_extensions_are_denied(self):
        generic = """x-project-defaults:
  APP_MODE: development
services:
  app:
    image: example.invalid/image
    x-project-note: ignored
"""

        def prepare_generic(project_dir):
            (project_dir / "compose.yaml").write_text(generic, encoding="utf-8")

        result, records, _ = self.run_guard(
            ["ps"], generic, prepare_project=prepare_generic
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = yaml.safe_load(records[1]["snapshot"])
        self.assertNotIn("x-project-defaults", snapshot)
        self.assertNotIn("x-project-note", snapshot["services"]["app"])

        provider_specific = """services:
  app:
    image: example.invalid/image
    x-podman.some-future-option: true
"""

        def prepare_provider_specific(project_dir):
            (project_dir / "compose.yaml").write_text(
                provider_specific, encoding="utf-8"
            )

        result, records, _ = self.run_guard(
            ["ps"], provider_specific, prepare_project=prepare_provider_specific
        )
        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertIn("provider-specific x-podman", result.stderr)
        self.assertEqual(records, [])

    def test_ordinary_labels_and_bounded_logging_are_preserved(self):
        config = """services:
  app:
    image: example.invalid/image
    labels:
      org.example.component: api
    logging:
      driver: k8s-file
      options:
        max-size: 10m
        max-file: 3
"""

        result, records, _ = self.run_guard(["ps"], config)

        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = yaml.safe_load(records[1]["snapshot"])
        service = snapshot["services"]["app"]
        self.assertEqual(service["labels"]["org.example.component"], "api")
        self.assertEqual(service["labels"]["io.github.paranoid-podman.policy"], "1")
        self.assertEqual(
            service["labels"]["io.github.paranoid-podman.installation"],
            "a" * 64,
        )
        self.assertEqual(service["logging"]["driver"], "k8s-file")

    def test_reserved_labels_and_host_targeting_log_options_are_denied(self):
        cases = (
            """services:
  app:
    image: example.invalid/image
    labels:
      io.podman.compose.project: another-project
""",
            """services:
  app:
    image: example.invalid/image
    labels:
      io.github.paranoid-podman.policy: 1
""",
            """services:
  app:
    image: example.invalid/image
    logging:
      driver: k8s-file
      options:
        path: /tmp/untrusted.log
""",
        )
        for config in cases:
            with self.subTest(config=config):
                result, records, _ = self.run_guard(["ps"], config)
                self.assertEqual(result.returncode, 125, result.stderr)
                self.assertIn("blocked resolved configuration", result.stderr)
                self.assertEqual(len(records), 1)

    def test_provider_exit_code_is_preserved_after_confirmation(self):
        result, records, _ = self.run_guard(
            ["up"], confirmation="y", execution_exit_code=23
        )

        self.assertEqual(result.returncode, 23)
        self.assertEqual(len(records), 2)

    def test_provider_exit_code_is_preserved_without_a_tty(self):
        result, records, _ = self.run_guard(["up"], execution_exit_code=23)

        self.assertEqual(result.returncode, 23)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(records), 2)

    def test_terminal_input_is_not_consumed_by_a_wrapper_prompt(self):
        result, records, _ = self.run_guard(["up"], confirmation="n")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(records), 2)

    def test_project_bind_is_canonical_and_protected_without_creating_paths(self):
        def prepare(project_dir):
            (project_dir / ".git").mkdir()
            (project_dir / ".env").write_text(
                "APP_MODE=development\n", encoding="utf-8"
            )
            (project_dir / "Dockerfile.dev").write_text(
                "FROM scratch\n", encoding="utf-8"
            )

        def config(project_dir):
            return """services:
  app:
    image: example.invalid/image
    volumes:
      - .:/workspace
"""

        result, records, source_file = self.run_guard(
            ["ps"], config, prepare_project=prepare
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = yaml.safe_load(records[1]["snapshot"])
        mounts = snapshot["services"]["app"]["volumes"]
        self.assertEqual(mounts[0]["source"], str(source_file.parent))
        protected_targets = {
            mount["target"] for mount in mounts if mount.get("read_only")
        }
        self.assertIn("/workspace/.git", protected_targets)
        self.assertIn("/workspace/.env", protected_targets)
        self.assertNotIn("/workspace/Dockerfile.dev", protected_targets)
        self.assertNotIn("/workspace/compose.yaml", protected_targets)

    def test_individual_external_bind_is_allowed_and_configs_stay_read_only(self):
        config = """services:
  app:
    image: example.invalid/image
    volumes:
      - ../shared:/shared
"""

        def prepare(project_dir):
            shared = project_dir.parent / "shared"
            shared.mkdir()
            (shared / ".devcontainer.json").write_text("{}\n", encoding="utf-8")

        result, records, source_file = self.run_guard(
            ["ps"], config, prepare_project=prepare
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = yaml.safe_load(records[1]["snapshot"])
        mounts = snapshot["services"]["app"]["volumes"]
        self.assertEqual(mounts[0]["source"], str(source_file.parent.parent / "shared"))
        self.assertTrue(
            any(
                mount.get("target") == "/shared/.devcontainer.json"
                and mount.get("read_only")
                for mount in mounts
            )
        )

    def test_broad_compose_host_binds_are_denied(self):
        # The policy protects the OS account home, independent of $HOME.
        for source in ("/", "..", pwd.getpwuid(os.getuid()).pw_dir):
            with self.subTest(source=source):
                config = f"""services:
  app:
    image: example.invalid/image
    volumes:
      - type: bind
        source: {source}
        target: /host
"""
                result, records, _ = self.run_guard(["ps"], config)
                self.assertEqual(result.returncode, 125, result.stderr)
                self.assertIn("blocked resolved configuration", result.stderr)
                self.assertEqual(len(records), 1)

    def test_external_bind_requires_exact_terminal_confirmation(self):
        config = """services:
  app:
    image: example.invalid/image
    volumes:
      - ../shared:/shared:ro
"""

        def prepare(project):
            (project.parent / "shared").mkdir()
            (project.parent / "shared" / ".devcontainer.json").write_text(
                "{}\n", encoding="utf-8"
            )
            (project / "compose.yaml").write_text(config, encoding="utf-8")

        for answer in (None, "", "n", "yes", "Y", " y", "y"):
            with self.subTest(answer=answer):
                result, records, compose_file = self.run_guard(
                    ["up"], config, confirmation=answer, prepare_project=prepare
                )
                approved = answer == "y"
                self.assertEqual(
                    result.returncode, 0 if approved else 125, result.stderr
                )
                self.assertEqual(len(records), 2 if approved else 1)
                self.assertIn("[warning]", result.stderr)
                self.assertIn(str(compose_file.parent.parent / "shared"), result.stderr)
                self.assertIn(f'"{compose_file}":5', result.stderr)
                self.assertIn("read-only", result.stderr)
                self.assertEqual(result.stderr.count("Host path:"), 1)
                self.assertNotIn("\033[", result.stderr)

    def test_external_mount_prompt_only_applies_to_services_being_created(self):
        config = """services:
  app:
    image: example.invalid/image
  worker:
    image: example.invalid/image
    depends_on:
      storage: {condition: service_started}
  storage:
    image: example.invalid/image
    volumes: ['../shared:/shared']
"""

        def prepare(project):
            (project.parent / "shared").mkdir()
            (project / "compose.yaml").write_text(config, encoding="utf-8")

        cases = (
            (["up"], True),
            (["up", "app"], False),
            (["up", "worker"], True),
            (["up", "--no-deps", "worker"], False),
            (["up", "--no-start", "storage"], True),
            (["up", "--scale", "storage=0", "storage"], False),
            (["run", "--rm", "worker", "echo", "--no-deps"], True),
            (["run", "--entrypoint", "--no-deps", "worker"], True),
            (["run", "--no-deps", "worker"], False),
            (["run", "app", "echo", "storage"], False),
            (["build"], False),
            (["config", "--quiet"], False),
            (["ps"], False),
            (["logs"], False),
            (["exec", "storage", "sh"], False),
            (["start", "storage"], False),
            (["restart", "storage"], False),
            (["stop"], False),
            (["down", "--volumes"], False),
        )
        for arguments, requires_confirmation in cases:
            with self.subTest(arguments=arguments):
                result, records, _ = self.run_guard(
                    arguments, config, prepare_project=prepare
                )
                self.assertEqual(
                    result.returncode,
                    125 if requires_confirmation else 0,
                    result.stderr,
                )
                self.assertEqual("[warning]" in result.stderr, requires_confirmation)
                if requires_confirmation:
                    self.assertEqual(len(records), 1)

    def test_external_bind_confirmation_cannot_override_a_policy_denial(self):
        config = """services:
  app:
    image: example.invalid/image
    volumes: ['/etc:/host']
"""

        def prepare(project):
            (project / "compose.yaml").write_text(config, encoding="utf-8")

        result, records, _ = self.run_guard(
            ["up"], config, confirmation="y", prepare_project=prepare
        )
        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertNotIn("Allow these mounts", result.stderr)
        self.assertEqual(len(records), 1)

    def test_external_bind_prompt_is_shared_by_compose_entry_points(self):
        config = """services:
  app:
    image: example.invalid/image
    volumes: ['../shared:/shared']
"""

        def prepare(project):
            (project.parent / "shared").mkdir()
            (project / "compose.yaml").write_text(config, encoding="utf-8")

        for guard, prefix in COMPOSE_ENTRY_POINTS:
            with self.subTest(guard=guard):
                result, records, _ = self.run_guard(
                    ["up"], config, guard=guard, prefix=prefix, prepare_project=prepare
                )
                self.assertEqual(result.returncode, 125, result.stderr)
                self.assertIn("[warning]", result.stderr)
                self.assertEqual(len(records), 1)

    def test_compose_file_binds_are_writable_except_inside_devcontainer(self):
        config = """services:
  app:
    image: example.invalid/image
    volumes:
      - ./Dockerfile:/Dockerfile:rw
      - ./compose.yaml:/compose.yaml:rw
      - ./.devcontainer/Dockerfile:/dev-Dockerfile:rw
      - ./.devcontainer/compose.yaml:/dev-compose.yaml:rw
      - ./.devcontainer.json:/devcontainer.json:rw
"""

        def prepare(project):
            (project / ".devcontainer").mkdir()
            for name in (
                "Dockerfile",
                ".devcontainer/Dockerfile",
                ".devcontainer/compose.yaml",
                ".devcontainer.json",
            ):
                (project / name).write_text("synthetic\n", encoding="utf-8")

        result, records, _ = self.run_guard(["up"], config, prepare_project=prepare)
        self.assertEqual(result.returncode, 0, result.stderr)
        volumes = yaml.safe_load(records[-1]["snapshot"])["services"]["app"]["volumes"]
        self.assertEqual(
            [volume["read_only"] for volume in volumes],
            [False, False, True, True, True],
        )

    def test_external_compose_bind_cannot_request_selinux_relabeling(self):
        config = """services:
  app:
    image: example.invalid/image
    volumes:
      - ../shared:/shared:z
"""

        def prepare(project_dir):
            (project_dir.parent / "shared").mkdir()

        result, records, _ = self.run_guard(["ps"], config, prepare_project=prepare)
        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertIn("SELinux relabel outside the project", result.stderr)
        self.assertEqual(len(records), 1)

    def test_nested_repository_metadata_is_protected(self):
        config = """services:
  app:
    image: example.invalid/image
    volumes:
      - .:/workspace
"""

        def prepare(project_dir):
            (project_dir / "packages" / "app" / ".git").mkdir(parents=True)

        result, records, _ = self.run_guard(["ps"], config, prepare_project=prepare)
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = yaml.safe_load(records[1]["snapshot"])
        targets = {
            mount["target"]
            for mount in snapshot["services"]["app"]["volumes"]
            if mount.get("read_only")
        }
        self.assertIn("/workspace/packages/app/.git", targets)

    def test_mount_error_locations_exclude_unrelated_settings(self):
        config = """services:
  app:
    image: example.invalid/image
    restart: always
    environment:
      PUBLIC_SETTING: synthetic-hidden-value
    volumes:
      - /etc:/host-etc
"""

        def prepare(project_dir):
            (project_dir / "compose.yaml").write_text(config, encoding="utf-8")

        result, records, _ = self.run_guard(["up"], config, prepare_project=prepare)
        self.assertEqual(result.returncode, 125)
        self.assertEqual(len(records), 1)
        self.assertIn("services.<service>.volumes", result.stderr)
        for hidden in (".environment", ".restart", "synthetic-hidden-value"):
            self.assertNotIn(hidden, result.stderr)

    def test_git_protection_can_be_disabled_without_unprotecting_devcontainer(self):
        config = """services:
  app:
    image: example.invalid/image
    volumes:
      - .:/workspace
"""

        def prepare(project_dir):
            (project_dir / ".git").mkdir()
            (project_dir / ".devcontainer").mkdir()
            (project_dir / ".devcontainer.json").write_text("{}\n", encoding="utf-8")

        result, records, _ = self.run_guard(
            ["ps"],
            config,
            prepare_project=prepare,
            extra_env={"PODMAN_GUARD_PROTECT_GIT": "0"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = yaml.safe_load(records[1]["snapshot"])
        read_only_targets = {
            mount["target"]
            for mount in snapshot["services"]["app"]["volumes"]
            if mount.get("read_only")
        }
        self.assertNotIn("/workspace/.git", read_only_targets)
        self.assertIn("/workspace/.devcontainer", read_only_targets)
        self.assertIn("/workspace/.devcontainer.json", read_only_targets)
        self.assertNotIn("/workspace/compose.yaml", read_only_targets)

    def test_read_only_project_bind_cannot_be_overridden_below_git(self):
        config = """services:
  app:
    image: example.invalid/image
    volumes:
      - type: bind
        source: .
        target: /workspace
        read_only: true
        bind:
          create_host_path: false
      - type: tmpfs
        target: /workspace/.git/objects
"""

        def prepare(project_dir):
            (project_dir / ".git").mkdir()

        result, records, _ = self.run_guard(["ps"], config, prepare_project=prepare)
        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertIn("overrides a protected project path", result.stderr)
        self.assertEqual(len(records), 1)

    def test_matching_read_only_protected_bind_is_not_an_override(self):
        def prepare(project):
            (project / ".git").mkdir()
            (project / "ordinary").mkdir()

        for source in ("./.git", "./ordinary"):
            for parent_mode in ("rw", "ro"):
                model = {
                    "services": {
                        "app": {
                            "image": "example.invalid/image",
                            "volumes": [
                                f".:/workspace:{parent_mode}",
                                f"{source}:/workspace/.git:ro",
                            ],
                        }
                    }
                }
                result, records, _ = self.run_guard(
                    ["up"], yaml.safe_dump(model), prepare_project=prepare
                )
                if source == "./ordinary":
                    self.assertEqual(result.returncode, 125, result.stderr)
                    self.assertEqual(len(records), 1)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    volumes = yaml.safe_load(records[-1]["snapshot"])["services"][
                        "app"
                    ]["volumes"]
                    mounts = [
                        volume
                        for volume in volumes
                        if volume["target"] == "/workspace/.git"
                    ]
                    self.assertEqual(len(mounts), 1)
                    self.assertTrue(mounts[0]["read_only"])

    def test_bind_inside_protected_directory_is_forced_read_only(self):
        config = """services:
  app:
    image: example.invalid/image
    volumes:
      - type: bind
        source: ./.git/config
        target: /git-config
        bind:
          create_host_path: false
"""

        def prepare(project_dir):
            git_dir = project_dir / ".git"
            git_dir.mkdir()
            (git_dir / "config").write_text("synthetic\n", encoding="utf-8")

        result, records, _ = self.run_guard(["ps"], config, prepare_project=prepare)
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = yaml.safe_load(records[1]["snapshot"])
        self.assertTrue(snapshot["services"]["app"]["volumes"][0]["read_only"])

    def test_missing_protected_paths_are_not_created(self):
        def config(project_dir):
            return """services:
  app:
    image: example.invalid/image
    volumes:
      - .:/workspace
"""

        observed = {}

        def prepare(project_dir):
            observed["project"] = project_dir

        result, records, _ = self.run_guard(["ps"], config, prepare_project=prepare)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((observed["project"] / ".git").exists())
        self.assertFalse((observed["project"] / ".devcontainer").exists())
        snapshot = yaml.safe_load(records[1]["snapshot"])
        targets = {mount["target"] for mount in snapshot["services"]["app"]["volumes"]}
        self.assertEqual(targets, {"/workspace"})

    def test_published_addresses_and_ports_are_preserved_in_the_execution_snapshot(
        self,
    ):
        cases = [
            ("127.0.0.1:8080:80", "127.0.0.1:8080:80"),
            ("8080:80", "8080:80"),
            ("5353:53/udp", "5353:53/udp"),
            ("0.0.0.0:8080:80", "0.0.0.0:8080:80"),
            ("192.0.2.1:8080:80", "192.0.2.1:8080:80"),
            ("[::]:8080:80", "[::]:8080:80"),
            ("[2001:db8::1]:8080:80", "[2001:db8::1]:8080:80"),
            ("127.0.0.1::80", "127.0.0.1::80"),
            ("8080-8081:80-81", "8080-8081:80-81"),
            ("80", "80"),
            (80, 80),
            (
                {"published": "8080", "target": 80},
                {"published": "8080", "target": 80},
            ),
            (
                {"host_ip": "127.0.0.1", "published": 8080, "target": "80"},
                {"host_ip": "127.0.0.1", "published": 8080, "target": "80"},
            ),
            (
                {"host_ip": "0.0.0.0", "published": 8080, "target": 80},  # noqa: S104 - fixture only
                {"host_ip": "0.0.0.0", "published": 8080, "target": 80},  # noqa: S104 - fixture only
            ),
            (
                {"host_ip": "::", "published": "8080", "target": 80},
                {"host_ip": "[::]", "published": "8080", "target": 80},
            ),
            (
                {"published": "8080-8090", "target": 80},
                {"published": "8080-8090", "target": 80},
            ),
            ({"target": 80}, {"target": 80}),
        ]
        for port, expected in cases:
            with self.subTest(port=port):
                config = yaml.safe_load(SAFE_CONFIG)
                config["services"]["app"]["ports"] = [port]
                result, records, _ = self.run_guard(["ps"], yaml.safe_dump(config))
                self.assertEqual(result.returncode, 0, result.stderr)
                snapshot = yaml.safe_load(records[1]["snapshot"])
                self.assertEqual(snapshot["services"]["app"]["ports"], [expected])

    def test_malformed_ports_never_reach_execution(self):
        ports = [
            "999.0.0.0:8080:80",
            "[not-an-ip]:8080:80",
            ":8080:80",
            "8081-8080:80-81",
            "8080-8082:80-81",
            "0:80",
            "65536:80",
            "8080:0",
            "8080:80/sctp",
            {"host_ip": "host.example.invalid", "published": 8080, "target": 80},
            {"host_ip": True, "published": 8080, "target": 80},
            {"published": True, "target": 80},
            {"published": "8081-8080", "target": 80},
            {"published": "8080", "target": 65536},
            {},
            True,
        ]
        for port in ports:
            with self.subTest(port=port):
                config = yaml.safe_load(SAFE_CONFIG)
                config["services"]["app"]["ports"] = [port]
                result, records, _ = self.run_guard(["ps"], yaml.safe_dump(config))
                self.assertEqual(result.returncode, 125)
                self.assertEqual(len(records), 1)

    def test_named_internal_bridge_and_build_without_image_are_preserved(self):
        config = """services:
  app:
    build: .
    ports: ["18055:8055"]
    networks:
      app-net:
        aliases: [app-internal]
networks:
  app-net:
    driver: bridge
    internal: true
    name: app-dev
"""

        def prepare(project_dir):
            (project_dir / "compose.yaml").write_text(config, encoding="utf-8")
            (project_dir / "Containerfile").write_text(
                "FROM scratch\n", encoding="utf-8"
            )

        result, records, _ = self.run_guard(
            ["up", "--build"], config, prepare_project=prepare
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = yaml.safe_load(records[1]["snapshot"])
        self.assertEqual(snapshot["networks"], yaml.safe_load(config)["networks"])
        app = snapshot["services"]["app"]
        self.assertNotIn("image", app)
        self.assertTrue(Path(app["build"]["context"]).is_absolute())
        self.assertEqual(app["ports"], ["18055:8055"])
        self.assertEqual(app["networks"], {"app-net": {"aliases": ["app-internal"]}})

    def test_build_without_image_still_requires_safe_build_inputs(self):
        def prepare(project_dir):
            (project_dir / "Containerfile").write_text(
                "FROM scratch\n", encoding="utf-8"
            )
            (project_dir / ".env").write_text(
                "APP_TOKEN=synthetic-build-secret\n", encoding="utf-8"
            )

        for service in ({"build": "."}, {}, {"image": "", "build": "."}):
            with self.subTest(service=service):
                result, records, _ = self.run_guard(
                    ["build"],
                    yaml.safe_dump({"services": {"app": service}}),
                    prepare_project=prepare,
                )
                self.assertEqual(result.returncode, 125)
                self.assertNotIn(
                    "synthetic-build-secret", result.stdout + result.stderr
                )
                self.assertEqual(len(records), 1)

    def test_unsafe_network_definitions_remain_denied(self):
        definitions = [
            {"name": "host"},
            {"name": "none"},
            {"name": "pasta"},
            {"name": "bridge"},
            {"name": "container:other"},
            {"name": "ns:/tmp/ns"},
            {"name": "--help"},
            {"name": "app,host"},
            {"name": None},
            {"name": True},
            {"name": "app-dev", "external": True},
            {"name": "app-dev", "driver": "macvlan"},
            {"name": "app-dev", "internal": "true"},
            {"name": "app-dev", "driver_opts": {"mode": "unmanaged"}},
        ]
        for definition in definitions:
            with self.subTest(definition=definition):
                model = yaml.safe_load(SAFE_CONFIG)
                model["networks"] = {"app-net": definition}
                result, records, _ = self.run_guard(["up"], yaml.safe_dump(model))
                self.assertEqual(result.returncode, 125)
                self.assertEqual(len(records), 1)

    def test_existing_explicit_network_must_match_reviewed_bridge_settings(self):
        model = yaml.safe_load(SAFE_CONFIG)
        model["services"]["app"]["networks"] = ["app-net"]
        model["networks"] = {"app-net": {"name": "app-dev", "internal": True}}
        cases = [
            ({}, 0, 0),
            ({"app-dev": "bridge\ttrue"}, 0, 0),
            ({"app-dev": "bridge\tfalse"}, 0, 125),
            ({"app-dev": "macvlan\ttrue"}, 0, 125),
            ({"app-dev": "malformed"}, 0, 125),
            ({}, 125, 125),
        ]
        for networks, query_exit, expected in cases:
            with self.subTest(networks=networks, query_exit=query_exit):
                result, records, _ = self.run_guard(
                    ["up"],
                    yaml.safe_dump(model),
                    extra_env={
                        "PARANOID_COMPOSE_TEST_NETWORKS": json.dumps(networks),
                        "PARANOID_COMPOSE_TEST_NETWORK_EXIT_CODE": str(query_exit),
                    },
                )
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(len(records), 2 if expected == 0 else 1)

    def test_network_error_locations_exclude_unrelated_service_settings(self):
        config = """services:
  app:
    image: example.invalid/image
    environment:
      PUBLIC_SETTING: synthetic-hidden-value
    restart: always
    networks: [app-net]
networks:
  app-net:
    driver: macvlan
"""

        def prepare(project_dir):
            (project_dir / "compose.yaml").write_text(config, encoding="utf-8")

        result, _, _ = self.run_guard(["up"], config, prepare_project=prepare)
        self.assertEqual(result.returncode, 125)
        self.assertIn("networks.<resource>.driver", result.stderr)
        for hidden in ("synthetic-hidden-value", ".environment", ".restart"):
            self.assertNotIn(hidden, result.stderr)

    def test_config_never_prints_values(self):
        result, records, _ = self.run_guard(["config"])
        self.assertEqual(result.returncode, 125)
        self.assertIn("resolved values are never printed", result.stderr)
        self.assertEqual(records, [])

        result, records, _ = self.run_guard(["config", "--quiet"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(records), 1)

        result, records, _ = self.run_guard(["config", "--services"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "app\n")
        self.assertEqual(len(records), 1)

    def test_devpod_project_metadata_query_is_forwarded_narrowly(self):
        project_name = "default-de-1b1f7"
        result, records, _ = self.run_guard(
            [
                "--project-name",
                project_name,
                "ls",
                "-a",
                "--filter",
                f"name={project_name}",
                "--format",
                "json",
            ],
            add_compose_file=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "[]\n")
        self.assertEqual(len(records), 1)
        self.assertEqual(
            records[0]["argv"],
            [
                "--podman-path",
                str(FAKE_FRONTEND),
                "-p",
                project_name,
                "ls",
                "-a",
                "--filter",
                f"name={project_name}",
                "--format",
                "json",
            ],
        )

    def test_devpod_global_inputs_keep_the_reviewed_snapshot_boundary(self):
        project_name = "default-de-1b1f7"

        def prepare(project_dir):
            (project_dir / ".env").write_text(
                "APP_MODE=development\nCOMPOSE_PROJECT_NAME=ignored\n",
                encoding="utf-8",
            )
            override_dir = project_dir.parent / ".docker-compose"
            override_dir.mkdir()
            override_file = (
                override_dir / "docker-compose.devcontainer.containerFeatures-10.yml"
            )
            override_file.write_text(SAFE_CONFIG, encoding="utf-8")

        result, records, source_file = self.run_guard(
            [
                "--env-file",
                ".env",
                "-f",
                "../.docker-compose/docker-compose.devcontainer.containerFeatures-10.yml",
                "--project-name",
                project_name,
                "up",
                "-d",
            ],
            prepare_project=prepare,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(records), 2)
        self.assertIn("APP_MODE=development", records[0]["env_file"])
        self.assertNotIn("COMPOSE_PROJECT_NAME", records[0]["env_file"])
        snapshot = yaml.safe_load(records[1]["snapshot"])
        self.assertEqual(snapshot["name"], project_name)
        self.assertNotIn(str(source_file), records[1]["argv"])
        self.assertIn(project_name, records[1]["argv"])

    def test_devpod_compose_build_pull_flag_uses_reviewed_snapshot(self):
        project_name = "default-de-1b1f7"
        result, records, source_file = self.run_guard(
            ["--project-name", project_name, "build", "--pull", "app"]
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(records), 2)
        self.assertNotIn(str(source_file), records[1]["argv"])
        self.assertIn("--pull", records[1]["argv"])
        self.assertIsNotNone(records[1]["snapshot"])

    def test_noninteractive_build_does_not_read_the_test_runners_terminal(self):
        # Reproduce a user running unittest in a terminal without supplying y.
        # The nested test must still exercise the non-interactive guard path.
        master_fd, slave_fd = pty.openpty()
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "tests.compose.test_cli.ComposeGuardIntegrationTests."
                    "test_devpod_compose_build_pull_flag_uses_reviewed_snapshot",
                    "-v",
                ],
                cwd=PROJECT_ROOT,
                stdin=slave_fd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            os.close(slave_fd)
            slave_fd = -1
            try:
                stdout, stderr = process.communicate(timeout=15)
            except BaseException:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=5)
                raise
        finally:
            os.close(master_fd)
            if slave_fd >= 0:
                os.close(slave_fd)

        self.assertEqual(process.returncode, 0, stdout + stderr)
        self.assertIn("Ran 1 test", stderr)

    def test_compose_file_under_devcontainer_uses_workspace_as_project_root(self):
        def prepare(project_dir):
            devcontainer_dir = project_dir / ".devcontainer"
            devcontainer_dir.mkdir()
            (devcontainer_dir / "compose.yaml").write_text(
                """services:
  app:
    image: example.invalid/image
    volumes:
      - ..:/workspace
""",
                encoding="utf-8",
            )

        config = """services:
  app:
    image: example.invalid/image
    volumes:
      - PLACEHOLDER:/workspace
"""

        def resolved_config(project_dir):
            return config.replace("PLACEHOLDER", str(project_dir))

        result, records, source_file = self.run_guard(
            ["-f", ".devcontainer/compose.yaml", "ps"],
            config=resolved_config,
            add_compose_file=False,
            prepare_project=prepare,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(records), 2)
        snapshot = yaml.safe_load(records[1]["snapshot"])
        self.assertEqual(
            snapshot["services"]["app"]["volumes"][0]["source"],
            str(source_file.parent),
        )

    def test_unsafe_cli_escapes_are_denied_before_provider(self):
        cases = (
            ["--podman-args=--privileged", "up"],
            ["--podman-path", "/tmp/provider", "up"],
            ["--env-file", "secrets.env", "up"],
            ["--project-name", "../other", "up"],
            [
                "--project-name",
                "other",
                "ls",
                "-a",
                "--filter",
                "name=different",
                "--format",
                "json",
            ],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result, records, _ = self.run_guard(arguments)
                self.assertEqual(result.returncode, 125)
                self.assertEqual(records, [])

    def test_missing_file_option_value_is_denied_before_provider(self):
        result, records, _ = self.run_guard(["--file"], add_compose_file=False)

        self.assertEqual(result.returncode, 125)
        self.assertIn("missing value for --file", result.stderr)
        self.assertEqual(records, [])

    def test_malformed_nested_values_fail_closed_instead_of_crashing(self):
        cases = (
            """services:
  app:
    image: example.invalid/image
    privileged: {unexpected: value}
""",
            """services:
  app:
    image: example.invalid/image
    ports:
      - target: 80
        published: 8080
        host_ip: {unexpected: value}
""",
            """services:
  app:
    image: example.invalid/image
volumes:
  data:
    external: {unexpected: value}
""",
        )
        for config in cases:
            with self.subTest(config=config):
                result, records, _ = self.run_guard(["ps"], config)
                self.assertEqual(result.returncode, 125)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(len(records), 1)

    def test_unreviewed_source_indirection_is_denied_before_provider(self):
        sources = (
            """include: ../outside.yaml
services:
  app:
    image: example.invalid/image
""",
            """services:
  app:
    extends:
      file: ../outside.yaml
      service: base
""",
            """services:
  app:
    image: first.invalid/image
    image: second.invalid/image
""",
        )
        for source in sources:
            with self.subTest(source=source):

                def prepare(project_dir, source=source):
                    (project_dir / "compose.yaml").write_text(source, encoding="utf-8")

                result, records, _ = self.run_guard(["ps"], prepare_project=prepare)
                self.assertEqual(result.returncode, 125)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(records, [])

    def test_all_compose_entry_points_use_the_local_guard(self):
        for entry_point, prefix in COMPOSE_ENTRY_POINTS:
            with self.subTest(entry_point=entry_point.name):
                result, records, _ = self.run_guard(
                    ["ps"],
                    guard=entry_point,
                    prefix=prefix,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(records), 2)
                self.assertEqual(records[1]["argv"][-1], "ps")


if __name__ == "__main__":
    unittest.main()
