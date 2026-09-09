"""Host scope checks retain account-home and rejection-order semantics."""

import errno
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from paranoid_podman.common.paths import (
    host_source_rejection,
    is_unreadable_foreign_directory,
)


class HostSourceTests(unittest.TestCase):
    def test_unreadable_directory_exception_is_limited_to_foreign_ownership(self):
        uid = os.geteuid()
        cases = (
            (errno.EACCES, stat.S_IFDIR | 0o700, uid + 1, True),
            (errno.EPERM, stat.S_IFDIR | 0o700, uid + 1, True),
            (errno.EACCES, stat.S_IFDIR | 0o000, uid, False),
            (errno.EACCES, stat.S_IFLNK | 0o777, uid + 1, False),
            (errno.EACCES, stat.S_IFREG | 0o600, uid + 1, False),
            (errno.EIO, stat.S_IFDIR | 0o700, uid + 1, False),
            (errno.ENOENT, stat.S_IFDIR | 0o700, uid + 1, False),
        )
        for code, mode, owner, expected in cases:
            with self.subTest(code=code, mode=mode, owner=owner):
                metadata = SimpleNamespace(st_mode=mode, st_uid=owner)
                with patch.object(Path, "lstat", return_value=metadata):
                    error = OSError(code, "synthetic", "/synthetic/data")
                    self.assertEqual(is_unreadable_foreign_directory(error), expected)

    def test_unreadable_directory_requires_verifiable_metadata(self):
        with patch.object(Path, "lstat", side_effect=PermissionError):
            self.assertFalse(
                is_unreadable_foreign_directory(
                    PermissionError(errno.EACCES, "synthetic", "/synthetic/data")
                )
            )
        self.assertFalse(
            is_unreadable_foreign_directory(PermissionError(errno.EACCES, "synthetic"))
        )

    def test_system_sources_are_rejected_before_account_lookup(self):
        with patch("paranoid_podman.common.paths.pwd.getpwuid", side_effect=KeyError):
            self.assertEqual(
                host_source_rejection(Path("/etc/project"), Path("/etc/project")),
                "broad or security-sensitive host source",
            )
            self.assertEqual(
                host_source_rejection(Path("/workspace"), Path("/workspace")),
                "current user home cannot be resolved",
            )

    def test_account_home_scope_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            project = home / "projects" / "app"
            account = SimpleNamespace(pw_dir=str(home))
            with patch(
                "paranoid_podman.common.paths.pwd.getpwuid", return_value=account
            ):
                for source, expected in (
                    (home, "full user-home source"),
                    (home / "projects", "source broader than the current project"),
                    (home / ".ssh" / "config", "security-sensitive user source"),
                    (home / ".config" / "containers", "security-sensitive user source"),
                    (project, None),
                    (project / "src", None),
                    (home / "sibling", None),
                ):
                    with self.subTest(source=source):
                        self.assertEqual(
                            host_source_rejection(source, project), expected
                        )
