"""Unreadable data must not disable readable project configuration protection."""

import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paranoid_podman.common.errors import GuardViolation
from paranoid_podman.compose.mounts import normalize_volume as compose_volume
from paranoid_podman.podman.mounts import normalize_volume as podman_volume


class BindDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name).resolve()
        self.data = self.project / "database" / "pgdata"
        self.data.mkdir(parents=True)
        (self.data / "synthetic-database-file").write_text(
            "synthetic\n", encoding="utf-8"
        )
        self.config = self.project / "nested" / "app" / ".devcontainer.json"
        self.config.parent.mkdir(parents=True)
        self.config.write_text("services: {}\n", encoding="utf-8")
        self.git = self.project / ".git"
        self.git.mkdir()

    def make_unreadable(self, path):
        if os.geteuid() == 0:
            self.skipTest("requires ordinary user directory permissions")
        path.chmod(0)
        self.addCleanup(path.chmod, 0o700)
        with self.assertRaises(PermissionError), os.scandir(path):
            self.fail("directory remained readable")

    def test_unreadable_service_data_keeps_bind_writable_and_other_configs_protected(
        self,
    ):
        self.make_unreadable(self.data)
        # Simulate only the ownership comparison; directory access really fails.
        # This keeps the test unprivileged without chown or a container runtime.
        with patch(
            "paranoid_podman.common.paths.os.geteuid", return_value=os.geteuid() + 1
        ):
            mount, additions, _ = compose_volume(".:/workspace", self.project, set())
            direct = podman_volume(".:/workspace", self.project)
        self.assertFalse(mount["read_only"])
        self.assertEqual(
            {item["target"] for item in additions},
            {
                "/workspace/.git",
                "/workspace/nested/app/.devcontainer.json",
            },
        )
        self.assertTrue(all(item["read_only"] for item in additions))
        self.assertEqual(direct.value, f"{self.project}:/workspace:rw")
        self.assertEqual(
            set(direct.mount.protected_targets),
            {
                "/workspace/.git",
                "/workspace/nested/app/.devcontainer.json",
            },
        )
        self.assertEqual(self.data.stat().st_mode & 0o777, 0)

    def test_unreadable_service_data_can_itself_be_the_bind_source(self):
        self.make_unreadable(self.data)
        for suffix, read_only in (("", False), (":ro", True)):
            with (
                self.subTest(read_only=read_only),
                patch(
                    "paranoid_podman.common.paths.os.geteuid",
                    return_value=os.geteuid() + 1,
                ),
            ):
                specification = f"./database/pgdata:/data{suffix}"
                mount, additions, _ = compose_volume(specification, self.project, set())
                direct = podman_volume(specification, self.project)
                self.assertEqual(mount["read_only"], read_only)
                self.assertEqual(additions, [])
                self.assertEqual(
                    direct.value, f"{self.data}:/data:{'ro' if read_only else 'rw'}"
                )

    def test_user_owned_directory_cannot_hide_configuration_with_chmod(self):
        self.make_unreadable(self.config.parent)
        for adapter in (compose_volume, podman_volume):
            with (
                self.subTest(adapter=adapter.__module__),
                self.assertRaises(GuardViolation),
            ):
                if adapter is compose_volume:
                    adapter(".:/workspace", self.project, set())
                else:
                    adapter(".:/workspace", self.project)

    def test_unreadable_git_directory_still_gets_a_read_only_submount(self):
        self.make_unreadable(self.git)
        with patch(
            "paranoid_podman.common.paths.os.geteuid", return_value=os.geteuid() + 1
        ):
            _, additions, _ = compose_volume(".:/workspace", self.project, set())
            direct = podman_volume(".:/workspace", self.project)
        self.assertTrue(
            any(
                item["target"] == "/workspace/.git" and item["read_only"]
                for item in additions
            )
        )
        self.assertIn((str(self.git), "/workspace/.git"), direct.mount.protected_mounts)

    def test_other_directory_errors_are_not_ignored(self):
        original = os.scandir

        def broken_scandir(path):
            if Path(path) == self.data:
                raise OSError(errno.EIO, "synthetic disk error", str(path))
            return original(path)

        with (
            patch("os.scandir", side_effect=broken_scandir),
            patch(
                "paranoid_podman.common.paths.os.geteuid",
                return_value=os.geteuid() + 1,
            ),
        ):
            with self.assertRaises(GuardViolation):
                compose_volume(".:/workspace", self.project, set())
            with self.assertRaises(GuardViolation):
                podman_volume(".:/workspace", self.project)
