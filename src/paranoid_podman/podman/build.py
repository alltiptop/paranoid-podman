"""Build policy and provider arguments; reads build contexts and Dockerfiles."""

import re
import stat
from pathlib import Path

from paranoid_podman.common.build_inputs import (
    is_safe_image_reference,
    validate_build_context,
    validate_build_inputs,
)
from paranoid_podman.common.environment import is_sensitive_env_key
from paranoid_podman.common.errors import BuildInputViolation, ViolationCategory
from paranoid_podman.podman.errors import reject
from paranoid_podman.podman.paths import (
    is_within,
    project_directory,
    validate_host_source_scope,
)
from paranoid_podman.podman.settings import REAL_PODMAN
from paranoid_podman.podman.validation import (
    validate_image_reference,
    validate_inspection_value,
    validate_label,
)


def resolve_build_path(
    value: str,
    base_dir: Path,
    *,
    boundary: Path | None,
    directory: bool,
) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    try:
        if candidate.is_symlink():
            reject("blocked symlinked build input", category=ViolationCategory.POLICY)
        resolved = candidate.resolve(strict=True)
        metadata = resolved.stat()
    except (OSError, RuntimeError):
        reject("blocked missing build input", category=ViolationCategory.INPUT)
    if boundary is not None and not is_within(resolved, boundary):
        reject(
            "blocked Dockerfile outside the build context",
            category=ViolationCategory.INPUT,
        )
    if directory and not stat.S_ISDIR(metadata.st_mode):
        reject(
            "blocked build context that is not a directory",
            category=ViolationCategory.POLICY,
        )
    if not directory and not stat.S_ISREG(metadata.st_mode):
        reject(
            "blocked Dockerfile that is not a regular file",
            category=ViolationCategory.POLICY,
        )
    return resolved


def validate_build_argument(value: str) -> None:
    key, separator, argument_value = value.partition("=")
    if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        reject("blocked malformed build argument", category=ViolationCategory.INPUT)
    if is_sensitive_env_key(key) and argument_value:
        reject(
            "blocked literal sensitive build argument",
            category=ViolationCategory.SECRET,
        )


def normalize_build_context_argument(
    value: str,
    invocation_dir: Path,
) -> str:
    name, separator, context = value.partition("=")
    if (
        not separator
        or not context
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name)
    ):
        reject(
            "blocked malformed additional build context",
            category=ViolationCategory.INPUT,
        )
    for prefix in ("container-image://", "docker://", "docker-image://"):
        if context.startswith(prefix):
            if not is_safe_image_reference(context.removeprefix(prefix)):
                reject(
                    "blocked invalid additional build image",
                    category=ViolationCategory.INPUT,
                )
            return value
    if "://" in context:
        reject(
            "blocked unreviewed additional build context transport",
            category=ViolationCategory.UNSUPPORTED,
        )
    resolved = resolve_build_path(
        context,
        invocation_dir,
        boundary=None,
        directory=True,
    )
    validate_host_source_scope(resolved, invocation_dir)
    try:
        validate_build_context(resolved)
    except BuildInputViolation as error:
        reject(str(error), category=error.category)
    return f"{name}={resolved}"


def validate_build_cache(value: str) -> None:
    fields: dict[str, str] = {}
    for field in value.split(","):
        key, separator, field_value = field.partition("=")
        if not separator or not field_value or key in fields:
            reject(
                "blocked malformed build cache setting",
                category=ViolationCategory.INPUT,
            )
        fields[key] = field_value
    if set(fields) - {"mode", "ref", "type"} or fields.get("type") != "registry":
        reject(
            "blocked build cache outside a registry reference",
            category=ViolationCategory.INPUT,
        )
    if "mode" in fields and fields["mode"] not in {"max", "min"}:
        reject("blocked invalid build cache mode", category=ViolationCategory.INPUT)
    if "ref" not in fields:
        reject(
            "blocked build cache without a registry reference",
            category=ViolationCategory.POLICY,
        )
    validate_image_reference(fields["ref"])


def build_command(argv: list[str], command_index: int) -> list[str]:
    invocation_dir = project_directory()
    arguments = argv[command_index + 1 :]
    if not arguments or arguments[-1].startswith("-"):
        reject("missing build context", category=ViolationCategory.INPUT)
    context = resolve_build_path(
        arguments[-1],
        invocation_dir,
        boundary=None,
        directory=True,
    )
    validate_host_source_scope(context, invocation_dir)

    forwarded: list[str] = []
    dockerfile: Path | None = None
    index = 0
    while index < len(arguments) - 1:
        argument = arguments[index]
        if not argument.startswith("-"):
            reject("blocked extra build context", category=ViolationCategory.POLICY)
        if argument in {"--load", "--no-cache", "--pull", "--push"}:
            forwarded.append(argument)
            index += 1
            continue
        if argument in {
            "-f",
            "-t",
            "--build-arg",
            "--build-context",
            "--cache-from",
            "--cache-to",
            "--file",
            "--label",
            "--platform",
            "--progress",
            "--tag",
            "--target",
        }:
            if index + 1 >= len(arguments) - 1:
                reject(
                    f"missing value for {argument}", category=ViolationCategory.INPUT
                )
            value = arguments[index + 1]
            validate_inspection_value(value, "build option")
            if argument in {"-f", "--file"}:
                dockerfile = resolve_build_path(
                    value,
                    invocation_dir,
                    boundary=context,
                    directory=False,
                )
                value = str(dockerfile)
            elif argument in {"-t", "--tag"}:
                validate_image_reference(value)
            elif argument == "--build-arg":
                validate_build_argument(value)
            elif argument == "--build-context":
                value = normalize_build_context_argument(value, invocation_dir)
            elif argument == "--label":
                validate_label(value)
            elif argument == "--target" and not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value
            ):
                reject("blocked invalid build target", category=ViolationCategory.INPUT)
            elif argument == "--platform" and not re.fullmatch(
                r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?",
                value,
            ):
                reject(
                    "blocked invalid build platform", category=ViolationCategory.INPUT
                )
            elif argument == "--progress" and value not in {
                "auto",
                "plain",
                "quiet",
                "rawjson",
                "tty",
            }:
                reject(
                    "blocked invalid build progress mode",
                    category=ViolationCategory.INPUT,
                )
            elif argument in {"--cache-from", "--cache-to"}:
                validate_build_cache(value)
            forwarded.extend((argument, value))
            index += 2
            continue
        reject(
            "blocked unreviewed build option", category=ViolationCategory.UNSUPPORTED
        )

    if dockerfile is None:
        dockerfile = resolve_build_path(
            str(context / "Dockerfile"),
            invocation_dir,
            boundary=context,
            directory=False,
        )
    try:
        validate_build_inputs(context, dockerfile)
    except BuildInputViolation as error:
        reject(str(error), category=error.category)
    return [REAL_PODMAN, *argv[: command_index + 1], *forwarded, str(context)]
