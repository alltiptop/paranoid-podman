"""Value-free next steps keyed by explicit policy decision categories."""

import os
import sys

from paranoid_podman.common.errors import GuardViolation, ViolationCategory

GUIDANCE = {
    ViolationCategory.BUILD_CONTEXT: (
        "add every listed path to .containerignore or .dockerignore and retry, "
        "or run `paranoid-podman build-context protect .`; build-context "
        "exclusions affect copied image inputs, not the read-only protection "
        "applied to runtime workspace mounts"
    ),
    ViolationCategory.SECRET: (
        "replace the literal with variable interpolation or a project-local "
        "environment file, then rotate the value if it may have been exposed"
    ),
    ViolationCategory.INTERPOLATION: (
        "define the required variables in the project .env or caller "
        "environment, then rerun the command"
    ),
    ViolationCategory.PROVENANCE: (
        "recreate the container through the guard; for DevPod run `devpod up "
        "<workspace> --recreate` (inspection and cleanup remain allowed)"
    ),
    ViolationCategory.INTERRUPTED: (
        "rerun from an interactive terminal and review the request again"
    ),
    ViolationCategory.CANCELLED: (
        "rerun and type exactly `y` only if the reviewed action is intended"
    ),
    ViolationCategory.COMPOSE_FILE: (
        "create a Compose file in the project or select one explicitly with `-f`"
    ),
    ViolationCategory.PULL: (
        "use a locally reviewed image with `--pull=never`, or pull it separately first"
    ),
    ViolationCategory.PORT: (
        "use valid TCP/UDP port mappings with ports from 1 through 65535 and "
        "an optional IPv4 or IPv6 host address"
    ),
    ViolationCategory.HOST_MAPPING: (
        "use `host.docker.internal:host-gateway` or "
        "`host.containers.internal:host-gateway` for local development"
    ),
    ViolationCategory.NETWORK: (
        "use an isolated bridge, none, pasta, or slirp4netns network; host, "
        "joined, external, and custom networks are outside this policy"
    ),
    ViolationCategory.MOUNT: (
        "mount only the specific project-owned path that is needed and remove "
        "unsafe options; protected project files are made read-only "
        "automatically"
    ),
    ViolationCategory.ENVIRONMENT: (
        "pass only explicit non-sensitive NAME=value entries or a regular "
        "project-local environment file"
    ),
    ViolationCategory.PRIVILEGE: (
        "remove the privilege-expanding setting and use the guarded defaults; "
        "there is no policy bypass for this operation"
    ),
    ViolationCategory.INSTALLATION: (
        "run `./install.sh status`, verify the configured providers and "
        "dependencies, then update or reinstall if needed"
    ),
    ViolationCategory.UNSUPPORTED: (
        "this feature is not supported by the adapter; report the command and "
        "setting names without secrets so compatibility can be added"
    ),
    ViolationCategory.PROVIDER: (
        "check the Compose source and provider compatibility without exposing "
        "rendered values, then retry"
    ),
    ViolationCategory.INPUT: (
        "correct the reported path, value, or command shape and retry; the "
        "rejected operation was not passed to the provider"
    ),
    ViolationCategory.POLICY: (
        "remove or correct the reported item and retry; the guard will not pass "
        "an unreviewed operation to the provider"
    ),
}


def policy_guidance(category: ViolationCategory) -> str:
    """Return actionable guidance without inspecting or exposing input values."""
    return GUIDANCE[category]


def print_violation(prefix: str, error: GuardViolation) -> None:
    """Separate policy denials from compatibility and input errors on stderr."""

    if error.category is ViolationCategory.UNSUPPORTED:
        status = "UNSUPPORTED"
    elif error.category in {
        ViolationCategory.INPUT,
        ViolationCategory.PROVIDER,
        ViolationCategory.INSTALLATION,
        ViolationCategory.COMPOSE_FILE,
        ViolationCategory.INTERPOLATION,
    }:
        status = "ERROR"
    else:
        status = "BLOCKED"
    heading = f"{prefix}: {status} [{error.category.name.lower()}]"
    if (
        sys.stderr.isatty()
        and "NO_COLOR" not in os.environ
        and os.environ.get("TERM") != "dumb"
    ):
        heading = f"\033[1;31m{heading}\033[0m"
    print(f"{heading}\n{prefix}: {error}", file=sys.stderr)
    print(f"{prefix}: next step: {policy_guidance(error.category)}", file=sys.stderr)
