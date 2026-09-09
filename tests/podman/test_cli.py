import json
import os
import pwd
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUARD = PROJECT_ROOT / "bin" / "podman"
DOCKER_ALIAS = PROJECT_ROOT / "bin" / "docker"
FAKE_PROVIDER = PROJECT_ROOT / "tests" / "support" / "fake_provider.py"
SENSITIVE_ENV_KEYS = {
    "DBUS_SESSION_BUS_ADDRESS",
    "DISPLAY",
    "GIT_ASKPASS",
    "GIT_SSH",
    "GIT_SSH_COMMAND",
    "GNUPGHOME",
    "GPG_AGENT_INFO",
    "KUBECONFIG",
    "SSH_AGENT_PID",
    "SSH_ASKPASS",
    "SSH_AUTH_SOCK",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
}
PROVIDER_CONTROL_ENV_KEYS = (
    "CONTAINER_CONNECTION",
    "CONTAINER_HOST",
    "CONTAINER_PROXY",
    "CONTAINER_SSHKEY",
    "CONTAINERS_CONF",
    "CONTAINERS_POLICY",
    "CONTAINERS_REGISTRIES_CONF",
    "CONTAINERS_STORAGE_CONF",
    "PODMAN_CONNECTIONS_CONF",
    "PODMAN_GUARD_INSTALLATION_ID",
    "PODMAN_HOST",
    "STORAGE_DRIVER",
    "STORAGE_OPTS",
    "SYNTHETIC_API_TOKEN",
    "TMPDIR",
)


class PodmanGuardIntegrationTests(unittest.TestCase):
    def run_guard(
        self,
        arguments,
        *,
        cwd=None,
        provider_exit_code=0,
        guard=GUARD,
        extra_env=None,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "argv.json"
            environment_output_path = Path(temp_dir) / "environment.json"
            env = os.environ.copy()
            env.update(
                {
                    "PARANOID_PODMAN_TEST_OUTPUT": str(output_path),
                    "PARANOID_PODMAN_TEST_ENV_OUTPUT": str(environment_output_path),
                    "PARANOID_PODMAN_TEST_ENV_KEYS": json.dumps(
                        PROVIDER_CONTROL_ENV_KEYS
                    ),
                    "PARANOID_PODMAN_TEST_EXIT_CODE": str(provider_exit_code),
                    "PODMAN_GUARD_REAL_PODMAN": str(FAKE_PROVIDER),
                    "PODMAN_GUARD_INSTALLATION_ID": "a" * 64,
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
            result = subprocess.run(
                [str(guard), *arguments],
                cwd=cwd or PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 125 and provider_exit_code != 125:
                self.assertIn("podman-guard: next step:", result.stderr)
            recorded_arguments = None
            if output_path.exists():
                recorded_arguments = json.loads(output_path.read_text(encoding="utf-8"))
            self.forwarded_provider_environment = None
            if environment_output_path.exists():
                self.forwarded_provider_environment = json.loads(
                    environment_output_path.read_text(encoding="utf-8")
                )
            return result, recorded_arguments

    def assert_denied(self, arguments, *, cwd=None, guard=GUARD):
        result, recorded_arguments = self.run_guard(arguments, cwd=cwd, guard=guard)
        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertIsNone(recorded_arguments, "denied arguments reached the provider")
        return result

    def test_safe_run_reaches_fake_provider_with_hardening(self):
        result, arguments = self.run_guard(["run", "--rm", "example.invalid/image"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNotNone(arguments)
        self.assertEqual(arguments[0], "run")
        self.assertIn("--rm", arguments)
        self.assertIn("--security-opt=no-new-privileges", arguments)
        self.assertIn("--pids-limit=512", arguments)
        self.assertIn("--http-proxy=false", arguments)
        self.assertIn("--pull=never", arguments)
        self.assertIn("--restart=no", arguments)
        self.assertIn("--label=io.github.paranoid-podman.policy=1", arguments)
        self.assertIn(
            f"--label=io.github.paranoid-podman.installation={'a' * 64}",
            arguments,
        )

    def test_provider_exit_code_is_preserved(self):
        result, _ = self.run_guard(
            ["run", "example.invalid/image"], provider_exit_code=47
        )
        self.assertEqual(result.returncode, 47)

    def test_real_provider_cannot_resolve_to_a_package_entry_point(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            linked_guard = Path(temp_dir) / "provider"
            linked_guard.symlink_to(GUARD)
            for provider in (GUARD, linked_guard):
                with self.subTest(provider=provider):
                    result, recorded_arguments = self.run_guard(
                        ["run", "example.invalid/image"],
                        extra_env={"PODMAN_GUARD_REAL_PODMAN": str(provider)},
                    )
                    self.assertEqual(result.returncode, 125, result.stderr)
                    self.assertIsNone(recorded_arguments)

    def test_docker_alias_uses_the_guard_from_the_same_installation(self):
        result, arguments = self.run_guard(
            ["run", "--rm", "example.invalid/image"], guard=DOCKER_ALIAS
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(arguments[0], "run")

        self.assert_denied(
            ["run", "--privileged", "example.invalid/image"], guard=DOCKER_ALIAS
        )

    def test_compose_dispatch_uses_local_guard_and_configured_provider(self):
        result, arguments = self.run_guard(
            ["compose", "help"],
            extra_env={
                "PODMAN_GUARD_COMPOSE_GUARD": str(FAKE_PROVIDER),
                "PODMAN_GUARD_COMPOSE_PROVIDER": str(FAKE_PROVIDER),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            arguments[:3],
            ["--dry-run", "--podman-path", str(FAKE_PROVIDER)],
        )
        self.assertNotIn("compose", arguments)

    def test_replace_does_not_hide_privileged(self):
        self.assert_denied(
            ["run", "--replace", "--privileged", "example.invalid/image"]
        )

    def test_container_run_is_protected(self):
        self.assert_denied(
            ["container", "run", "--privileged", "example.invalid/image"]
        )

    def test_container_create_is_protected(self):
        self.assert_denied(
            ["container", "create", "--network=host", "example.invalid/image"]
        )

    def test_known_privilege_and_host_access_forms_are_denied(self):
        unsafe_arguments = (
            ["--privileged=true"],
            ["--network", "host"],
            ["--pid=host"],
            ["--cap-add=SYS_ADMIN"],
            ["--device-cgroup-rule", "c 1:3 rwm"],
            ["--gpus", "all"],
            ["--security-opt=seccomp=unconfined"],
            ["--volume", "/:/host"],
            [
                "--mount",
                "type=bind,src=/run/docker.sock,target=/run/docker.sock",
            ],
        )
        for runtime_arguments in unsafe_arguments:
            with self.subTest(arguments=runtime_arguments):
                self.assert_denied(["run", *runtime_arguments, "example.invalid/image"])

    def test_bind_mount_ownership_and_relabel_options_are_denied(self):
        unsafe_arguments = (
            ["--mount", "type=bind,src=.,target=/workspace,U"],
            ["--mount=type=bind,src=.,target=/workspace,idmap"],
            ["--mount", "type=bind,src=.,target=/workspace,relabel=shared"],
            ["--mount=type=bind,src=.,target=/workspace,z"],
            ["--volume", ".:/workspace:U"],
            ["--volume=.:/workspace:z"],
            ["--volume", ".:/workspace:Z"],
            ["-v.:/workspace:idmap"],
        )
        for mount_arguments in unsafe_arguments:
            with self.subTest(arguments=mount_arguments):
                self.assert_denied(["run", *mount_arguments, "example.invalid/image"])

    def test_missing_env_files_and_env_host_are_denied(self):
        unsafe_arguments = (
            ["--env-file", "example.env"],
            ["--env-file=example.env"],
            ["--env-host"],
        )
        for env_arguments in unsafe_arguments:
            with self.subTest(arguments=env_arguments):
                self.assert_denied(["run", *env_arguments, "example.invalid/image"])

    def test_sensitive_environment_is_removed_in_all_cli_forms(self):
        forms = (
            ["-e", "SSH_AUTH_SOCK=/tmp/synthetic-secret"],
            ["--env", "SSH_AUTH_SOCK=/tmp/synthetic-secret"],
            ["-eSSH_AUTH_SOCK=/tmp/synthetic-secret"],
            ["--env=SSH_AUTH_SOCK=/tmp/synthetic-secret"],
        )
        for env_arguments in forms:
            with self.subTest(arguments=env_arguments):
                result, arguments = self.run_guard(
                    ["run", *env_arguments, "example.invalid/image"]
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("synthetic-secret", " ".join(arguments or []))
                self.assertIn("--unsetenv=SSH_AUTH_SOCK", arguments)

    def test_implicit_environment_forwarding_is_denied_or_removed(self):
        self.assert_denied(["run", "--env", "SAFE_VALUE", "example.invalid/image"])

        result, arguments = self.run_guard(
            ["run", "-eSSH_AUTH_SOCK", "example.invalid/image"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--unsetenv=SSH_AUTH_SOCK", arguments)

    def test_explicit_safe_environment_is_preserved(self):
        result, arguments = self.run_guard(
            ["run", "--env=SAFE_VALUE=yes", "example.invalid/image"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--env=SAFE_VALUE=yes", arguments)

    def test_repeated_sensitive_environment_options_cannot_restore_a_value(self):
        marker = "synthetic-secret"
        result, arguments = self.run_guard(
            [
                "run",
                "-e",
                f"SSH_AUTH_SOCK=/tmp/{marker}",
                "--env=SAFE_VALUE=yes",
                f"-eEXAMPLE_API_KEY={marker}",
                "example.invalid/image",
            ]
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(marker, " ".join(arguments or []))
        self.assertIn("--env=SAFE_VALUE=yes", arguments)
        self.assertIn("--unsetenv=SSH_AUTH_SOCK", arguments)
        self.assertIn("--unsetenv=EXAMPLE_API_KEY", arguments)

    def test_credential_forwarding_options_are_denied(self):
        unsafe_arguments = (
            ["--http-proxy"],
            ["--secret", "id=example"],
            ["--volumes-from", "existing-container"],
            ["--rootfs"],
        )
        for runtime_arguments in unsafe_arguments:
            with self.subTest(arguments=runtime_arguments):
                self.assert_denied(["run", *runtime_arguments, "example.invalid/image"])

    def test_weakened_hardening_options_are_denied(self):
        unsafe_arguments = (
            ["--cap-drop", "NET_RAW"],
            ["--cap-drop=CHOWN"],
            ["--pids-limit", "0"],
            ["--pids-limit=32769"],
            ["--pids-limit=unlimited"],
        )
        for runtime_arguments in unsafe_arguments:
            with self.subTest(arguments=runtime_arguments):
                self.assert_denied(["run", *runtime_arguments, "example.invalid/image"])

    def test_explicit_safe_boolean_and_limit_values_are_preserved(self):
        result, arguments = self.run_guard(
            [
                "run",
                "--privileged=false",
                "--http-proxy=false",
                "--cap-drop=ALL",
                "--pids-limit=128",
                "example.invalid/image",
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--privileged=false", arguments)
        self.assertIn("--http-proxy=false", arguments)
        self.assertEqual(arguments.count("--cap-drop=ALL"), 1)
        self.assertEqual(arguments.count("--pids-limit=128"), 1)

    def test_published_port_addresses_are_preserved(self):
        ports = (
            "8080:80",
            "0.0.0.0:8080:80",
            "192.0.2.1:8080:80",
            "[::]:8080:80",
            "[2001:db8::1]::80",
            "127.0.0.1::80",
            "80",
            "8080-8081:80-81",
            "53/udp",
            "127.0.0.1:8080:80/tcp",
        )
        for port in ports:
            with self.subTest(port=port):
                result, arguments = self.run_guard(
                    ["run", f"-p{port}", "example.invalid/image"]
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"-p{port}", arguments)

    def test_malformed_published_ports_are_denied(self):
        unsafe_ports = (
            "127.0.0.1:0:80",
            "65536:80",
            "8080:0",
            "999.0.0.1:8080:80",
            "[not-ip]:8080:80",
            "8081-8080:80-81",
            "8080-8081:80-82",
            "--privileged",
            "80/tcp,host",
        )
        for port in unsafe_ports:
            with self.subTest(port=port):
                self.assert_denied(["run", "--publish", port, "example.invalid/image"])

    def test_publish_all_is_an_ordinary_runtime_option(self):
        for option in (
            "-P",
            "-dP",
            "--publish-all",
            "--publish-all=true",
            "--publish-all=false",
        ):
            with self.subTest(option=option):
                result, arguments = self.run_guard(
                    ["run", option, "example.invalid/image"]
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(option, arguments)

    def test_only_reviewed_network_and_namespace_forms_are_allowed(self):
        unsafe_values = (
            ["--network", "container:existing"],
            ["--network=custom-network"],
            ["--network=slirp4netns:allow_host_loopback=true"],
            ["--pid=container:existing"],
            ["--ipc=host"],
            ["--userns=auto"],
        )
        for options in unsafe_values:
            with self.subTest(options=options):
                self.assert_denied(["run", *options, "example.invalid/image"])

        result, arguments = self.run_guard(
            [
                "run",
                "--network=slirp4netns",
                "--pid=private",
                "--userns=keep-id",
                "example.invalid/image",
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--network=slirp4netns", arguments)
        self.assertIn("--pid=private", arguments)
        self.assertEqual(arguments.count("--userns=keep-id"), 1)

    def test_unreviewed_host_access_and_lifecycle_options_are_denied(self):
        unsafe_options = (
            ["--annotation", "example=value"],
            ["--authfile", "/tmp/auth.json"],
            ["--cidfile", "/tmp/container.id"],
            ["--gidmap", "0:0:1"],
            ["--group-add", "keep-groups"],
            ["--hosts-file", "/tmp/hosts"],
            ["--label-file", "/tmp/labels"],
            ["--log-driver", "journald"],
            ["--log-opt", "path=/tmp/container.log"],
            ["--pod", "existing"],
            ["--preserve-fd", "3"],
            ["--restart", "always"],
            ["--sdnotify", "container"],
            ["--security-opt", "seccomp=unconfined"],
            ["--sysctl", "net.ipv4.ip_forward=1"],
            ["--uidmap", "0:0:1"],
            ["--volume-driver", "local"],
        )
        for options in unsafe_options:
            with self.subTest(options=options):
                self.assert_denied(["run", *options, "example.invalid/image"])

    def test_environment_cannot_disable_critical_hardening(self):
        for variable in ("PODMAN_GUARD_CAP_DROP", "PODMAN_GUARD_NO_NEW_PRIVS"):
            with self.subTest(variable=variable):
                result, recorded_arguments = self.run_guard(
                    ["run", "example.invalid/image"],
                    extra_env={variable: "0"},
                )
                self.assertEqual(result.returncode, 125, result.stderr)
                self.assertIsNone(recorded_arguments)

    def test_global_remote_and_runtime_overrides_are_denied(self):
        unsafe_arguments = (
            ["--remote"],
            ["--url", "unix:///tmp/podman.sock"],
            ["--runtime=/tmp/custom-runtime"],
            ["--hooks-dir", "/tmp/hooks"],
        )
        for global_arguments in unsafe_arguments:
            with self.subTest(arguments=global_arguments):
                self.assert_denied([*global_arguments, "run", "example.invalid/image"])

    def test_provider_control_environment_is_removed_before_exec(self):
        controlled_environment = {
            "CONTAINER_CONNECTION": "synthetic-connection",
            "CONTAINER_HOST": "ssh://synthetic.invalid/run/podman.sock",
            "CONTAINER_PROXY": "socks5://synthetic.invalid",
            "CONTAINER_SSHKEY": "/synthetic/key",
            "CONTAINERS_CONF": "/synthetic/containers.conf",
            "CONTAINERS_POLICY": "/synthetic/policy.json",
            "CONTAINERS_REGISTRIES_CONF": "/synthetic/registries.conf",
            "CONTAINERS_STORAGE_CONF": "/synthetic/storage.conf",
            "PODMAN_CONNECTIONS_CONF": "/synthetic/connections.json",
            "PODMAN_HOST": "tcp://synthetic.invalid:1234",
            "STORAGE_DRIVER": "synthetic",
            "STORAGE_OPTS": "synthetic.option=true",
            "SYNTHETIC_API_TOKEN": "synthetic-secret",
            "TMPDIR": "/synthetic/tmp",
        }

        result, arguments = self.run_guard(
            ["run", "example.invalid/image"], extra_env=controlled_environment
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNotNone(arguments)
        self.assertEqual(self.forwarded_provider_environment, [])

    def test_unknown_global_option_fails_closed(self):
        result = self.assert_denied(
            ["--definitely-unknown=value", "run", "example.invalid/image"]
        )
        self.assertIn("--definitely-unknown", result.stderr)
        self.assertNotIn("=value", result.stderr)

    def test_all_global_options_are_denied_for_guarded_commands(self):
        self.assert_denied(
            ["--log-level=error", "run", "--rm", "example.invalid/image"]
        )

    def test_unreviewed_commands_are_denied_before_provider(self):
        commands = (
            ["image", "mount", "example"],
            ["kube", "play", "example.yaml"],
            ["machine", "start"],
            ["mount", "example"],
            ["play", "kube", "example.yaml"],
            ["pod", "create"],
            ["runlabel", "example"],
            ["system", "service"],
            ["unshare", "sh"],
            ["volume", "create", "example"],
        )
        for arguments in commands:
            with self.subTest(arguments=arguments):
                result = self.assert_denied(arguments)
                self.assertIn("unreviewed Podman command", result.stderr)

    def test_metadata_commands_are_forwarded_exactly(self):
        forms = ([], ["help"], ["version"], ["-h"], ["--help"], ["-v"], ["--version"])
        for arguments in forms:
            with self.subTest(arguments=arguments):
                result, recorded = self.run_guard(arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(recorded, arguments)

    def test_devpod_discovery_and_container_lifecycle_are_supported(self):
        forms = (
            ["ps", "-q", "-a", "--filter", "label=devpod.sh/id=example-workspace"],
            ["inspect", "--type", "container", "deadbeef"],
            [
                "inspect",
                "--type",
                "image",
                "--format",
                "{{if .RepoTags}}{{index .RepoTags 0}}{{end}}",
                "example.invalid/devpod:latest",
            ],
            ["info", "-f", "{{.Runtimes.nvidia}}"],
            ["start", "deadbeef"],
            ["stop", "deadbeef"],
            ["logs", "deadbeef"],
            ["exec", "-i", "-u", "node", "deadbeef", "sh", "-c", "echo ready"],
            ["exec", "-u", "root", "deadbeef", "chmod", "644", "/etc/passwd"],
        )
        for arguments in forms:
            with self.subTest(arguments=arguments):
                result, recorded = self.run_guard(arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(recorded, arguments)

    def test_executable_lifecycle_requires_current_guard_provenance(self):
        active_forms = (
            ["start", "deadbeef"],
            ["container", "start", "deadbeef"],
            ["exec", "deadbeef", "sh", "-lc", "echo ready"],
            ["container", "exec", "deadbeef", "sh"],
        )
        for arguments in active_forms:
            with self.subTest(arguments=arguments):
                result, recorded = self.run_guard(
                    arguments,
                    extra_env={"PARANOID_PODMAN_TEST_POLICY_VERSION": "0"},
                )
                self.assertEqual(result.returncode, 125, result.stderr)
                self.assertIn("not created by the current guard policy", result.stderr)
                self.assertIsNone(recorded)

        result, recorded = self.run_guard(
            ["exec", "deadbeef", "sh"],
            extra_env={"PARANOID_PODMAN_TEST_INSTALLATION_ID": "b" * 64},
        )
        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertIn("not created by the current guard policy", result.stderr)
        self.assertIsNone(recorded)

        cleanup_forms = (
            ["stop", "deadbeef"],
            ["rm", "deadbeef"],
        )
        for arguments in cleanup_forms:
            with self.subTest(arguments=arguments):
                result, recorded = self.run_guard(
                    arguments,
                    extra_env={"PARANOID_PODMAN_TEST_POLICY_VERSION": "0"},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(recorded, arguments)

    def test_failed_container_provenance_query_is_hidden_and_denied(self):
        result, recorded = self.run_guard(
            ["start", "deadbeef"],
            extra_env={"PARANOID_PODMAN_TEST_INSPECT_EXIT_CODE": "7"},
        )

        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertIn("could not verify existing container provenance", result.stderr)
        self.assertIsNone(recorded)

    def test_devpod_lifecycle_rejects_unreviewed_host_access(self):
        forms = (
            ["ps", "--url", "ssh://example.invalid"],
            ["inspect", "--type", "volume", "example"],
            ["exec", "--privileged", "deadbeef", "sh"],
            ["exec", "--env-file", "/tmp/example.env", "deadbeef", "sh"],
            ["exec", "-e", "SSH_AUTH_SOCK=/tmp/agent", "deadbeef", "sh"],
            ["start", "../../host"],
        )
        for arguments in forms:
            with self.subTest(arguments=arguments):
                self.assert_denied(arguments)

    def test_devpod_image_and_removal_commands_are_narrowly_supported(self):
        forms = (
            ["pull", "example.invalid/devpod:latest"],
            ["push", "example.invalid/devpod:latest"],
            [
                "tag",
                "example.invalid/devpod:latest",
                "example.invalid/devpod:cached",
            ],
            ["rm", "deadbeef"],
            ["container", "rm", "deadbeef"],
            ["buildx", "version"],
        )
        for arguments in forms:
            with self.subTest(arguments=arguments):
                result, recorded = self.run_guard(arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(recorded, arguments)

        self.assert_denied(["pull", "--creds", "user:password", "example.invalid/x"])

    def test_container_removal_options_preserve_arguments_and_allow_old_containers(
        self,
    ):
        container = "example-cities-pg18-12345678"
        forms = (
            ["-f", container],
            ["--force", container],
            [container, "-f"],
            ["--ignore", container, "deadbeef"],
            ["-i", "-v", container],
            ["--volumes", "--force", container],
            ["-fvi", container],
            ["--force=true", "--ignore=false", container],
            ["-f=false", container],
            ["-vi=false", container],
            ["--force", "--time", "0", container],
            ["--time=-1", container, "--force"],
            ["-f", "-t", "10", container],
            ["-ft10", container],
            ["-fvt=10", container],
            ["-ft-1", container],
            ["-f", "--", container, "deadbeef"],
        )
        for guard in (GUARD, DOCKER_ALIAS):
            for command in (["rm"], ["container", "rm"]):
                for options in forms:
                    arguments = [*command, *options]
                    with self.subTest(guard=guard.name, arguments=arguments):
                        result, recorded = self.run_guard(
                            arguments,
                            guard=guard,
                            extra_env={
                                "PARANOID_PODMAN_TEST_POLICY_VERSION": "0",
                                "PARANOID_PODMAN_TEST_INSPECT_EXIT_CODE": "7",
                            },
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(recorded, arguments)

    def test_container_removal_rejects_implicit_targets_and_malformed_options(self):
        forms = (
            [],
            ["-f"],
            ["--"],
            ["-fi", "--"],
            ["--all"],
            ["-fa"],
            ["--latest"],
            ["--filter", "name=example"],
            ["--cidfile", "/tmp/example.cid"],
            ["--depend", "example"],
            ["example", "--all"],
            ["--force=synthetic-secret", "example"],
            ["--ignore=", "example"],
            ["-fv=synthetic-secret", "example"],
            ["-t"],
            ["--time"],
            ["-t=", "example"],
            ["-ft", "example"],
            ["--time=", "example"],
            ["--time=-2", "example"],
            ["-ft1.5", "example"],
            ["--time=9223372036854775808", "example"],
            ["--time", "--all", "example"],
            ["-f", "--", "--all"],
            ["-f", "../example"],
        )
        for command in (["rm"], ["container", "rm"]):
            for options in forms:
                with self.subTest(command=command, options=options):
                    result = self.assert_denied([*command, *options])
                    self.assertNotIn("synthetic-secret", result.stdout + result.stderr)

    def test_container_removal_keeps_provider_exit_status(self):
        arguments = ["rm", "-f", "example"]
        for exit_code in (1, 2, 125):
            with self.subTest(exit_code=exit_code):
                result, recorded = self.run_guard(
                    arguments, provider_exit_code=exit_code
                )
                self.assertEqual(result.returncode, exit_code)
                self.assertEqual(recorded, arguments)

    def test_devpod_account_file_copy_is_limited_to_private_temp_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for account_file in ("passwd", "group"):
                with self.subTest(account_file=account_file):
                    temporary = Path(temp_dir) / (
                        f"devpod_container_{account_file}_in1234"
                    )
                    temporary.write_text("synthetic\n", encoding="utf-8")
                    temporary.chmod(0o600)
                    host_path = str(temporary.resolve())
                    forms = (
                        ["cp", f"deadbeef:/etc/{account_file}", host_path],
                        ["cp", host_path, f"deadbeef:/etc/{account_file}"],
                    )
                    for arguments in forms:
                        result, recorded = self.run_guard(arguments)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(recorded, arguments)

            ordinary_file = Path(temp_dir) / "ordinary"
            ordinary_file.write_text("synthetic\n", encoding="utf-8")
            ordinary_file.chmod(0o600)
            self.assert_denied(["cp", "deadbeef:/etc/passwd", str(ordinary_file)])
            self.assert_denied(["cp", "deadbeef:/workspace/file", str(ordinary_file)])

    def test_devpod_build_is_checked_before_it_reaches_the_provider(self):
        with tempfile.TemporaryDirectory() as project_dir:
            project = Path(project_dir)
            dockerfile = project / "Dockerfile"
            dockerfile.write_text("FROM scratch AS development\n", encoding="utf-8")
            command = [
                "buildx",
                "build",
                "--load",
                "--build-arg",
                "BUILDKIT_INLINE_CACHE=1",
                "--cache-to",
                "type=registry,ref=example.invalid/devpod:cache,mode=max",
                "--label",
                "devpod.sh/id=example-workspace",
                "--target",
                "development",
                "-f",
                "Dockerfile",
                "-t",
                "example.invalid/devpod:latest",
                ".",
            ]
            result, recorded = self.run_guard(command, cwd=project_dir)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(recorded[:2], ["buildx", "build"])
            self.assertEqual(recorded[-1], str(project.resolve()))
            self.assertIn(str(dockerfile.resolve()), recorded)

            self.assert_denied(
                ["build", "--build-arg", "API_TOKEN=literal-secret", "."],
                cwd=project_dir,
            )

    def test_additional_build_paths_are_checked_even_when_they_look_like_images(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root)
            (project / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            assets = project / "assets"
            assets.mkdir()
            for path in ("assets", "./assets", str(assets)):
                with self.subTest(path=path):
                    result, recorded = self.run_guard(
                        ["build", "--build-context", f"extra={path}", "."], cwd=project
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(f"extra={assets.resolve()}", recorded)
            (assets / ".env").write_text("SYNTHETIC=value\n", encoding="utf-8")
            self.assert_denied(
                ["build", "--build-context", "extra=assets", "."], cwd=project
            )
            (project / "linked").symlink_to(assets, target_is_directory=True)
            (project / "broad").symlink_to(Path(root).parent, target_is_directory=True)
            for path in ("linked", "broad", "missing", "Dockerfile", ""):
                with self.subTest(path=path):
                    self.assert_denied(
                        ["build", "--build-context", f"extra={path}", "."], cwd=project
                    )

    def test_additional_build_images_require_a_reviewed_explicit_transport(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root)
            (project / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            for prefix in ("container-image://", "docker://", "docker-image://"):
                reference = f"extra={prefix}example.invalid/assets:latest"
                with self.subTest(prefix=prefix):
                    result, recorded = self.run_guard(
                        ["buildx", "build", "--build-context", reference, "."],
                        cwd=project,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(reference, recorded)
                    for image in (
                        "",
                        "/tmp/assets",
                        "oci-layout:/tmp/assets",
                        "../assets",
                    ):
                        self.assert_denied(
                            ["build", "--build-context", f"extra={prefix}{image}", "."],
                            cwd=project,
                        )
            for value in ("https://example.invalid/assets.tar", "file:///tmp/assets"):
                self.assert_denied(
                    ["build", "--build-context", f"extra={value}", "."], cwd=project
                )

    def test_build_ignore_descendant_exception_never_reaches_provider(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root)
            (project / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            (project / ".ssh").mkdir()
            (project / ".ssh" / "project-key").write_text(
                "synthetic\n", encoding="utf-8"
            )
            ignore = project / ".containerignore"
            ignore.write_text("*\n!.ssh/project-key\n", encoding="utf-8")
            self.assert_denied(["build", "."], cwd=project)
            ignore.write_text("*\n!.ssh/project-key\n.ssh\n", encoding="utf-8")
            result, _ = self.run_guard(["build", "."], cwd=project)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_dockerfile_literal_secrets_are_denied_without_printing_values(self):
        marker = "synthetic-dockerfile-secret"
        instructions = (
            f"ARG APP_API_TOKEN={marker}",
            f"ARG APP_API_TOKEN=${{APP_API_TOKEN:-{marker}}}",
            f"ENV APP_API_TOKEN={marker}",
            f"LABEL APP_API_TOKEN={marker}",
            f"RUN APP_API_TOKEN={marker} command",
            f"RUN env APP_API_TOKEN={marker} command",
            f"RUN export APP_API_TOKEN={marker}; command",
        )
        for instruction in instructions:
            with self.subTest(instruction=instruction.split(" ", 1)[0]):
                with tempfile.TemporaryDirectory() as project_dir:
                    project = Path(project_dir)
                    (project / "Dockerfile").write_text(
                        f"FROM scratch\n{instruction}\n", encoding="utf-8"
                    )

                    result = self.assert_denied(["build", "."], cwd=project)
                    self.assertIn("literal secret", result.stderr)
                    self.assertNotIn(marker, result.stderr)

    def test_dockerfile_secret_references_remain_supported(self):
        with tempfile.TemporaryDirectory() as project_dir:
            project = Path(project_dir)
            (project / "Dockerfile").write_text(
                "FROM scratch\nARG APP_API_TOKEN\nENV APP_API_TOKEN=${APP_API_TOKEN}\n",
                encoding="utf-8",
            )

            result, recorded = self.run_guard(["build", "."], cwd=project)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(recorded[-1], str(project.resolve()))

    def test_sensitive_build_context_paths_require_an_ignore_rule(self):
        sensitive_paths = (
            (".ssh", True),
            (".aws", True),
            (".env.production", False),
            (".netrc", False),
            (".npmrc", False),
        )
        for name, is_directory in sensitive_paths:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as root:
                project = Path(root)
                (project / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
                sensitive = project / name
                if is_directory:
                    sensitive.mkdir()
                else:
                    sensitive.write_text("synthetic\n", encoding="utf-8")

                result = self.assert_denied(["build", "."], cwd=project)
                self.assertIn("sensitive build-context path", result.stderr)
                self.assertIn(name, result.stderr)

                (project / ".dockerignore").write_text(f"{name}\n", encoding="utf-8")
                result, recorded = self.run_guard(["build", "."], cwd=project)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(recorded[-1], str(project.resolve()))

    def test_git_is_allowed_while_sensitive_build_paths_are_reported(self):
        with tempfile.TemporaryDirectory() as project_dir:
            project = Path(project_dir)
            (project / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            (project / ".git").mkdir()
            (project / ".env.local").write_text(
                "APP_ENV=development\n", encoding="utf-8"
            )

            result = self.assert_denied(["build", "."], cwd=project)

            self.assertIn(".env.local", result.stderr)
            self.assertNotIn(".git", result.stderr)
            self.assertIn("add every listed path", result.stderr)
            self.assertIn("copied image inputs", result.stderr)

            (project / ".containerignore").write_text(".env.local\n", encoding="utf-8")
            result, recorded = self.run_guard(["build", "."], cwd=project)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(recorded[-1], str(project.resolve()))

    def test_containerignore_takes_precedence_for_sensitive_context_paths(self):
        with tempfile.TemporaryDirectory() as project_dir:
            project = Path(project_dir)
            (project / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            (project / ".env.local").write_text("synthetic\n", encoding="utf-8")
            (project / ".dockerignore").write_text(".env.local\n", encoding="utf-8")
            (project / ".containerignore").write_text(
                "# selected by Podman\n", encoding="utf-8"
            )

            result = self.assert_denied(["build", "."], cwd=project)
            self.assertIn("sensitive build-context path", result.stderr)

            (project / ".containerignore").write_text(
                ".env.local\n!.env.local\n", encoding="utf-8"
            )
            result = self.assert_denied(["build", "."], cwd=project)
            self.assertIn("sensitive build-context path", result.stderr)

            (project / ".containerignore").write_text(".env.local\n", encoding="utf-8")
            result, recorded = self.run_guard(["build", "."], cwd=project)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(recorded[-1], str(project.resolve()))

    def test_devpod_build_context_can_be_separate_from_the_process_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            invocation_dir = root / "agent"
            build_context = root / "workspace"
            invocation_dir.mkdir()
            build_context.mkdir()
            dockerfile = build_context / "Dockerfile"
            dockerfile.write_text("FROM scratch\n", encoding="utf-8")

            command = [
                "buildx",
                "build",
                "--load",
                "--file",
                str(dockerfile),
                "--tag",
                "example.invalid/devpod:latest",
                str(build_context),
            ]
            result, recorded = self.run_guard(command, cwd=invocation_dir)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(recorded[-1], str(build_context.resolve()))

    def test_devpod_run_shape_keeps_devcontainer_read_only(self):
        with tempfile.TemporaryDirectory() as project_dir:
            project = Path(project_dir)
            devcontainer = project / ".devcontainer"
            devcontainer.mkdir()
            (devcontainer / "devcontainer.json").write_text("{}\n", encoding="utf-8")
            environment_file = project / ".env.local"
            environment_file.write_text("APP_ENV=development\n", encoding="utf-8")
            command = [
                "run",
                "--sig-proxy=false",
                "--mount",
                f"type=bind,source={project},target=/workspace,consistency=cached",
                "--userns",
                "keep-id",
                "--env-file",
                str(environment_file),
                "--add-host=host.docker.internal:host-gateway",
                "--ulimit",
                "nproc=8192:8192",
                "--pids-limit",
                "8192",
                "-l",
                "devpod.sh/id=example-workspace",
                "-d",
                "example.invalid/devpod:latest",
            ]
            result, recorded = self.run_guard(command, cwd=project_dir)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(environment_file.resolve()), recorded)
            self.assertIn("--pids-limit", recorded)
            self.assertIn("8192", recorded)
            expected_mount = (
                f"type=bind,src={devcontainer.resolve()},"
                "target=/workspace/.devcontainer,readonly=true"
            )
            self.assertIn(expected_mount, recorded)

    def test_devpod_hostname_defaults_to_workspace_and_preserves_explicit_names(self):
        label_forms = (
            ["--label", "devpod.sh/id=example-workspace"],
            ["--label=devpod.sh/id=example-workspace"],
            ["-ldevpod.sh/id=example-workspace"],
        )
        for labels in label_forms:
            for command in ("run", "create"):
                result, recorded = self.run_guard(
                    [command, *labels, "example.invalid/image"]
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--hostname=example-workspace", recorded)
        for explicit in (["--hostname", "custom"], ["-hcustom"], ["--no-hostname"]):
            result, recorded = self.run_guard(
                ["run", *label_forms[0], *explicit, "example.invalid/image"]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("--hostname=example-workspace", recorded)
        result, recorded = self.run_guard(
            ["run", "example.invalid/image", *label_forms[0]]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("--hostname=example-workspace", recorded)

    def test_option_values_cannot_disable_defaults_or_supply_workspace_labels(self):
        for value in (
            "--security-opt",
            "--cap-drop=ALL",
            "--pids-limit",
            "-ldevpod.sh/id=decoy",
        ):
            result, recorded = self.run_guard(
                ["run", "--entrypoint", value, "example.invalid/image"]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--security-opt=no-new-privileges", recorded)
            self.assertIn("--cap-drop=ALL", recorded)
            self.assertIn("--pids-limit=512", recorded)
            self.assertNotIn("--hostname=decoy", recorded)

    def test_keep_id_uid_gid_mapping_is_allowed_without_host_namespaces(self):
        for mode in (
            "keep-id:uid=10001,gid=10001",
            "keep-id:gid=999,uid=999,size=65536",
        ):
            result, recorded = self.run_guard(
                ["run", "--userns", mode, "example.invalid/image"]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(mode, recorded)
        for mode in (
            "host",
            "keep-id:uid=1,uid=0",
            "keep-id:size=0",
            "keep-id:uid=4294967295",
        ):
            self.assert_denied(["run", "--userns", mode, "example.invalid/image"])

    def test_devpod_run_shape_cannot_weaken_the_common_policy(self):
        with tempfile.TemporaryDirectory() as project_dir:
            project = Path(project_dir)
            environment_file = project / ".env.local"
            environment_file.write_text("APP_ENV=development\n", encoding="utf-8")
            forms = (
                ["--privileged"],
                ["--network", "host"],
                ["--add-host", "host.example:host-gateway"],
                ["--pids-limit", "32769"],
                ["--label", "API_TOKEN=literal-secret"],
                ["--label", "io.github.paranoid-podman.policy=1"],
                ["--label", "io.podman.compose.project=spoofed"],
                ["--label", "com.docker.compose.service=spoofed"],
            )
            for runtime_arguments in forms:
                with self.subTest(arguments=runtime_arguments):
                    self.assert_denied(
                        ["run", *runtime_arguments, "example.invalid/devpod:latest"],
                        cwd=project_dir,
                    )

    def test_tokens_after_image_are_not_parsed_as_runtime_options(self):
        result, arguments = self.run_guard(
            ["run", "example.invalid/image", "--privileged"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(arguments[-2:], ["example.invalid/image", "--privileged"])

    def test_option_separator_preserves_the_image_boundary(self):
        result, arguments = self.run_guard(
            ["run", "--rm", "--", "example.invalid/image", "--privileged"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            arguments[-3:], ["--", "example.invalid/image", "--privileged"]
        )

    def test_unsafe_option_order_never_changes_denial(self):
        safe_options = (["--rm"], ["--env=SAFE_VALUE=yes"], ["--pids-limit=128"])
        unsafe_options = (
            ["--privileged"],
            ["--network=host"],
            ["--security-opt=seccomp=unconfined"],
        )
        for unsafe in unsafe_options:
            for index in range(len(safe_options) + 1):
                ordered = [*safe_options[:index], unsafe, *safe_options[index:]]
                runtime = [argument for option in ordered for argument in option]
                with self.subTest(unsafe=unsafe, index=index):
                    self.assert_denied(["run", *runtime, "example.invalid/image"])

    def test_image_reference_cannot_select_a_file_transport(self):
        for image in (
            "/tmp/image",
            "docker-archive:/tmp/image.tar",
            "docker://example.invalid/image",
            "oci-archive:/tmp/image.tar",
        ):
            with self.subTest(image=image):
                self.assert_denied(["run", image])

    def test_attached_user_option_disables_incompatible_default_hardening(self):
        result, arguments = self.run_guard(["run", "-u1000", "example.invalid/image"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("--userns=keep-id", arguments)
        self.assertNotIn("--cap-drop=ALL", arguments)

    def test_explicit_root_container_user_is_denied(self):
        for option in ("--user=root", "-u0", "--user=000", "--user=1000:00"):
            with self.subTest(option=option):
                self.assert_denied(["run", option, "example.invalid/image"])

    def test_unknown_runtime_option_fails_closed(self):
        result = self.assert_denied(
            ["run", "--definitely-unknown", "value", "example.invalid/image"]
        )
        self.assertIn("--definitely-unknown", result.stderr)
        self.assertNotIn(" value ", result.stderr)

    def test_denial_does_not_echo_argument_values(self):
        marker = "synthetic-secret-that-must-not-be-logged"
        result = self.assert_denied(
            ["run", "--privileged", "-e", f"EXAMPLE={marker}", "example.invalid/image"]
        )
        self.assertNotIn(marker, result.stderr)

    def test_debug_output_does_not_echo_argument_values(self):
        marker = "synthetic-debug-value-that-must-not-be-logged"
        result, arguments = self.run_guard(
            ["run", "--env", f"EXAMPLE={marker}", "example.invalid/image"],
            extra_env={"PODMAN_GUARD_DEBUG": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(marker, " ".join(arguments or []))
        self.assertNotIn(marker, result.stdout + result.stderr)

    def test_existing_git_directory_gets_read_only_submount(self):
        with tempfile.TemporaryDirectory() as source_dir:
            git_dir = Path(source_dir) / ".git"
            git_dir.mkdir()
            result, arguments = self.run_guard(
                [
                    "run",
                    "--volume",
                    f"{source_dir}:/workspace",
                    "example.invalid/image",
                ],
                cwd=source_dir,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        expected = f"type=bind,src={git_dir},target=/workspace/.git,readonly=true"
        self.assertIn(expected, arguments)

    def test_missing_protected_paths_are_not_created(self):
        with tempfile.TemporaryDirectory() as source_dir:
            result, arguments = self.run_guard(
                [
                    "run",
                    "--volume",
                    f"{source_dir}:/workspace",
                    "example.invalid/image",
                ],
                cwd=source_dir,
            )
            self.assertFalse((Path(source_dir) / ".git").exists())
            self.assertFalse((Path(source_dir) / ".devcontainer").exists())

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any("/workspace/.git" in arg for arg in arguments or []))
        self.assertFalse(
            any("/workspace/.devcontainer" in arg for arg in arguments or [])
        )

    def test_worktree_git_file_gets_read_only_submount(self):
        with tempfile.TemporaryDirectory() as source_dir:
            git_file = Path(source_dir) / ".git"
            git_file.write_text("gitdir: ../synthetic-worktree\n", encoding="utf-8")
            result, arguments = self.run_guard(
                [
                    "run",
                    "--volume",
                    f"{source_dir}:/workspace",
                    "example.invalid/image",
                ],
                cwd=source_dir,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        expected = f"type=bind,src={git_file},target=/workspace/.git,readonly=true"
        self.assertIn(expected, arguments)

    def test_full_protected_filename_set_gets_read_only_submounts(self):
        protected_names = (
            ".containerignore",
            ".devcontainer",
            ".devcontainer.json",
            ".dockerignore",
            ".env.local",
            ".git-credentials",
            ".gitmodules",
            ".netrc",
            ".npmrc",
            ".pypirc",
            "devcontainer.json",
        )
        with tempfile.TemporaryDirectory() as source_dir:
            source = Path(source_dir)
            for name in protected_names:
                path = source / name
                if name == ".devcontainer":
                    path.mkdir()
                else:
                    path.write_text("synthetic\n", encoding="utf-8")
            result, arguments = self.run_guard(
                ["run", "--volume", ".:/workspace", "example.invalid/image"],
                cwd=source_dir,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        for name in protected_names:
            self.assertTrue(
                any(
                    f"target=/workspace/{name},readonly=true" in argument
                    for argument in arguments
                ),
                name,
            )

    def test_git_protection_can_be_disabled_without_unprotecting_configs(self):
        with tempfile.TemporaryDirectory() as source_dir:
            source = Path(source_dir)
            git_dir = source / ".git"
            devcontainer_dir = source / ".devcontainer"
            git_dir.mkdir()
            devcontainer_dir.mkdir()
            result, arguments = self.run_guard(
                ["run", "--volume", ".:/workspace", "example.invalid/image"],
                cwd=source_dir,
                extra_env={"PODMAN_GUARD_PROTECT_GIT": "0"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(
                any("target=/workspace/.git," in argument for argument in arguments)
            )
            self.assertTrue(
                any(
                    "target=/workspace/.devcontainer,readonly=true" in argument
                    for argument in arguments
                )
            )

    def test_nested_repository_metadata_gets_read_only_submount(self):
        with tempfile.TemporaryDirectory() as source_dir:
            nested_git = Path(source_dir) / "packages" / "app" / ".git"
            nested_git.mkdir(parents=True)
            result, arguments = self.run_guard(
                [
                    "run",
                    "--mount=type=bind,src=.,target=/workspace",
                    "example.invalid/image",
                ],
                cwd=source_dir,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        expected = (
            f"type=bind,src={nested_git},"
            "target=/workspace/packages/app/.git,readonly=true"
        )
        self.assertIn(expected, arguments)

    def test_direct_protected_file_bind_is_forced_read_only(self):
        with tempfile.TemporaryDirectory() as source_dir:
            config_file = Path(source_dir) / ".devcontainer.json"
            config_file.write_text("{}\n", encoding="utf-8")
            result, arguments = self.run_guard(
                ["run", "-v./.devcontainer.json:/config:rw", "example.invalid/image"],
                cwd=source_dir,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"-v{config_file}:/config:ro", arguments)

    def test_build_and_compose_files_remain_writable_outside_devcontainer(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / ".devcontainer").mkdir()
            for name in ("Dockerfile.dev", "Containerfile", "compose.yaml"):
                (project / name).write_text("synthetic\n", encoding="utf-8")
                (project / ".devcontainer" / name).write_text(
                    "synthetic\n", encoding="utf-8"
                )
            result, arguments = self.run_guard(
                ["run", "-v.:/workspace", "example.invalid/image"], cwd=temporary
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"-v{project}:/workspace:rw", arguments)
            self.assertEqual(
                [argument for argument in arguments if "readonly=true" in argument],
                [
                    f"type=bind,src={project}/.devcontainer,target=/workspace/.devcontainer,readonly=true"
                ],
            )
            for name, mode in (
                ("Dockerfile.dev", "rw"),
                ("Containerfile", "rw"),
                ("compose.yaml", "rw"),
                (".devcontainer/Dockerfile.dev", "ro"),
                (".devcontainer/compose.yaml", "ro"),
            ):
                with self.subTest(name=name):
                    result, arguments = self.run_guard(
                        ["run", f"-v./{name}:/config:rw", "example.invalid/image"],
                        cwd=temporary,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(f"-v{project / name}:/config:{mode}", arguments)

    def test_nested_mount_cannot_override_a_protected_bind(self):
        with tempfile.TemporaryDirectory() as source_dir:
            git_dir = Path(source_dir) / ".git"
            git_dir.mkdir()
            for project_mount in ("-v.:/workspace", "-v.:/workspace:ro"):
                with self.subTest(project_mount=project_mount):
                    self.assert_denied(
                        [
                            "run",
                            project_mount,
                            "--tmpfs=/workspace/.git/objects",
                            "example.invalid/image",
                        ],
                        cwd=source_dir,
                    )

    def test_existing_read_only_configuration_binds_are_preserved_before_separator(
        self,
    ):
        with tempfile.TemporaryDirectory() as source_dir:
            project = Path(source_dir).resolve()
            (project / ".git").mkdir()
            (project / ".devcontainer").mkdir()
            (project / "ordinary").mkdir()
            for parent_mode in ("rw", "ro"):
                result, arguments = self.run_guard(
                    [
                        "run",
                        "-v",
                        f".:/workspace:{parent_mode}",
                        "-v",
                        "./.git:/workspace/.git:ro",
                        "--",
                        "example.invalid/image",
                    ],
                    cwd=project,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(arguments[-2:], ["--", "example.invalid/image"])
                self.assertEqual(
                    sum("/workspace/.git" in value for value in arguments), 1
                )
                if parent_mode == "rw":
                    self.assertTrue(
                        any(
                            "target=/workspace/.devcontainer,readonly=true" in value
                            for value in arguments[: arguments.index("--")]
                        )
                    )
                self.assert_denied(
                    [
                        "run",
                        "-v",
                        f".:/workspace:{parent_mode}",
                        "-v",
                        "./ordinary:/workspace/.git:ro",
                        "example.invalid/image",
                    ],
                    cwd=project,
                )

    def test_individual_external_bind_is_allowed_but_ambiguous_sources_are_denied(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            outside = root / "outside"
            project.mkdir()
            outside.mkdir()
            (project / "outside-link").symlink_to(outside, target_is_directory=True)

            result, arguments = self.run_guard(
                ["run", "--volume", f"{outside}:/outside", "example.invalid/image"],
                cwd=project,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"{outside.resolve()}:/outside:rw", arguments)

            denied = (
                "./missing:/missing",
                "./outside-link:/linked",
                "..:/parent",
                "named-volume:/data",
            )
            for volume in denied:
                with self.subTest(volume=volume):
                    self.assert_denied(
                        ["run", "--volume", volume, "example.invalid/image"],
                        cwd=project,
                    )

    def test_broad_and_security_sensitive_host_binds_are_denied(self):
        with tempfile.TemporaryDirectory() as project_dir:
            # The policy protects the OS account home, independent of $HOME.
            for source in ("/", "/etc", pwd.getpwuid(os.getuid()).pw_dir):
                with self.subTest(source=source):
                    self.assert_denied(
                        ["run", "--volume", f"{source}:/host", "example.invalid/image"],
                        cwd=project_dir,
                    )

    def test_safe_bind_path_with_spaces_and_unicode_is_canonicalized(self):
        with tempfile.TemporaryDirectory() as source_dir:
            source = Path(source_dir) / "directory with spaces-λ"
            source.mkdir()
            result, arguments = self.run_guard(
                [
                    "run",
                    "--mount",
                    "type=bind,src=./directory with spaces-λ,target=/workspace",
                    "example.invalid/image",
                ],
                cwd=source_dir,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            f"type=bind,src={source},target=/workspace",
            arguments,
        )

    def test_ambiguous_bind_path_delimiters_are_denied(self):
        with tempfile.TemporaryDirectory() as source_dir:
            for name in ("directory,comma", "directory:colon", "directory\nnewline"):
                (Path(source_dir) / name).mkdir()
                with self.subTest(name=name):
                    self.assert_denied(
                        [
                            "run",
                            "--volume",
                            f"./{name}:/workspace",
                            "example.invalid/image",
                        ],
                        cwd=source_dir,
                    )

    def test_special_bind_source_is_denied(self):
        with tempfile.TemporaryDirectory() as source_dir:
            socket_path = Path(source_dir) / "service.sock"
            unix_socket = socket.socket(socket.AF_UNIX)
            try:
                try:
                    unix_socket.bind(str(socket_path))
                except OSError as error:
                    self.skipTest(f"filesystem sockets are unavailable: {error}")
                self.assert_denied(
                    ["run", "-v./service.sock:/socket", "example.invalid/image"],
                    cwd=source_dir,
                )
            finally:
                unix_socket.close()

    def test_anonymous_volume_and_tmpfs_mounts_are_allowed(self):
        result, arguments = self.run_guard(
            [
                "run",
                "--volume=/data",
                "--mount=type=volume,target=/cache",
                "--mount=type=tmpfs,target=/scratch,readonly=true",
                "example.invalid/image",
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--volume=/data", arguments)
        self.assertIn("--mount=type=volume,target=/cache", arguments)
        self.assertIn("--mount=type=tmpfs,target=/scratch,readonly=true", arguments)

    def test_named_volume_mount_is_denied(self):
        self.assert_denied(
            [
                "run",
                "--mount=type=volume,source=cache,target=/cache",
                "example.invalid/image",
            ]
        )

    def test_every_sensitive_key_is_unset(self):
        result, arguments = self.run_guard(["create", "example.invalid/image"])
        self.assertEqual(result.returncode, 0, result.stderr)
        for key in SENSITIVE_ENV_KEYS:
            self.assertIn(f"--unsetenv={key}", arguments)


if __name__ == "__main__":
    unittest.main()
