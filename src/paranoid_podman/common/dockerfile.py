"""Dockerfile instruction parsing and literal-secret checks; reads the given file."""

import re
import shlex
from pathlib import Path

from paranoid_podman.common.environment import is_sensitive_env_key
from paranoid_podman.common.errors import BuildInputViolation, ViolationCategory
from paranoid_podman.common.settings import MAX_BUILD_CONTROL_FILE_BYTES


def _indirect_secret_value(value: str) -> bool:
    value = value.strip().strip("\"'")
    return bool(
        re.fullmatch(
            r"\$(?:[A-Za-z_][A-Za-z0-9_]*|"
            r"\{[A-Za-z_][A-Za-z0-9_]*(?:(?:\?|:\?)[^}]*)?\})",
            value,
        )
    )


def _dockerfile_instructions(text: str) -> list[tuple[int, str, str]]:
    instructions: list[tuple[int, str, str]] = []
    escape = "\\"
    logical = ""
    start_line = 0

    for line_number, physical_line in enumerate(text.splitlines(), start=1):
        stripped = physical_line.strip()
        if not logical and stripped.lower().startswith("# escape="):
            selected = stripped.split("=", 1)[1].strip()
            if selected in {"\\", "`"}:
                escape = selected
            continue
        if not logical and (not stripped or stripped.startswith("#")):
            continue
        if not logical:
            start_line = line_number

        continued = physical_line.rstrip().endswith(escape)
        fragment = physical_line.rstrip()
        if continued:
            fragment = fragment[:-1]
        logical = f"{logical} {fragment.strip()}".strip()
        if continued:
            continue

        instruction, separator, arguments = logical.partition(" ")
        if instruction:
            instructions.append((start_line, instruction.upper(), arguments))
        logical = ""

    if logical:
        instruction, _separator, arguments = logical.partition(" ")
        instructions.append((start_line, instruction.upper(), arguments))
    return instructions


def _assignment_secret(
    instruction: str,
    arguments: str,
) -> str | None:
    if instruction == "ARG":
        key, separator, value = arguments.strip().partition("=")
        if separator and is_sensitive_env_key(key) and value:
            return None if _indirect_secret_value(value) else key
        return None

    try:
        tokens = shlex.split(arguments, comments=False, posix=True)
    except ValueError as error:
        raise BuildInputViolation(
            f"blocked malformed {instruction} instruction in Dockerfile",
            category=ViolationCategory.INPUT,
        ) from error
    if not tokens:
        return None

    assignments: list[tuple[str, str]] = []
    if "=" not in tokens[0]:
        if len(tokens) >= 2:
            assignments.append((tokens[0], " ".join(tokens[1:])))
    else:
        for token in tokens:
            key, separator, value = token.partition("=")
            if separator:
                assignments.append((key, value))

    for key, value in assignments:
        if is_sensitive_env_key(key) and value and not _indirect_secret_value(value):
            return key
    return None


def lint_dockerfile(dockerfile: Path) -> None:
    """Reject obvious literal secrets without exposing their values."""

    try:
        data = dockerfile.read_bytes()
    except OSError as error:
        raise BuildInputViolation(
            "blocked unreadable Dockerfile", category=ViolationCategory.POLICY
        ) from error
    if len(data) > MAX_BUILD_CONTROL_FILE_BYTES:
        raise BuildInputViolation(
            "blocked Dockerfile larger than 1 MiB", category=ViolationCategory.POLICY
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BuildInputViolation(
            "blocked non-UTF-8 Dockerfile", category=ViolationCategory.POLICY
        ) from error
    if "\x00" in text:
        raise BuildInputViolation(
            "blocked Dockerfile containing a NUL byte",
            category=ViolationCategory.POLICY,
        )

    run_assignment = re.compile(
        r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)="
        r"(\"[^\"]*\"|'[^']*'|[^\s;&|]+)"
    )
    for line_number, instruction, arguments in _dockerfile_instructions(text):
        secret_key: str | None = None
        if instruction in {"ARG", "ENV", "LABEL"}:
            secret_key = _assignment_secret(instruction, arguments)
        elif instruction == "RUN":
            for match in run_assignment.finditer(arguments):
                key, value = match.groups()
                if (
                    is_sensitive_env_key(key)
                    and value.strip("\"'")
                    and not _indirect_secret_value(value)
                ):
                    secret_key = key
                    break
        if secret_key is not None:
            raise BuildInputViolation(
                "blocked literal secret assigned to "
                f"{secret_key} in Dockerfile line {line_number}",
                category=ViolationCategory.SECRET,
            )
