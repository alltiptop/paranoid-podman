"""Opt-in real SSH/helper tests; no containers, TCP listeners, or user keys.

The stock DevPod helper runs locally over stdio. OpenSSH multiplexing models
an IDE connection whose setup command finishes before terminal commands run.
"""

import os
import selectors
import shlex
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from paranoid_podman.devpod import provider as devpod_provider


@unittest.skipUnless(
    os.environ.get("PARANOID_PODMAN_RUN_DEVPOD_SSH_TESTS") == "1",
    "opt-in real DevPod SSH-helper test",
)
class DevPodSSHTransportTests(unittest.TestCase):
    def setUp(self):
        self.devpod = Path(
            os.environ.get("PARANOID_PODMAN_TEST_DEVPOD", "/usr/bin/devpod-cli")
        )
        if not self.devpod.is_absolute() or not self.devpod.is_file():
            self.skipTest("set PARANOID_PODMAN_TEST_DEVPOD to a real DevPod binary")
        for name in ("ssh", "ssh-keygen", "ssh-agent", "ssh-add"):
            if not shutil.which(name):
                self.skipTest(f"{name} is required")
        devpod_provider.validate_devpod_version(self.devpod)
        temporary = tempfile.TemporaryDirectory(prefix="pp-ssh-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.environment = os.environ.copy()
        self.environment.update(
            {
                "HOME": str(self.root),
                "ZDOTDIR": str(self.root),
                "SHELL": "/bin/sh",
                "SSH_AUTH_SOCK": str(self.root / "agent.sock"),
                "SSH_ASKPASS_REQUIRE": "never",
            }
        )
        self.environment.pop("SSH_AGENT_PID", None)
        self.key = self.root / "key"
        self.run_command(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)]
        )
        self.agent = subprocess.Popen(
            ["ssh-agent", "-D", "-a", self.environment["SSH_AUTH_SOCK"]],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=self.environment,
            start_new_session=True,
        )
        self.addCleanup(self.agent.stderr.close)
        self.addCleanup(self.stop_process, self.agent)

        def agent_ready():
            if self.agent.poll() is not None:
                self.fail(
                    f"ssh-agent exited: {self.agent.stderr.read().decode(errors='replace')}"
                )
            return (self.root / "agent.sock").is_socket()

        self.wait_for(agent_ready, "dedicated agent socket")
        self.run_command(["ssh-add", str(self.key)])
        self.expected = self.run_command(
            ["ssh-keygen", "-lf", str(self.key) + ".pub"]
        ).stdout.split()[1]
        self.masters = []
        self.forwarded_sockets = []
        self.addCleanup(self.remove_stale_test_sockets)

    def remove_stale_test_sockets(self):
        for path in self.forwarded_sockets:
            # Only paths returned by our synthetic helper sessions are eligible.
            if path.parent.parent != Path("/tmp") or not path.parent.name.startswith(
                "auth-agent"
            ):
                continue
            if path.is_socket() and not self.socket_accepts_connections(path):
                path.unlink()
                path.parent.rmdir()

    @staticmethod
    def socket_accepts_connections(path):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            return probe.connect_ex(str(path)) == 0

    def run_command(self, arguments, *, expected=0, environment=None):
        result = subprocess.run(
            arguments,
            env=environment or self.environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, expected, result.stderr)
        return result

    def wait_for(self, predicate, description):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        self.fail(f"timed out waiting for {description}")

    @staticmethod
    def stop_process(process):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)

    def ssh_arguments(self, forwarding):
        return [
            shutil.which("ssh"),
            "-F",
            "/dev/null",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "PubkeyAuthentication=no",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "ServerAliveInterval=2",
            "-o",
            "ServerAliveCountMax=2",
            "-o",
            f"ForwardAgent={forwarding}",
            "-o",
            f"IdentityAgent={self.environment['SSH_AUTH_SOCK']}",
        ]

    def start_master(self, *, forwarding="no", environment=None):
        helper = shlex.join([str(self.devpod), "helper", "ssh-server", "--stdio"])
        proxy = helper
        control = self.root / f"control-{len(self.masters)}"
        master_arguments = self.ssh_arguments(forwarding)
        if environment is not None and "SSH_AUTH_SOCK" not in environment:
            # Model the guard's IDE-only IdentityAgent none as well as its env.
            master_arguments[1:1] = ["-o", "IdentityAgent=none"]
        process = subprocess.Popen(
            [
                *master_arguments,
                "-M",
                "-N",
                "-S",
                str(control),
                "-o",
                f"ProxyCommand={proxy}",
                "test.invalid",
            ],
            env=environment or self.environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self.masters.append(process)
        self.addCleanup(process.stderr.close)
        self.addCleanup(self.close_master, process, control)

        def ready():
            if process.poll() is not None:
                self.fail(
                    f"SSH master exited: {process.stderr.read().decode(errors='replace')}"
                )
            return control.is_socket()

        self.wait_for(ready, "stdio SSH connection")
        return process, control

    def close_master(self, process, control):
        if process.poll() is None:
            try:
                subprocess.run(
                    [
                        *self.ssh_arguments("no"),
                        "-S",
                        str(control),
                        "-O",
                        "exit",
                        "test.invalid",
                    ],
                    env=self.environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.stop_process(process)

    def session(self, control, command, *, forwarding="no", expected=0):
        return self.run_command(
            [
                *self.ssh_arguments(forwarding),
                "-S",
                str(control),
                "test.invalid",
                command,
            ],
            expected=expected,
        )

    def socket_from_setup(self, control, *, forwarding="no"):
        value = self.session(
            control, 'printf "%s\\n" "$SSH_AUTH_SOCK"', forwarding=forwarding
        ).stdout.strip()
        self.assertTrue(value.startswith("/tmp/auth-agent"), value)
        path = Path(value)
        self.forwarded_sockets.append(path)
        return path

    def assert_project_identity(self, control):
        lines = self.session(control, "ssh-add -l").stdout.strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn(self.expected, lines[0])

    def test_original_session_socket_disappears_after_ide_setup(self):
        _process, control = self.start_master(forwarding="yes")
        socket = self.socket_from_setup(control, forwarding="yes")
        self.wait_for(lambda: not socket.exists(), "original setup socket removal")

    def held_setup(self, control):
        # Model an IDE extension reading setup results without closing the
        # channel. stdin stays open until this specific IDE session is closed.
        process = subprocess.Popen(
            [
                *self.ssh_arguments("yes"),
                "-S",
                str(control),
                "test.invalid",
                'printf "%s\\n" "$SSH_AUTH_SOCK"; cat >/dev/null',
            ],
            env=self.environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self.addCleanup(self.close_setup, process)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            self.assertTrue(selector.select(timeout=8), "setup did not report a socket")
            value = process.stdout.readline(4096).decode().strip()
        self.assertTrue(value.startswith("/tmp/auth-agent"), value)
        path = Path(value)
        self.forwarded_sockets.append(path)
        self.assertTrue(path.is_socket())
        return process, path

    def close_setup(self, process):
        if not process.stdin.closed:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.stop_process(process)
        process.stdout.close()
        process.stderr.close()

    def identity_at(self, control, path, *, expected=0):
        return (
            self.session(
                control,
                f"SSH_AUTH_SOCK={shlex.quote(str(path))} ssh-add -l",
                expected=expected,
            )
            .stdout.strip()
            .splitlines()
        )

    def test_held_setup_channel_supports_reconnect_and_independent_sessions(self):
        master, control = self.start_master(forwarding="yes")
        first, first_socket = self.held_setup(control)
        second, second_socket = self.held_setup(control)
        self.assertNotEqual(first_socket, second_socket)
        for path in (first_socket, second_socket):
            lines = self.identity_at(control, path)
            self.assertEqual(len(lines), 1)
            self.assertIn(self.expected, lines[0])
        self.close_setup(first)
        self.wait_for(lambda: not first_socket.exists(), "first setup socket removal")
        self.identity_at(control, first_socket, expected=2)
        self.assertIn(self.expected, self.identity_at(control, second_socket)[0])
        self.close_setup(second)
        self.wait_for(lambda: not second_socket.exists(), "second setup socket removal")
        self.close_master(master, control)

        _reconnected, new_control = self.start_master(forwarding="yes")
        third, third_socket = self.held_setup(new_control)
        self.assertNotIn(third_socket, (first_socket, second_socket))
        self.assertIn(self.expected, self.identity_at(new_control, third_socket)[0])
        self.identity_at(new_control, first_socket, expected=2)
        self.close_setup(third)
        self.wait_for(lambda: not third_socket.exists(), "reconnected socket removal")

    def test_ide_only_does_not_inherit_the_host_agent(self):
        environment = self.environment.copy()
        environment.pop("SSH_AUTH_SOCK")
        _process, control = self.start_master(environment=environment)
        result = self.session(
            control, 'if [ -n "${SSH_AUTH_SOCK:-}" ]; then exit 1; fi'
        )
        self.assertEqual(result.returncode, 0)
