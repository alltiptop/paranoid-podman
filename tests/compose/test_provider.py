import json
import os
import pty
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from paranoid_podman.common.environment import is_sensitive_env_key
from paranoid_podman.compose.models import Invocation
from paranoid_podman.compose.paths import guarded_project_name
from paranoid_podman.compose.policy import validate_and_rewrite
from paranoid_podman.compose.settings import (
    COMPOSE_CONTROL_ENV,
    COMPOSE_PROVIDER,
    REAL_PODMAN,
)
from paranoid_podman.compose.snapshot import write_snapshot

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(
    os.environ.get("PARANOID_PODMAN_RUN_COMPOSE_PROVIDER_TESTS") == "1",
    "opt-in real Compose provider test (may query Podman)",
)
class InstalledComposeProviderCompatibilityTests(unittest.TestCase):
    def test_external_binds_require_review_after_real_provider_interpolation(self):
        provider = Path(COMPOSE_PROVIDER).resolve()
        if (
            not provider.is_file()
            or provider.parent == (PROJECT_ROOT / "bin").resolve()
        ):
            self.skipTest("the real Compose provider is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            (root / "shared data").mkdir()
            (root / "other").mkdir()
            (project / ".env").write_text(
                "SHARED_DIR='../shared data'\nTARGET=/shared\n", encoding="utf-8"
            )
            log = root / "engine.jsonl"
            stdin_copy = root / "stdin.txt"
            environment = {
                **os.environ,
                "PODMAN_GUARD_REAL_PODMAN": str(
                    PROJECT_ROOT / "tests/support/fake_compose_engine.py"
                ),
                "PODMAN_GUARD_COMPOSE_PROVIDER": str(provider),
                "PODMAN_GUARD_INSTALLATION_ID": "a" * 64,
                "PARANOID_COMPOSE_ENGINE_LOG": str(log),
                "PARANOID_COMPOSE_ENGINE_STDIN": str(stdin_copy),
            }
            cases = (
                (
                    "services:\n  app:\n    image: example.invalid/app\n    volumes: ['${SHARED_DIR}:/shared:ro']\n",
                    None,
                    "compose.yaml",
                    4,
                ),
                (
                    "x-bind: &bind\n  type: bind\n  source: ${SHARED_DIR}\n  target: ${TARGET}\nservices:\n  app:\n    image: example.invalid/app\n    volumes: [*bind]\n",
                    None,
                    "compose.yaml",
                    3,
                ),
                (
                    "services:\n  app:\n    image: example.invalid/app\n    volumes: ['../other:/shared']\n",
                    "services:\n  app:\n    volumes: ['${SHARED_DIR}:/shared']\n",
                    "override.yaml",
                    3,
                ),
            )
            for source, override, filename, line in cases:
                for approve in (False, True):
                    with self.subTest(source=source, approve=approve):
                        (project / "compose.yaml").write_text(source, encoding="utf-8")
                        arguments = [
                            str(PROJECT_ROOT / "bin/compose-guard"),
                            "-f",
                            "compose.yaml",
                        ]
                        if override is not None:
                            (project / "override.yaml").write_text(
                                override, encoding="utf-8"
                            )
                            arguments.extend(["-f", "override.yaml"])
                        arguments.extend(
                            ["run", "--rm", "--no-deps", "-T", "app", "cat"]
                        )
                        log.write_text("", encoding="utf-8")
                        stdin_copy.unlink(missing_ok=True)
                        if approve:
                            master, slave = pty.openpty()
                            try:
                                process = subprocess.Popen(
                                    arguments,
                                    cwd=project,
                                    env=environment,
                                    stdin=slave,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    text=True,
                                    start_new_session=True,
                                )
                                os.write(master, b"y\ncontainer input\n\x04")
                                try:
                                    stdout, stderr = process.communicate(timeout=30)
                                except BaseException:
                                    os.killpg(process.pid, signal.SIGKILL)
                                    process.communicate(timeout=5)
                                    raise
                                result = subprocess.CompletedProcess(
                                    arguments, process.returncode, stdout, stderr
                                )
                            finally:
                                os.close(master)
                                os.close(slave)
                        else:
                            result = subprocess.run(
                                arguments,
                                cwd=project,
                                env=environment,
                                input="y\n",
                                capture_output=True,
                                text=True,
                                check=False,
                                timeout=30,
                            )
                        self.assertEqual(
                            result.returncode, 0 if approve else 125, result.stderr
                        )
                        self.assertIn("[warning]", result.stderr)
                        self.assertIn(str(root / "shared data"), result.stderr)
                        self.assertIn(f'"{project / filename}":{line}', result.stderr)
                        calls = [
                            json.loads(item) for item in log.read_text().splitlines()
                        ]
                        self.assertEqual(
                            any(
                                call[0] in {"create", "run", "build"} for call in calls
                            ),
                            approve,
                        )
                        if approve:
                            self.assertEqual(
                                stdin_copy.read_text(), "container input\n"
                            )

    def test_daily_compose_scenarios_with_real_provider_and_recording_engine(self):
        provider = Path(COMPOSE_PROVIDER).resolve()
        if (
            not provider.is_file()
            or provider.parent == (PROJECT_ROOT / "bin").resolve()
        ):
            self.skipTest("the real Compose provider is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "Containerfile").write_text("FROM scratch\n", encoding="utf-8")
            (project / "data").mkdir()
            (project / ".env").write_text("APP_MODE=development\n", encoding="utf-8")
            (project / ".containerignore").write_text(".env\n", encoding="utf-8")
            (project / ".devcontainer").mkdir()
            (project / ".devcontainer/devcontainer.json").write_text(
                "{}\n", encoding="utf-8"
            )
            (project / "compose.yaml").write_text(
                """services:
  app:
    image: example.invalid/app
    build: .
    command: [sh]
    labels:
      devpod.sh/id: example-workspace
    env_file: [.env]
    environment:
      CORS_CREDENTIALS: 'true'
    ports: ['18000:8000', '0.0.0.0:18001:8001', '[::]:18002:8002']
    volumes: ['.:/workspace', './.devcontainer:/workspace/.devcontainer:ro', './data:/data', 'cache:/cache']
    networks: [backend]
    userns_mode: keep-id:uid=10001,gid=10001
    cpus: '1.0'
    mem_limit: 1g
    mem_reservation: 256m
    shm_size: 128m
    ulimits:
      nofile: {soft: 1024, hard: 2048}
    extra_hosts:
      host.docker.internal: host-gateway
      host.containers.internal: host-gateway
    restart: unless-stopped
volumes:
  cache: {}
networks:
  backend:
    driver: bridge
""",
                encoding="utf-8",
            )
            log = project / "engine.jsonl"
            stdin_copy = project / "stdin.txt"
            environment = {
                **os.environ,
                "PODMAN_GUARD_REAL_PODMAN": str(
                    PROJECT_ROOT / "tests/support/fake_compose_engine.py"
                ),
                "PODMAN_GUARD_COMPOSE_PROVIDER": str(provider),
                "PODMAN_GUARD_INSTALLATION_ID": "a" * 64,
                "PARANOID_COMPOSE_ENGINE_LOG": str(log),
                "PARANOID_COMPOSE_ENGINE_STDIN": str(stdin_copy),
                "PARANOID_COMPOSE_ENGINE_RUN_EXIT": "0",
            }
            scenarios = (
                (["config", "--quiet"], None),
                (["config", "--services"], None),
                (["build", "--no-cache", "app"], "build"),
                (["up", "-d", "--build", "app"], "create"),
                (["up", "-d", "--force-recreate", "--remove-orphans", "app"], "create"),
                (["up", "-d", "--renew-anon-volumes", "app"], "create"),
                (["up", "-d", "--no-recreate", "app"], "create"),
                (["run", "--rm", "--no-deps", "-T", "app"], "run"),
                (
                    [
                        "run",
                        "--rm",
                        "-T",
                        "-eMODE=test",
                        "-u10001",
                        "-w/workspace",
                        "app",
                        "sh",
                    ],
                    "run",
                ),
                (
                    [
                        "run",
                        "--rm",
                        "--service-ports",
                        "-T",
                        "--env",
                        "MODE=test",
                        "app",
                        "sh",
                    ],
                    "run",
                ),
                (
                    [
                        "exec",
                        "-T",
                        "--user",
                        "root",
                        "--workdir",
                        "/workspace",
                        "app",
                        "sh",
                        "-c",
                        "cat",
                    ],
                    "exec",
                ),
                (["ps", "-q"], "ps"),
                (["logs", "--tail", "25", "app"], "logs"),
                (["images"], "images"),
                (["port", "app", "8000"], "inspect"),
                (["stop", "--timeout", "10", "app"], "stop"),
                (["start", "app"], "start"),
                (["restart", "app"], "restart"),
                (["down", "--volumes", "--remove-orphans"], "rm"),
            )
            for arguments, engine_command in scenarios:
                with self.subTest(command=arguments):
                    log.write_text("", encoding="utf-8")
                    stdin_copy.unlink(missing_ok=True)
                    result = subprocess.run(
                        [
                            str(PROJECT_ROOT / "bin/compose-guard"),
                            "-f",
                            "compose.yaml",
                            *arguments,
                        ],
                        cwd=project,
                        env=environment,
                        input="input for the container\n",
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn("compose-guard:", result.stderr)
                    calls = [json.loads(line) for line in log.read_text().splitlines()]
                    if engine_command:
                        self.assertTrue(
                            any(call[0] == engine_command for call in calls), calls
                        )
                    for call in calls:
                        if call[0] not in {"run", "create"}:
                            continue
                        self.assertIn("no-new-privileges", call)
                        self.assertIn("io.github.paranoid-podman.policy=1", call)
                        self.assertIn("example-workspace", call)
                        self.assertIn("host.docker.internal:host-gateway", call)
                        self.assertIn("host.containers.internal:host-gateway", call)
                        self.assertIn("128m", call)
                        self.assertIn("nofile=1024:2048", call)
                        self.assertTrue(
                            any(
                                ":/workspace/.devcontainer:ro" in token
                                for token in call
                            ),
                            call,
                        )
                    if arguments[0] in {"exec", "run"}:
                        self.assertEqual(
                            stdin_copy.read_text(), "input for the container\n"
                        )
                    if arguments[0] == "run":
                        run = next(call for call in calls if call[0] == "run")
                        self.assertNotIn("--restart", run)
                        self.assertEqual("-p" in run, "--service-ports" in arguments)

    def test_one_off_run_with_real_provider_and_recording_engine(self):
        provider = Path(COMPOSE_PROVIDER).resolve()
        if (
            not provider.is_file()
            or provider.parent == (PROJECT_ROOT / "bin").resolve()
        ):
            self.skipTest("the real Compose provider is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "Containerfile").write_text("FROM scratch\n", encoding="utf-8")
            (project / "compose.yaml").write_text(
                """services:
  database:
    image: example.invalid/database
    profiles: [data-build]
  importer:
    build: .
    profiles: [data-build]
    depends_on: [database]
    cpus: '1.0'
    mem_limit: 1g
    memswap_limit: 1g
    userns_mode: keep-id:uid=10001,gid=10001
""",
                encoding="utf-8",
            )
            engine_log = project / "engine.jsonl"
            environment = {
                **os.environ,
                "PODMAN_GUARD_REAL_PODMAN": str(
                    PROJECT_ROOT / "tests/support/fake_compose_engine.py"
                ),
                "PODMAN_GUARD_COMPOSE_PROVIDER": str(provider),
                "PARANOID_COMPOSE_ENGINE_LOG": str(engine_log),
                "PARANOID_COMPOSE_ENGINE_RUN_EXIT": "23",
                "PODMAN_GUARD_INSTALLATION_ID": "a" * 64,
            }
            result = subprocess.run(
                [
                    str(PROJECT_ROOT / "bin/compose-guard"),
                    "-f",
                    "compose.yaml",
                    "--profile",
                    "data-build",
                    "run",
                    "--rm",
                    "--build",
                    "importer",
                    "build",
                    "--flag",
                    "two words",
                ],
                cwd=project,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(result.returncode, 23, result.stderr)
            calls = [json.loads(line) for line in engine_log.read_text().splitlines()]
            self.assertTrue(any(call[0] == "build" for call in calls))
            self.assertTrue(any(call[0] == "create" for call in calls))
            run = next(call for call in calls if call[0] == "run")
            self.assertEqual(run[-3:], ["build", "--flag", "two words"])
            for flag in ("--rm", "--security-opt", "--pids-limit", "--cpus", "-m"):
                self.assertIn(flag, run)
            self.assertEqual(
                run[run.index("--userns") + 1], "keep-id:uid=10001,gid=10001"
            )
            self.assertIn("io.github.paranoid-podman.policy=1", run)
            self.assertNotIn("--memory-swap", run)  # Current provider limitation.
            self.assertLess(
                next(i for i, call in enumerate(calls) if call[0] == "build"),
                next(i for i, call in enumerate(calls) if call[0] == "create"),
            )
            engine_log.write_text("", encoding="utf-8")
            failed = subprocess.run(
                result.args,
                cwd=project,
                env={**environment, "PARANOID_COMPOSE_ENGINE_BUILD_EXIT": "19"},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(failed.returncode, 19, failed.stderr)
            calls = [json.loads(line) for line in engine_log.read_text().splitlines()]
            self.assertFalse(
                any(call[0] in {"create", "start", "run"} for call in calls), calls
            )

    def test_reviewed_snapshot_can_be_reparsed_without_starting_containers(self):
        # PATH intentionally contains the wrapper in a normal installation.
        # Test the policy-owned provider rather than rediscovering that wrapper.
        provider = Path(COMPOSE_PROVIDER).resolve()
        if not provider.is_file() or not os.access(provider, os.X_OK):
            self.skipTest("the configured Compose provider is unavailable")
        if provider.parent == (PROJECT_ROOT / "bin").resolve():
            self.skipTest("the provider path resolves to a paranoid-podman wrapper")
        real_podman = Path(REAL_PODMAN).resolve()
        if not real_podman.is_file():
            self.skipTest("the configured real Podman executable is unavailable")
        if real_podman.parent == (PROJECT_ROOT / "bin").resolve():
            self.skipTest("the Podman path resolves to a paranoid-podman wrapper")

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir).resolve()
            invocation = Invocation(
                command="ps",
                command_args=[],
                compose_files=[],
                env_file=None,
                profiles=[],
                project_dir=project_dir,
                project_name=guarded_project_name(project_dir),
                execution_globals=[],
            )
            original_command = "printf '$HOME ${VALUE}'"
            (project_dir / "Containerfile").write_text(
                "FROM scratch\n", encoding="utf-8"
            )
            ports = [
                "18055:8055",
                "0.0.0.0:8080:80",
                "[::]:8081:81",
                {"host_ip": "::", "published": "8082", "target": 82},
            ]
            model = {
                "services": {
                    "app": {
                        "command": ["sh", "-c", original_command],
                        "image": "example.invalid/image",
                        "ports": ports,
                        "networks": {"backend": {"aliases": ["app-internal"]}},
                    },
                    "built": {"build": ".", "networks": ["backend"]},
                },
                "networks": {
                    "backend": {"name": "app-dev", "driver": "bridge", "internal": True}
                },
            }
            rewritten = validate_and_rewrite(model, invocation)
            snapshot = write_snapshot(project_dir, rewritten)
            environment = {
                key: value
                for key, value in os.environ.items()
                if key not in COMPOSE_CONTROL_ENV
                and not key.startswith("PODMAN_COMPOSE_")
                and not is_sensitive_env_key(key)
            }
            runtime_dir = project_dir / "runtime"
            runtime_dir.mkdir(mode=0o700)
            environment["XDG_RUNTIME_DIR"] = str(runtime_dir)
            result = subprocess.run(
                [
                    str(provider),
                    "--dry-run",
                    "--podman-path",
                    str(real_podman),
                    "--env-file",
                    "/dev/null",
                    "-f",
                    str(snapshot),
                    "-p",
                    invocation.project_name,
                    "config",
                ],
                cwd=project_dir,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

            if result.returncode != 0 and any(
                marker in result.stderr
                for marker in (
                    "Failed to obtain podman configuration",
                    "RunRoot is pointing to a path",
                    "runtime init lock",
                )
            ):
                self.skipTest(
                    "the sandbox does not provide a writable rootless Podman runtime"
                )
            self.assertEqual(result.returncode, 0, result.stderr)
            reparsed = yaml.safe_load(result.stdout)
            self.assertEqual(
                reparsed["services"]["app"]["command"][-1], original_command
            )
            self.assertEqual(
                reparsed["services"]["app"]["security_opt"],
                ["no-new-privileges"],
            )
            self.assertEqual(reparsed["networks"], model["networks"])
            self.assertEqual(reparsed["services"]["app"]["ports"][:3], ports[:3])
            self.assertEqual(reparsed["services"]["app"]["ports"][3]["host_ip"], "[::]")
            self.assertNotIn("image", reparsed["services"]["built"])
            self.assertEqual(
                reparsed["services"]["built"]["build"]["context"], str(project_dir)
            )


if __name__ == "__main__":
    unittest.main()
