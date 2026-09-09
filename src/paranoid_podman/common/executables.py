"""Provider-path checks; reads filesystem metadata and never starts a process."""

import os
from pathlib import Path


def is_external_executable(value: str, package_directory: Path) -> bool:
    """Return whether a configured provider is executable and outside this package."""

    candidate = Path(value)
    if not candidate.is_absolute():
        return False
    try:
        resolved = candidate.resolve(strict=True)
        package_directory = package_directory.resolve(strict=True)
        resolved.relative_to(package_directory)
    except ValueError:
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            return False
        try:
            return not any(
                child.is_file() and os.path.samefile(resolved, child)
                for child in package_directory.iterdir()
            )
        except OSError:
            return False
    except (OSError, RuntimeError):
        return False
    return False
