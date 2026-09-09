"""Consumer-specific environment behavior at the shared policy boundary."""

import os
import unittest
from types import MappingProxyType
from unittest.mock import patch

from paranoid_podman.common.environment import (
    ProviderEnvironmentProfile,
    is_sensitive_env_key,
    provider_environment,
    sanitized_provider_environment,
)
from paranoid_podman.lifecycle import providers as lifecycle


class ProviderEnvironmentTests(unittest.TestCase):
    def test_cors_settings_are_not_credentials(self):
        for key in ("CORS_CREDENTIALS", "CORS_ALLOW_CREDENTIALS", "cors_credentials"):
            self.assertFalse(is_sensitive_env_key(key))
        for key in ("GOOGLE_APPLICATION_CREDENTIALS", "APP_CREDENTIALS", "APP_TOKEN"):
            self.assertTrue(is_sensitive_env_key(key))

    def test_consumer_profiles_preserve_their_distinct_exclusions(self):
        environment = MappingProxyType(
            dict.fromkeys(
                (
                    "PATH",
                    "LANG",
                    "HOME",
                    "APP_MODE",
                    "API_KEY",
                    "PASSWORD",
                    "TOKEN",
                    "http_proxy",
                    "DISPLAY",
                    "SSH_AUTH_SOCK",
                    "SSH_AGENT_PID",
                    "PODMAN_GUARD_INSTALLATION_ID",
                    "CONTAINERS_CONF",
                    "TMPDIR",
                    "custom_Password",
                    "custom_CONNECTION_STRING",
                    "containers_conf",
                ),
                "synthetic-value",
            )
        )
        guard_keys = {"PATH", "LANG", "HOME", "APP_MODE", "containers_conf"}
        lifecycle_keys = guard_keys | {
            "API_KEY",
            "PASSWORD",
            "TOKEN",
            "http_proxy",
            "DISPLAY",
        }
        for profile, expected in (
            (ProviderEnvironmentProfile.GUARD, guard_keys),
            (ProviderEnvironmentProfile.LIFECYCLE, lifecycle_keys),
        ):
            with self.subTest(profile=profile):
                result = provider_environment(environment, profile=profile)
                self.assertEqual(result, dict.fromkeys(expected, "synthetic-value"))
                result["APP_MODE"] = "changed"
                self.assertEqual(environment["APP_MODE"], "synthetic-value")

    def test_current_environment_is_read_at_each_call(self):
        with patch.dict(
            os.environ, {"APP_MODE": "first", "TOKEN": "fixture"}, clear=True
        ):
            self.assertEqual(sanitized_provider_environment(), {"APP_MODE": "first"})
            self.assertEqual(
                lifecycle.sanitized_provider_environment(),
                {"APP_MODE": "first", "TOKEN": "fixture"},
            )
            os.environ["APP_MODE"] = "second"
            os.environ["CUSTOM_SECRET"] = "fixture"  # noqa: S105 - synthetic test value
            self.assertEqual(sanitized_provider_environment(), {"APP_MODE": "second"})
            self.assertEqual(
                lifecycle.sanitized_provider_environment(),
                {"APP_MODE": "second", "TOKEN": "fixture"},
            )
