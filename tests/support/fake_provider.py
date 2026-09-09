#!/usr/bin/env python3
"""Test-only Podman replacement that records arguments without running containers."""

import json
import os
import sys
from pathlib import Path


def main() -> int:
    arguments = sys.argv[1:]
    if (
        len(arguments) == 5
        and arguments[:2] == ["container", "inspect"]
        and arguments[2] == "--format"
        and "io.github.paranoid-podman.policy" in arguments[3]
    ):
        policy = os.environ.get("PARANOID_PODMAN_TEST_POLICY_VERSION", "1")
        installation = os.environ.get("PARANOID_PODMAN_TEST_INSTALLATION_ID", "a" * 64)
        sys.stdout.write(f"{policy}\t{installation}\n")
        return int(os.environ.get("PARANOID_PODMAN_TEST_INSPECT_EXIT_CODE", "0"))

    output_path = os.environ.get("PARANOID_PODMAN_TEST_OUTPUT")
    if not output_path:
        print("fake-provider: PARANOID_PODMAN_TEST_OUTPUT is required", file=sys.stderr)
        return 126

    Path(output_path).write_text(json.dumps(arguments), encoding="utf-8")
    environment_output = os.environ.get("PARANOID_PODMAN_TEST_ENV_OUTPUT")
    if environment_output:
        inspected_keys = json.loads(
            os.environ.get("PARANOID_PODMAN_TEST_ENV_KEYS", "[]")
        )
        forwarded = sorted(key for key in inspected_keys if key in os.environ)
        Path(environment_output).write_text(json.dumps(forwarded), encoding="utf-8")
    return int(os.environ.get("PARANOID_PODMAN_TEST_EXIT_CODE", "0"))


if __name__ == "__main__":
    raise SystemExit(main())
