import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from paranoid_podman.devpod import models as devpod_models

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class DevPodFixture(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.home = self.root / "home"
        self.runtime = self.root / "runtime"
        self.home.mkdir(mode=0o700)
        self.runtime.mkdir(mode=0o700)
        self.environment = mock.patch.dict(
            os.environ,
            {
                "HOME": str(self.home),
                "XDG_RUNTIME_DIR": str(self.runtime),
            },
            clear=False,
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def workspace(self, name="example-workspace"):
        return devpod_models.Workspace("default", name, self.home / ".ssh" / "config")

    @staticmethod
    def devpod_block(workspace, executable="/usr/bin/devpod", forwarding="yes"):
        host = f"{workspace}.devpod"
        return (
            f"# DevPod Start {host}\n"
            f"Host {host}\n"
            f"  ForwardAgent {forwarding}\n"
            "  LogLevel error\n"
            "  StrictHostKeyChecking no\n"
            "  UserKnownHostsFile /dev/null\n"
            "  HostKeyAlgorithms rsa-sha2-256,rsa-sha2-512,ssh-rsa\n"
            f'  ProxyCommand "{executable}" ssh --stdio --context default '
            f"--user vscode {workspace}\n"
            "  User vscode\n"
            f"# DevPod End {host}\n"
        )

    def executable(self, name, content):
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
        return path
