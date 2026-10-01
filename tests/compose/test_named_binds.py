"""Named local-driver binds obey the ordinary host-mount policy."""

import copy
import os
import tempfile
import unittest
from pathlib import Path

from paranoid_podman.compose.errors import PolicyViolation
from paranoid_podman.compose.models import Invocation
from paranoid_podman.compose.policy import validate_and_rewrite


class NamedBindTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name).resolve() / "project"
        self.data = self.project / "data" / "database"
        self.data.mkdir(parents=True)
        self.invocation = Invocation(
            "up", [], [], None, [], self.project, "example", []
        )
        self.definition = {
            "driver": "local",
            "driver_opts": {"type": "none", "o": "bind", "device": str(self.data)},
        }
        self.model = {
            "services": {
                "database": {
                    "image": "example.invalid/database",
                    "volumes": ["database:/var/lib/postgresql/data"],
                },
                "backup": {
                    "image": "example.invalid/backup",
                    "volumes": [
                        {
                            "type": "volume",
                            "source": "database",
                            "target": "/data",
                            "read_only": True,
                            "volume": {"nocopy": True},
                        }
                    ],
                },
            },
            "volumes": {"database": self.definition, "cache": None},
        }

    def rewrite(self):
        return validate_and_rewrite(copy.deepcopy(self.model), self.invocation)

    def test_shared_named_bind_uses_host_data_and_preserves_service_access(self):
        rewritten = self.rewrite()
        for name, read_only in (("database", False), ("backup", True)):
            mount = rewritten["services"][name]["volumes"][0]
            self.assertEqual(mount["type"], "bind")
            self.assertEqual(mount["source"], str(self.data))
            self.assertEqual(mount["read_only"], read_only)
            self.assertEqual(mount["bind"], {"create_host_path": False})
        self.assertEqual(rewritten["volumes"], {"cache": {}})
        self.assertEqual(list(self.data.iterdir()), [])

    def test_default_driver_and_driver_read_only_are_supported(self):
        del self.definition["driver"]
        for mode in ("bind", "bind,rw", "rw,bind", "bind,ro", "ro,bind"):
            with self.subTest(mode=mode):
                self.definition["driver_opts"]["o"] = mode
                rewritten = self.rewrite()
                self.assertEqual(
                    rewritten["services"]["database"]["volumes"][0]["read_only"],
                    "ro" in mode.split(","),
                )
                self.assertTrue(
                    rewritten["services"]["backup"]["volumes"][0]["read_only"]
                )

    def test_protected_children_and_explicit_read_only_submounts_are_preserved(self):
        protected = self.data / ".devcontainer"
        protected.mkdir()
        (self.data / ".env").write_text("MODE=dev\n", encoding="utf-8")
        mounts = self.rewrite()["services"]["database"]["volumes"]
        for child in (".devcontainer", ".env"):
            mount = next(m for m in mounts if m["target"].endswith("/" + child))
            self.assertEqual(mount["source"], str(self.data / child))
            self.assertTrue(mount["read_only"])
        self.model["services"]["database"]["volumes"].append(
            f"{protected}:/var/lib/postgresql/data/.devcontainer:ro"
        )
        mounts = self.rewrite()["services"]["database"]["volumes"]
        self.assertEqual(len(mounts), 3)

    def test_protected_source_cannot_be_made_writable(self):
        protected = self.data / ".devcontainer"
        protected.mkdir()
        self.definition["driver_opts"]["device"] = str(protected)
        mount = self.rewrite()["services"]["database"]["volumes"][0]
        self.assertTrue(mount["read_only"])

    def test_named_bind_cannot_hide_protected_paths(self):
        (self.data / ".devcontainer").mkdir()
        self.model["services"]["database"]["volumes"].append(
            "cache:/var/lib/postgresql/data/.devcontainer"
        )
        with self.assertRaisesRegex(PolicyViolation, "overrides a protected"):
            self.rewrite()

    def test_driver_options_cannot_bypass_host_source_checks(self):
        link = self.project / "link"
        link.symlink_to(self.data, target_is_directory=True)
        fifo = self.project / "fifo"
        os.mkfifo(fifo)
        regular = self.project / "regular"
        regular.touch()
        for device in (
            "/",
            "/etc",
            str(self.project.parent),
            str(link),
            str(fifo),
            str(regular),
            str(self.project / "missing"),
            "./data/database",
            "",
            None,
        ):
            with self.subTest(device=device):
                self.definition["driver_opts"]["device"] = device
                with self.assertRaises(PolicyViolation):
                    self.rewrite()

    def test_unreviewed_driver_options_and_volume_settings_are_rejected(self):
        options = self.definition["driver_opts"].copy()
        invalid = [
            None,
            [],
            {},
            {**options, "type": "nfs"},
            {**options, "uid": "1000"},
            {"type": "none", "o": "bind"},
        ]
        invalid.extend(
            {**options, "o": mode}
            for mode in (
                "rbind",
                "bind,U",
                "bind,idmap",
                "bind,z",
                "bind,ro,rw",
                "bind,shared",
                "bind,uid=1000",
                "bind,",
                None,
                ["bind"],
            )
        )
        for value in invalid:
            with self.subTest(options=value):
                self.definition["driver_opts"] = value
                with self.assertRaises(PolicyViolation):
                    self.rewrite()
        self.definition["driver_opts"] = options
        for key, value in (("driver", "nfs"), ("external", True), ("name", "other")):
            with self.subTest(key=key):
                definition = {**self.definition, key: value}
                self.model["volumes"]["database"] = definition
                with self.assertRaises(PolicyViolation):
                    self.rewrite()

    def test_explicit_image_copy_is_rejected(self):
        self.model["services"]["backup"]["volumes"][0]["volume"]["nocopy"] = False
        with self.assertRaisesRegex(PolicyViolation, "image copy"):
            self.rewrite()

    def test_ordinary_named_volume_still_uses_the_engine_volume(self):
        self.model["volumes"]["database"] = {"driver": "local"}
        mount = self.rewrite()["services"]["database"]["volumes"][0]
        self.assertEqual(mount["type"], "volume")
        self.assertEqual(mount["source"], "database")
