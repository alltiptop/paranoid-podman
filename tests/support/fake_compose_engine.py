#!/usr/bin/env python3
"""Record real Compose provider commands; never execute an engine operation."""

import json
import os
import sys
from pathlib import Path


def main():
    arguments = sys.argv[1:]
    with Path(os.environ["PARANOID_COMPOSE_ENGINE_LOG"]).open(
        "a", encoding="utf-8"
    ) as output:
        output.write(json.dumps(arguments) + "\n")
    if arguments[:1] == ["--version"]:
        print("podman version 6.1.1")
    elif arguments[:1] == ["ps"] and arguments[-1:] == ["json"]:
        print("[]")
    elif arguments[:1] == ["inspect"]:
        if "--format" in arguments or "-f" in arguments:
            print("synthetic-image-id")
        else:
            print(
                json.dumps(
                    [
                        {
                            "NetworkSettings": {
                                "Ports": {"8000/tcp": [{"HostPort": "18000"}]}
                            }
                        }
                    ]
                )
            )
    elif arguments[:1] == ["images"]:
        print("example_app_1 example.invalid/app latest abcdef123456 10MB")
    elif arguments[:1] in (["run"], ["exec"]):
        stdin_path = os.environ.get("PARANOID_COMPOSE_ENGINE_STDIN")
        if stdin_path:
            Path(stdin_path).write_text(sys.stdin.read(), encoding="utf-8")
        return int(os.environ.get("PARANOID_COMPOSE_ENGINE_RUN_EXIT", "0"))
    elif arguments[:1] == ["build"]:
        return int(os.environ.get("PARANOID_COMPOSE_ENGINE_BUILD_EXIT", "0"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
