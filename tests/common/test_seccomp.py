"""The browser exception changes only chroot in the reviewed Podman baseline."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paranoid_podman.common import seccomp


class ChromiumSeccompTests(unittest.TestCase):
    def test_only_chroot_differs_from_reviewed_baseline(self):
        profile = json.loads(seccomp.CHROMIUM_PROFILE.read_bytes())
        chroot_rules = [r for r in profile["syscalls"] if "chroot" in r["names"]]
        self.assertEqual(len(chroot_rules), 1)
        rule = chroot_rules[0]
        self.assertEqual(rule["names"], ["chroot"])
        self.assertEqual(rule["action"], "SCMP_ACT_ALLOW")
        for key in ("args", "includes", "excludes"):
            self.assertFalse(rule[key])
        profile["syscalls"].remove(rule)
        # containers-common 0.69.1 baseline, removing only its chroot rules.
        canonical = json.dumps(profile, sort_keys=True, separators=(",", ":"))
        self.assertEqual(
            hashlib.sha256(canonical.encode()).hexdigest(),
            "70c40a12729f16c634152fcc74bc923b11b8b6ee4454e91b7df0a620ca7c372b",
        )
        self.assertEqual(profile["defaultAction"], "SCMP_ACT_ERRNO")
        self.assertEqual(
            seccomp.chromium_security_option(), f"seccomp={seccomp.CHROMIUM_PROFILE}"
        )

    def test_missing_modified_and_symlinked_profiles_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "profile.json"
            with patch.object(seccomp, "CHROMIUM_PROFILE", path):
                with self.assertRaisesRegex(ValueError, "unavailable"):
                    seccomp.chromium_security_option()
                path.write_text('{"defaultAction":"SCMP_ACT_ALLOW"}')
                with self.assertRaisesRegex(ValueError, "integrity"):
                    seccomp.chromium_security_option()
                path.unlink()
                path.symlink_to(Path(temporary) / "missing.json")
                with self.assertRaisesRegex(ValueError, "symlink"):
                    seccomp.chromium_security_option()

    def test_browser_identity_rejects_root_groups_and_malformed_values(self):
        for value in (
            None,
            True,
            False,
            0,
            "0",
            "00",
            "root",
            "pwuser:0",
            "1000:root",
            "",
            "1000:",
            "1:2:3",
            -1,
            [],
            "pw user",
        ):
            with self.subTest(value=value):
                self.assertFalse(seccomp.is_explicit_nonroot_user(value))
        for value in (1000, "1000", "pwuser", "1000:1000", "pwuser:pwuser"):
            with self.subTest(value=value):
                self.assertTrue(seccomp.is_explicit_nonroot_user(value))
