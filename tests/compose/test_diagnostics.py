"""Typed categories survive source decoration and redacted provider failures."""

import unittest
from pathlib import Path
from unittest.mock import patch

from paranoid_podman.common.diagnostics import policy_guidance
from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.compose.cli import run_project_command
from paranoid_podman.compose.diagnostics import classify_provider_failure
from paranoid_podman.compose.errors import PolicyViolation
from paranoid_podman.compose.models import Invocation


class DiagnosticTests(unittest.TestCase):
    def test_source_locations_and_guidance_do_not_depend_on_rejection_wording(self):
        invocation = Invocation("up", [], [], None, [], Path("."), "example", [])
        for reason in ("missing required interpolation", "reworded rejection"):
            with self.subTest(reason=reason):
                error = PolicyViolation(
                    reason, category=ViolationCategory.INTERPOLATION
                )
                with (
                    patch(
                        "paranoid_podman.compose.cli.preflight_compose_inputs",
                        side_effect=error,
                    ),
                    patch(
                        "paranoid_podman.compose.diagnostics.source_review_locations",
                        return_value=["compose.yaml:2: image (value hidden)"],
                    ) as locations,
                    patch("paranoid_podman.compose.cli.render_config") as render,
                    self.assertRaises(PolicyViolation) as caught,
                ):
                    run_project_command(invocation)
                render.assert_not_called()
                locations.assert_called_once_with(
                    invocation,
                    include_interpolation=True,
                    category=ViolationCategory.INTERPOLATION,
                    service_name=None,
                )
                self.assertEqual(
                    caught.exception.category, ViolationCategory.INTERPOLATION
                )
                self.assertIn(reason, str(caught.exception))
                self.assertIn("compose.yaml:2", str(caught.exception))
                self.assertIn(
                    "define the required variables",
                    policy_guidance(caught.exception.category),
                )

    def test_provider_failure_is_redacted_before_reporting_and_categorized_once(self):
        message, category = classify_provider_failure(
            "required variable SYNTHETIC_SECRET=do-not-print"
        )
        self.assertEqual(message, "environment interpolation failed")
        self.assertIs(category, ViolationCategory.INTERPOLATION)
        message, category = classify_provider_failure("SYNTHETIC_SECRET=do-not-print")
        self.assertEqual(message, "provider rejected the configuration")
        self.assertIs(category, ViolationCategory.PROVIDER)
