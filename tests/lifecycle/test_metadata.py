"""Only the release metadata format with verified provenance is supported."""

import hashlib
import unittest
from pathlib import Path

from paranoid_podman.lifecycle.errors import LifecycleError
from paranoid_podman.lifecycle.metadata import (
    installation_id_from_record,
    validate_metadata_header,
)
from paranoid_podman.lifecycle.settings import MANIFEST_FORMAT, PROJECT_NAME


class MetadataTests(unittest.TestCase):
    def test_metadata_requires_the_current_format(self):
        path = Path(".install.json")
        metadata = {"format": MANIFEST_FORMAT, "project": PROJECT_NAME}
        validate_metadata_header(metadata, path)
        for format_value in (None, 0, 1, 3, "2"):
            with (
                self.subTest(format_value=format_value),
                self.assertRaisesRegex(
                    LifecycleError, "unrecognized lifecycle metadata"
                ),
            ):
                validate_metadata_header({**metadata, "format": format_value}, path)

    def test_missing_provenance_is_not_generated_during_validation(self):
        installation_id = "a" * 64
        record = {"installation_id": installation_id}
        manifest = {
            "installation_id_sha256": hashlib.sha256(
                installation_id.encode()
            ).hexdigest()
        }
        self.assertEqual(installation_id_from_record(record, manifest), installation_id)
        for supplied_record, supplied_manifest in (
            ({}, {}),
            ({}, manifest),
            (record, {}),
            ({"installation_id": "invalid"}, manifest),
            (record, {"installation_id_sha256": "b" * 64}),
        ):
            with (
                self.subTest(record=supplied_record, manifest=supplied_manifest),
                self.assertRaises(LifecycleError),
            ):
                installation_id_from_record(supplied_record, supplied_manifest)
