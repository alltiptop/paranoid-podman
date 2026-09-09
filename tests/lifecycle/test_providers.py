"""Provider discovery must not chain installations through their public wrappers."""

import os
from pathlib import Path
from unittest import mock

from paranoid_podman.lifecycle import bootstrap, cli, launchers, providers
from paranoid_podman.lifecycle.errors import LifecycleError
from tests.support.lifecycle import LifecycleFixture


class ProviderDiscoveryTests(LifecycleFixture):
    def setUp(self):
        super().setUp()
        self.foreign_bin = self.test_root / "old bin"
        self.foreign_bin.mkdir()
        self.foreign_root = self.test_root / "old library '$`\nfiles"
        self.devpod = self.provider_directory / "devpod-cli"
        self.write_devpod(self.devpod)
        for name in ("podman", "podman-compose", "devpod"):
            wrapper = self.foreign_bin / name
            wrapper.write_bytes(launchers.command_launcher(self.foreign_root, name))
            wrapper.chmod(0o755)

    def test_other_installation_on_path_is_skipped_without_probing_it(self):
        with (
            mock.patch.dict(
                os.environ, {"PATH": f"{self.foreign_bin}:{self.provider_directory}"}
            ),
            mock.patch.object(
                providers,
                "provider_output",
                side_effect=AssertionError("unexpected execution"),
            ),
        ):
            self.assertEqual(
                providers.discover_provider(
                    "podman", self.install_root, self.binary_directory
                ),
                self.podman,
            )
            self.assertEqual(
                providers.discover_provider(
                    "podman-compose", self.install_root, self.binary_directory
                ),
                self.compose,
            )
            self.assertEqual(
                providers.discover_devpod(self.install_root, self.binary_directory),
                str(self.devpod),
            )
        self.assertFalse(self.foreign_root.exists())

    def test_explicit_wrapper_paths_are_rejected_including_allowed_devpod_backup(self):
        for name in ("podman", "podman-compose"):
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(LifecycleError, "wrapper; select the real"),
            ):
                providers.resolve_provider(
                    str(self.foreign_bin / name),
                    name,
                    self.install_root,
                    self.binary_directory,
                )
        devpod = self.foreign_bin / "devpod"
        with self.assertRaisesRegex(LifecycleError, "wrapper; select the real"):
            providers.resolve_devpod_provider(
                str(devpod), self.install_root, self.binary_directory, (devpod,)
            )

    def test_aliases_of_installed_wrappers_are_skipped(self):
        aliases = self.test_root / "aliases"
        aliases.mkdir()
        (aliases / "podman").symlink_to(self.foreign_bin / "podman")
        (aliases / "podman-compose").hardlink_to(self.foreign_bin / "podman-compose")
        with mock.patch.dict(
            os.environ, {"PATH": f"{aliases}:{self.provider_directory}"}
        ):
            self.assertEqual(
                providers.discover_provider(
                    "podman", self.install_root, self.binary_directory
                ),
                self.podman,
            )
            self.assertEqual(
                providers.discover_provider(
                    "podman-compose", self.install_root, self.binary_directory
                ),
                self.compose,
            )

    def test_foreign_devpod_in_new_command_directory_is_not_adopted(self):
        self.binary_directory.mkdir()
        (self.binary_directory / "devpod").symlink_to(self.foreign_bin / "devpod")
        with mock.patch.dict(
            os.environ, {"PATH": f"{self.binary_directory}:{self.provider_directory}"}
        ):
            self.assertEqual(
                providers.discover_devpod(self.install_root, self.binary_directory),
                str(self.devpod),
            )

    def test_stale_launcher_is_recognized_without_requiring_its_installation(self):
        wrapper = self.foreign_bin / "podman-compose"
        wrapper.write_text(
            '#!/usr/bin/env bash\nset -euo pipefail\n\nexec /tmp/pp-old/lib/current/launch podman-compose "$@"\n'
        )
        self.assertTrue(providers.is_guard_launcher(wrapper))
        wrapper.write_text(
            '#!/bin/sh\n# See /current/launch in documentation\nexec /usr/bin/podman-compose "$@"\n'
        )
        self.assertFalse(providers.is_guard_launcher(wrapper))
        self.assertFalse(providers.is_guard_launcher(Path("/dev/null")))

    def test_missing_update_reports_install_command_without_preparation(self):
        with (
            mock.patch.object(bootstrap, "prepare") as prepare,
            self.assertRaisesRegex(
                LifecycleError, r"no installation found.*\./install.sh install"
            ),
        ):
            cli.main(
                [
                    "update",
                    "--bindir",
                    str(self.binary_directory),
                    "--libdir",
                    str(self.install_root),
                ]
            )
        prepare.assert_not_called()
        self.assertFalse(self.install_root.exists())
