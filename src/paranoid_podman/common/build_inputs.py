"""Build contexts, ignore files, and image references; reads local build inputs."""

import fnmatch
import os
import posixpath
import re
import stat
from pathlib import Path

from paranoid_podman.common.dockerfile import lint_dockerfile
from paranoid_podman.common.errors import BuildInputViolation, ViolationCategory
from paranoid_podman.common.paths import is_sensitive_dotenv_name
from paranoid_podman.common.settings import MAX_BUILD_CONTROL_FILE_BYTES

BUILD_CONTEXT_SENSITIVE_NAMES = {
    ".aws",
    ".docker",
    ".git-credentials",
    ".gnupg",
    ".kube",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".ssh",
}
MAX_IGNORED_TREE_ENTRIES = 10_000


def _read_build_ignore(
    context: Path, dockerfile: Path | None = None
) -> tuple[Path | None, list[str]]:
    candidates = [context / ".containerignore", context / ".dockerignore"]
    if dockerfile is not None:
        # Buildah 1.45 selects the Dockerfile-specific rules before root rules.
        candidates[:0] = [
            Path(f"{dockerfile}.dockerignore"),
            Path(f"{dockerfile}.containerignore"),
        ]
    ignore_file = next(
        (path for path in candidates if path.exists() or path.is_symlink()), None
    )
    if ignore_file is None:
        return None, []
    try:
        if ignore_file.is_symlink() or not ignore_file.is_file():
            raise BuildInputViolation(
                "blocked invalid build ignore file", category=ViolationCategory.INPUT
            )
        data = ignore_file.read_bytes()
    except OSError as error:
        raise BuildInputViolation(
            "blocked unreadable build ignore file", category=ViolationCategory.POLICY
        ) from error
    if len(data) > MAX_BUILD_CONTROL_FILE_BYTES:
        raise BuildInputViolation(
            "blocked build ignore file larger than 1 MiB",
            category=ViolationCategory.POLICY,
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BuildInputViolation(
            "blocked non-UTF-8 build ignore file", category=ViolationCategory.POLICY
        ) from error
    if "\x00" in text:
        raise BuildInputViolation(
            "blocked build ignore file containing a NUL byte",
            category=ViolationCategory.POLICY,
        )
    return ignore_file, text.split("\n")


def _matches_ignore_component(name: str, pattern: str) -> bool | None:
    """Match reviewed Go globs; None means the pattern cannot prove exclusion."""
    if "**" in pattern and pattern != "**":
        return None
    translated = ""
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "[":
            end = pattern.find("]", index + 1)
            if end == -1:
                return None
            body = pattern[index + 1 : end]
            characters = body.removeprefix("^")
            if (
                not characters
                or characters.startswith("-")
                or characters.endswith("-")
                or any(not c.isalnum() and c not in " ._-" for c in characters)
            ):
                return None
            try:
                re.compile(f"[{body}]")
            except re.error:
                return None
            # Go uses ^ for class negation; Python fnmatch uses !.
            translated += "[" + ("!" + characters if body[0] == "^" else body) + "]"
            index = end + 1
        elif char.isalnum() or char in " ._-*?":
            translated += char
            index += 1
        else:
            return None
    return fnmatch.fnmatchcase(name, translated)


def _ignored_by_patterns(relative_name: str, patterns: list[str]) -> bool:
    """Require a root exclusion after every potentially relevant exception."""
    ignored = False
    for raw_pattern in patterns:
        # Match imagebuilder.ParseIgnoreReader, then fileutils.NewPatternMatcher.
        if not raw_pattern or raw_pattern.startswith("#"):
            continue
        pattern = raw_pattern.strip("/").strip()
        if not pattern:
            continue
        pattern = posixpath.normpath(pattern)
        negated = pattern.startswith("!")
        if negated:
            if pattern == "!":
                ignored = False
                continue
            pattern = posixpath.normpath(pattern[1:]).removeprefix("/")
        components = pattern.split("/")
        matches = [
            _matches_ignore_component(relative_name, part) for part in components
        ]
        if any(match is None for match in matches):
            # Unreviewed syntax cannot establish exclusion; an exception may
            # reopen any path. A later plain root exclusion can restore safety.
            if negated:
                ignored = False
        elif negated:
            if components[0] == "**" or matches[0]:
                ignored = False
        elif all(part == "**" for part in components[:-1]) and matches[-1]:
            ignored = True
    return ignored


def _check_ignored_tree(path: Path) -> None:
    """Check names only: Buildah's descendant regex does not span newlines."""
    pending = [path]
    count = 0
    try:
        if path.is_symlink():
            raise OSError("cannot inspect a symlinked sensitive tree")
        while pending:
            current = pending.pop()
            if "\n" in current.name:
                raise OSError("newline cannot be safely excluded with exceptions")
            if not stat.S_ISDIR(current.lstat().st_mode):
                continue
            with os.scandir(current) as entries:
                for entry in entries:
                    count += 1
                    if count > MAX_IGNORED_TREE_ENTRIES:
                        raise OSError("sensitive tree exceeds inspection limit")
                    if "\n" in entry.name:
                        raise OSError(
                            "newline cannot be safely excluded with exceptions"
                        )
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
    except OSError as error:
        raise BuildInputViolation(
            "blocked sensitive build-context tree that cannot be safely ignored; "
            "remove descendant exceptions or keep credential inputs outside the context",
            category=ViolationCategory.BUILD_CONTEXT,
        ) from error


def inspect_build_context(
    context: Path, *, dockerfile: Path | None = None
) -> tuple[Path | None, list[str]]:
    """Return the active ignore file and obvious unignored credential inputs."""

    ignore_file, patterns = _read_build_ignore(context, dockerfile)
    sensitive: list[str] = []
    try:
        children = sorted(context.iterdir(), key=lambda child: child.name)
    except OSError as error:
        raise BuildInputViolation(
            "blocked unreadable build context", category=ViolationCategory.POLICY
        ) from error
    for child in children:
        if child.name in BUILD_CONTEXT_SENSITIVE_NAMES or is_sensitive_dotenv_name(
            child.name
        ):
            sensitive.append(child.name)

    unignored = [
        relative_name
        for relative_name in sensitive
        if not _ignored_by_patterns(relative_name, patterns)
    ]
    has_exceptions = any(
        posixpath.normpath(pattern.strip("/").strip()).startswith("!")
        for pattern in patterns
        if pattern.strip() and not pattern.startswith("#")
    )
    if has_exceptions:
        for name in sensitive:
            if name not in unignored:
                _check_ignored_tree(context / name)
    return ignore_file, unignored


def validate_build_context(context: Path, *, dockerfile: Path | None = None) -> None:
    """Require obvious root-level credential inputs to be excluded."""

    _ignore_file, unignored = inspect_build_context(context, dockerfile=dockerfile)
    if unignored:
        displayed = [
            name if re.fullmatch(r"[A-Za-z0-9._-]{1,200}", name) else "<sensitive-path>"
            for name in unignored
        ]
        noun = "path" if len(displayed) == 1 else "paths"
        raise BuildInputViolation(
            f"blocked sensitive build-context {noun} not excluded by "
            f".containerignore or .dockerignore: {', '.join(displayed)}",
            category=ViolationCategory.BUILD_CONTEXT,
        )


def validate_build_inputs(context: Path, dockerfile: Path) -> None:
    validate_build_context(context, dockerfile=dockerfile)
    lint_dockerfile(dockerfile)


def is_safe_image_reference(value: str) -> bool:
    """Accept normal registry/image references while rejecting file transports."""

    if re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{64}", value):
        return True
    component = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
    registry = rf"{component}(?::[0-9]+)?/"
    tag = r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?"
    digest = r"(?:@sha256:[0-9a-fA-F]{64})?"
    return len(value) <= 512 and bool(
        re.fullmatch(
            rf"(?:{registry})?{component}(?:/{component})*{tag}{digest}",
            value,
        )
    )
