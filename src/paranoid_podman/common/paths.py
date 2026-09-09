"""Protected names and host scope checks using filesystem and account metadata."""

import errno
import os
import pwd
import stat
from pathlib import Path

from paranoid_podman.common.settings import PROTECT_GIT

PROTECTED_NAMES = {
    ".containerignore",
    ".devcontainer",
    ".devcontainer.json",
    ".dockerignore",
    ".env",
    ".git-credentials",
    ".gitmodules",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "devcontainer.json",
}
BROAD_HOST_ROOTS = {
    Path("/"),
    Path("/home"),
    Path("/media"),
    Path("/mnt"),
    Path("/srv"),
    # This is a denied mount source, not a temporary-file allocation.
    Path("/tmp"),  # noqa: S108  # nosec B108
}
SYSTEM_HOST_TREES = tuple(
    Path(path)
    for path in (
        "/boot",
        "/dev",
        "/etc",
        "/opt",
        "/proc",
        "/root",
        "/run",
        "/sys",
        "/usr",
        "/var",
    )
)
SENSITIVE_USER_PATHS = {
    (".aws",),
    (".docker",),
    (".gnupg",),
    (".kube",),
    (".ssh",),
    (".config", "containers"),
    (".local", "share", "containers"),
}


def is_sensitive_dotenv_name(name: str) -> bool:
    """Return whether a filename is likely to contain live dotenv values."""

    lowered = name.lower()
    if lowered.endswith((".dist", ".example", ".sample", ".template")):
        return False
    return (
        lowered == ".env"
        or lowered.startswith(".env.")
        or lowered.endswith(".env")
        or ".env." in lowered
    )


def is_protected_name(name: str) -> bool:
    return (
        (name == ".git" and PROTECT_GIT)
        or name in PROTECTED_NAMES
        or is_sensitive_dotenv_name(name)
    )


def is_unreadable_foreign_directory(error: OSError) -> bool:
    """Recognize inaccessible directories owned by another UID, such as DB data."""

    if error.errno not in {errno.EACCES, errno.EPERM} or not error.filename:
        return False
    try:
        metadata = Path(error.filename).lstat()
    except OSError:
        return False
    return stat.S_ISDIR(metadata.st_mode) and metadata.st_uid != os.geteuid()


def host_source_rejection(source: Path, project_dir: Path) -> str | None:
    """Return why a bind/build source is too broad or host-sensitive."""

    def within(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
        except ValueError:
            return False
        return True

    if source in BROAD_HOST_ROOTS or any(
        within(source, root) for root in SYSTEM_HOST_TREES
    ):
        return "broad or security-sensitive host source"

    try:
        user_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(strict=True)
    except (KeyError, OSError, RuntimeError):
        return "current user home cannot be resolved"
    if source == user_home:
        return "full user-home source"
    if source != project_dir and within(project_dir, source):
        return "source broader than the current project"

    try:
        relative_to_home = source.relative_to(user_home)
    except ValueError:
        return None
    parts = relative_to_home.parts
    if any(parts[: len(prefix)] == prefix for prefix in SENSITIVE_USER_PATHS):
        return "security-sensitive user source"
    return None
