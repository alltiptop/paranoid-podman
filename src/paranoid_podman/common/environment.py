"""Environment filtering for guard execution and lifecycle provider checks."""

import os
from collections.abc import Mapping
from enum import Enum

SENSITIVE_ENV_KEYS = {
    "ACCESS_TOKEN",
    "ALL_PROXY",
    "all_proxy",
    "API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AZURE_CLIENT_SECRET",
    "AUTH_TOKEN",
    "CLIENT_SECRET",
    "CONTAINER_HOST",
    "COOKIE",
    "DATABASE_URL",
    "DBUS_SESSION_BUS_ADDRESS",
    "DISPLAY",
    "DOCKER_AUTH_CONFIG",
    "DOCKER_CONFIG",
    "DOCKER_HOST",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GITLAB_TOKEN",
    "GIT_ASKPASS",
    "GIT_SSH",
    "GIT_SSH_COMMAND",
    "GNUPGHOME",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GPG_AGENT_INFO",
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "KUBECONFIG",
    "NO_PROXY",
    "no_proxy",
    "NPM_TOKEN",
    "PASSWORD",
    "PGPASSWORD",
    "PODMAN_HOST",
    "PYPI_API_TOKEN",
    "PRIVATE_KEY",
    "REGISTRY_AUTH_FILE",
    "SSH_AGENT_PID",
    "SSH_ASKPASS",
    "SSH_AUTH_SOCK",
    "SECRET",
    "TOKEN",
    "VAULT_TOKEN",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
}
SENSITIVE_ENV_KEYS_UPPER = {key.upper() for key in SENSITIVE_ENV_KEYS}
SENSITIVE_ENV_SUFFIXES = (
    "_ACCESS_KEY",
    "_API_KEY",
    "_AUTH_TOKEN",
    "_CONNECTION_STRING",
    "_COOKIE",
    "_CREDENTIAL",
    "_CREDENTIALS",
    "_PASSWORD",
    "_PRIVATE_KEY",
    "_SECRET",
    "_SESSION_TOKEN",
    "_TOKEN",
)
PROVIDER_CONTROL_ENV_KEYS = {
    "CONTAINER_CONNECTION",
    "CONTAINER_HOST",
    "CONTAINER_PROXY",
    "CONTAINER_SSHKEY",
    "CONTAINERS_CONF",
    "CONTAINERS_POLICY",
    "CONTAINERS_REGISTRIES_CONF",
    "CONTAINERS_STORAGE_CONF",
    "PODMAN_CONNECTIONS_CONF",
    "PODMAN_GUARD_INSTALLATION_ID",
    "PODMAN_HOST",
    "STORAGE_DRIVER",
    "STORAGE_OPTS",
    "TMPDIR",
}


LIFECYCLE_CONTROL_ENV_KEYS = PROVIDER_CONTROL_ENV_KEYS | {
    "SSH_AGENT_PID",
    "SSH_AUTH_SOCK",
}


class ProviderEnvironmentProfile(Enum):
    GUARD = "guard"
    LIFECYCLE = "lifecycle"


def is_sensitive_env_key(key: str) -> bool:
    normalized = key.upper()
    # These CORS switches contain policy settings, not credential material.
    if normalized in {"CORS_CREDENTIALS", "CORS_ALLOW_CREDENTIALS"}:
        return False
    return normalized in SENSITIVE_ENV_KEYS_UPPER or normalized.endswith(
        SENSITIVE_ENV_SUFFIXES
    )


def provider_environment(
    environment: Mapping[str, str], *, profile: ProviderEnvironmentProfile
) -> dict[str, str]:
    """Copy an environment with the consumer's existing exclusions applied."""
    if profile is ProviderEnvironmentProfile.GUARD:
        return {
            key: value
            for key, value in environment.items()
            if key not in PROVIDER_CONTROL_ENV_KEYS and not is_sensitive_env_key(key)
        }
    if profile is ProviderEnvironmentProfile.LIFECYCLE:
        return {
            key: value
            for key, value in environment.items()
            if key not in LIFECYCLE_CONTROL_ENV_KEYS
            and not key.upper().endswith(SENSITIVE_ENV_SUFFIXES)
        }
    raise ValueError("unknown provider environment profile")


def sanitized_provider_environment() -> dict[str, str]:
    """Read the current environment and apply the Podman/Compose guard profile."""
    return provider_environment(os.environ, profile=ProviderEnvironmentProfile.GUARD)
