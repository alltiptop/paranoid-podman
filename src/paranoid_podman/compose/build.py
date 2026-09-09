"""Validate Compose builds and their local Dockerfile/context inputs."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

from paranoid_podman.common.build_inputs import validate_build_inputs
from paranoid_podman.common.errors import BuildInputViolation, ViolationCategory
from paranoid_podman.compose.dotenv import DOTENV_KEY_PATTERN
from paranoid_podman.compose.errors import reject
from paranoid_podman.compose.paths import is_within
from paranoid_podman.compose.resources import validate_labels
from paranoid_podman.compose.schema import NAME_PATTERN, _mapping

ALLOWED_BUILD_KEYS = {"args", "context", "dockerfile", "labels", "target"}


def _resolve_project_build_path(
    value: Any,
    base: Path,
    project_dir: Path,
    *,
    expected_directory: bool,
) -> Path:
    if not isinstance(value, str) or not value:
        reject(
            "blocked resolved configuration: malformed build path",
            category=ViolationCategory.INPUT,
        )
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    try:
        resolved = candidate.resolve(strict=True)
        mode = resolved.stat().st_mode
    except (OSError, RuntimeError):
        reject(
            "blocked resolved configuration: missing build path",
            category=ViolationCategory.INPUT,
        )
    if not is_within(resolved, project_dir):
        reject(
            "blocked resolved configuration: build path outside the project",
            category=ViolationCategory.INPUT,
        )
    if expected_directory and not stat.S_ISDIR(mode):
        reject(
            "blocked resolved configuration: build context is not a directory",
            category=ViolationCategory.POLICY,
        )
    if not expected_directory and not stat.S_ISREG(mode):
        reject(
            "blocked resolved configuration: Dockerfile is not a regular file",
            category=ViolationCategory.POLICY,
        )
    return resolved


def validate_build(value: Any, project_dir: Path) -> dict[str, Any]:
    build: dict[str, Any]
    if isinstance(value, str):
        build = {"context": value}
    else:
        build = dict(
            _mapping(value, "malformed Compose build", category=ViolationCategory.INPUT)
        )
    if set(build) - ALLOWED_BUILD_KEYS:
        reject(
            "blocked resolved configuration: unreviewed Compose build setting",
            category=ViolationCategory.UNSUPPORTED,
        )

    context = _resolve_project_build_path(
        build.get("context", "."),
        project_dir,
        project_dir,
        expected_directory=True,
    )
    dockerfile_value = build.get("dockerfile")
    if dockerfile_value is None:
        candidates = (
            "Containerfile",
            "ContainerFile",
            "containerfile",
            "Dockerfile",
            "DockerFile",
            "dockerfile",
        )
        dockerfile_value = next(
            (name for name in candidates if (context / name).is_file()), None
        )
        if dockerfile_value is None:
            reject(
                "blocked resolved configuration: build has no Dockerfile",
                category=ViolationCategory.POLICY,
            )
    dockerfile = _resolve_project_build_path(
        dockerfile_value,
        context,
        project_dir,
        expected_directory=False,
    )
    try:
        validate_build_inputs(context, dockerfile)
    except BuildInputViolation as error:
        reject(str(error), category=error.category)

    args = build.get("args", [])
    if isinstance(args, dict):
        entries = list(args.items())
    elif isinstance(args, list):
        entries = []
        for item in args:
            if not isinstance(item, str):
                reject(
                    "blocked resolved configuration: malformed build argument",
                    category=ViolationCategory.INPUT,
                )
            key, separator, argument_value = item.partition("=")
            if not separator:
                reject(
                    "blocked resolved configuration: implicit host build argument",
                    category=ViolationCategory.POLICY,
                )
            entries.append((key, argument_value))
    else:
        reject(
            "blocked resolved configuration: malformed build arguments",
            category=ViolationCategory.INPUT,
        )
    for key, argument_value in entries:
        if not isinstance(key, str) or not DOTENV_KEY_PATTERN.fullmatch(key):
            reject(
                "blocked resolved configuration: invalid build argument name",
                category=ViolationCategory.INPUT,
            )
        if argument_value is None:
            reject(
                "blocked resolved configuration: implicit host build argument",
                category=ViolationCategory.POLICY,
            )
        if not isinstance(argument_value, (str, int, float, bool)):
            reject(
                "blocked resolved configuration: malformed build argument value",
                category=ViolationCategory.INPUT,
            )

    target = build.get("target")
    if target is not None and (
        not isinstance(target, str) or not NAME_PATTERN.fullmatch(target)
    ):
        reject(
            "blocked resolved configuration: invalid build target",
            category=ViolationCategory.INPUT,
        )

    if "labels" in build:
        build["labels"] = validate_labels(build["labels"])

    build["context"] = str(context)
    build["dockerfile"] = str(dockerfile)
    return build
