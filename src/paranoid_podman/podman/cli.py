"""Podman command routing and policy errors to exit status."""

import sys
from typing import NoReturn

from paranoid_podman.common.diagnostics import print_violation
from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.podman.arguments import printable_option
from paranoid_podman.podman.build import build_command
from paranoid_podman.podman.commands import (
    build_info_command,
    build_inspect_command,
    build_logs_command,
    build_ps_command,
    build_pull_or_push_command,
    build_remove_command,
    build_stop_command,
    build_tag_command,
)
from paranoid_podman.podman.errors import PolicyViolation, reject
from paranoid_podman.podman.execution import (
    debug_exec,
    execute_compose,
    execute_copy,
    execute_exec,
    execute_start,
)
from paranoid_podman.podman.runtime import build_run_create_command
from paranoid_podman.podman.settings import REAL_PODMAN, validate_configuration


def dispatch(argv: list[str]) -> NoReturn:
    validate_configuration()
    if not argv or argv in (
        ["help"],
        ["version"],
        ["-h"],
        ["--help"],
        ["-v"],
        ["--version"],
    ):
        debug_exec([REAL_PODMAN, *argv])

    if argv[0] in {"run", "create"}:
        debug_exec(build_run_create_command(argv, 0))

    if len(argv) >= 2 and argv[0] == "container" and argv[1] in {"run", "create"}:
        debug_exec(build_run_create_command(argv, 1))

    if argv[0] == "ps":
        debug_exec(build_ps_command(argv, 0))

    if len(argv) >= 2 and argv[0] == "container" and argv[1] in {"ls", "ps"}:
        debug_exec(build_ps_command(argv, 1))

    if argv[0] == "inspect":
        debug_exec(build_inspect_command(argv, 0))

    if len(argv) >= 2 and argv[0] == "container" and argv[1] == "inspect":
        debug_exec(build_inspect_command(argv, 1, "container"))

    if len(argv) >= 2 and argv[0] == "image" and argv[1] == "inspect":
        debug_exec(build_inspect_command(argv, 1, "image"))

    if argv[0] == "info":
        debug_exec(build_info_command(argv))

    if argv[0] == "start":
        execute_start(argv, 0)

    if len(argv) >= 2 and argv[0] == "container" and argv[1] == "start":
        execute_start(argv, 1)

    if argv[0] == "stop":
        debug_exec(build_stop_command(argv, 0))

    if len(argv) >= 2 and argv[0] == "container" and argv[1] == "stop":
        debug_exec(build_stop_command(argv, 1))

    if argv[0] == "logs":
        debug_exec(build_logs_command(argv, 0))

    if len(argv) >= 2 and argv[0] == "container" and argv[1] == "logs":
        debug_exec(build_logs_command(argv, 1))

    if argv[0] == "exec":
        execute_exec(argv, 0)

    if len(argv) >= 2 and argv[0] == "container" and argv[1] == "exec":
        execute_exec(argv, 1)

    if argv[0] in {"pull", "push"}:
        debug_exec(build_pull_or_push_command(argv))

    if argv[0] == "tag":
        debug_exec(build_tag_command(argv))

    if argv[0] == "rm":
        debug_exec(build_remove_command(argv, 0))

    if len(argv) >= 2 and argv[0] == "container" and argv[1] == "rm":
        debug_exec(build_remove_command(argv, 1))

    if argv[0] == "cp":
        execute_copy(argv)

    if argv[0] == "build":
        debug_exec(build_command(argv, 0))

    if argv == ["buildx", "version"]:
        debug_exec([REAL_PODMAN, *argv])

    if len(argv) >= 2 and argv[0] == "buildx" and argv[1] == "build":
        debug_exec(build_command(argv, 1))

    if argv[0] == "compose":
        execute_compose(argv[1:])

    if argv[0].startswith("-"):
        reject(
            f"blocked global option {printable_option(argv[0])}; "
            "guarded commands do not accept Podman global overrides",
            category=ViolationCategory.POLICY,
        )
    reject(
        "blocked unreviewed Podman command; use the real Podman binary "
        "deliberately for host administration",
        category=ViolationCategory.UNSUPPORTED,
    )


def main() -> int:
    try:
        dispatch(sys.argv[1:])
    except PolicyViolation as error:
        print_violation("podman-guard", error)
        return 125
    return 0
