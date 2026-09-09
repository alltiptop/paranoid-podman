#!/usr/bin/env python3
"""Test-only `podman compose` frontend with no container side effects."""

import json
import os
import sys
from pathlib import Path

import yaml


def main() -> int:
    arguments = sys.argv[1:]
    if arguments[:2] in (["network", "ls"], ["network", "inspect"]):
        networks = json.loads(os.environ.get("PARANOID_COMPOSE_TEST_NETWORKS", "{}"))
        if arguments == ["network", "ls", "--format", "{{.Name}}"]:
            sys.stdout.write("".join(f"{name}\n" for name in networks))
        elif len(arguments) == 5 and arguments[2] == "--format":
            sys.stdout.write(networks.get(arguments[4], "missing") + "\n")
        else:
            return 126
        return int(os.environ.get("PARANOID_COMPOSE_TEST_NETWORK_EXIT_CODE", "0"))

    if (
        len(arguments) == 5
        and arguments[:2] == ["container", "inspect"]
        and arguments[2] == "--format"
        and "io.github.paranoid-podman.policy" in arguments[3]
    ):
        versions = json.loads(
            os.environ.get("PARANOID_COMPOSE_TEST_POLICY_VERSIONS", "{}")
        )
        policy = str(
            versions.get(
                arguments[4],
                os.environ.get("PARANOID_COMPOSE_TEST_POLICY_VERSION", "1"),
            )
        )
        installation = os.environ.get("PARANOID_COMPOSE_TEST_INSTALLATION_ID", "a" * 64)
        sys.stdout.write(f"{policy}\t{installation}\n")
        return int(os.environ.get("PARANOID_COMPOSE_TEST_INSPECT_EXIT_CODE", "0"))

    if (
        len(arguments) == 6
        and arguments[:2] == ["ps", "--all"]
        and arguments[2] == "--filter"
        and arguments[3].startswith("label=io.podman.compose.project=")
        and arguments[4:] == ["--format", "{{.ID}}"]
    ):
        containers = json.loads(
            os.environ.get("PARANOID_COMPOSE_TEST_PROJECT_CONTAINERS", "[]")
        )
        sys.stdout.write("".join(f"{container}\n" for container in containers))
        return int(os.environ.get("PARANOID_COMPOSE_TEST_PS_EXIT_CODE", "0"))

    output_path = os.environ.get("PARANOID_COMPOSE_TEST_OUTPUT")
    if not output_path:
        print("fake-compose: PARANOID_COMPOSE_TEST_OUTPUT is required", file=sys.stderr)
        return 126

    record = {
        "argv": arguments,
        "forwarded_sensitive_environment": sorted(
            key for key in ("SSH_AUTH_SOCK", "SYNTHETIC_API_TOKEN") if key in os.environ
        ),
    }
    try:
        env_file_index = arguments.index("--env-file")
        env_file_path = Path(arguments[env_file_index + 1])
        record["env_file"] = env_file_path.read_text(encoding="utf-8")
    except (IndexError, OSError, ValueError):
        record["env_file"] = None
    if not (arguments and arguments[-1] == "config"):
        try:
            file_index = arguments.index("-f")
            snapshot_path = Path(arguments[file_index + 1])
            record["snapshot"] = snapshot_path.read_text(encoding="utf-8")
            snapshot = yaml.safe_load(record["snapshot"])
            record["service_env_files"] = [
                Path(
                    definition if isinstance(definition, str) else definition["path"]
                ).read_text(encoding="utf-8")
                for service in snapshot.get("services", {}).values()
                for definition in service.get("env_file", [])
            ]
        except (
            AttributeError,
            IndexError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
            yaml.YAMLError,
        ):
            record["snapshot"] = None
            record["service_env_files"] = None
    with Path(output_path).open("a", encoding="utf-8") as output:
        output.write(json.dumps(record) + "\n")

    if arguments and arguments[-1] == "config":
        sys.stdout.write(
            os.environ.get("PARANOID_COMPOSE_TEST_CONFIG", "services: {}\n")
        )
        sys.stderr.write(os.environ.get("PARANOID_COMPOSE_TEST_CONFIG_ERROR", ""))
        return int(os.environ.get("PARANOID_COMPOSE_TEST_CONFIG_EXIT_CODE", "0"))

    if "ls" in arguments:
        sys.stdout.write("[]\n")

    return int(os.environ.get("PARANOID_COMPOSE_TEST_EXIT_CODE", "0"))


if __name__ == "__main__":
    raise SystemExit(main())
