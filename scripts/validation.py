"""Local source checks and a directory-only secrets scan; never installs tools."""

import argparse
import os
import shutil
import subprocess  # nosec B404
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_LAUNCHERS = {"podman", "compose-guard", "devpod", "paranoid-podman"}
BASH_LAUNCHERS = {"docker", "docker-compose", "podman-compose"}
SOURCE_DIRECTORIES = ("bin", "src", "tools", "scripts", "tests")
PUBLIC_DIRECTORIES = (*SOURCE_DIRECTORIES, "docs", "i18n", ".github")
PUBLIC_ROOT_FILES = {
    "CHANGELOG.md",
    "CLI_IMPLEMENTATION_PLAN.md",
    "CONTRIBUTING.md",
    "KNOWN_ISSUES.md",
    "LICENSE",
    "README.md",
    "REVIEW_TODO.md",
    "SECURITY.md",
    "TODO.md",
    "VERSION",
    "install.sh",
    "uninstall.sh",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-build.txt",
    "requirements-installer.txt",
}
PUBLIC_SUFFIXES = {
    ".py",
    ".sh",
    ".md",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
    ".patch",
    ".zip",
}


def tree_files(directory: Path) -> list[Path]:
    """Walk regular source files, excluding hidden/private trees and symlinks."""
    if directory.is_symlink() or not directory.is_dir():
        return []
    files = []
    for current, directories, names in os.walk(directory, followlinks=False):
        parent = Path(current)
        directories[:] = sorted(
            name
            for name in directories
            if not name.startswith(".")
            and name not in {"__pycache__", "tmp", "node_modules"}
            and not (parent / name).is_symlink()
        )
        for name in sorted(names):
            path = parent / name
            if not name.startswith(".") and not path.is_symlink() and path.is_file():
                files.append(path)
    return files


def python_files(root: Path) -> list[Path]:
    return sorted(
        path
        for name in SOURCE_DIRECTORIES
        for path in tree_files(root / name)
        if path.suffix == ".py"
        or (path.parent == root / "bin" and path.name in PYTHON_LAUNCHERS)
    )


def bash_files(root: Path) -> list[Path]:
    return [
        root / name
        for name in ("install.sh", "uninstall.sh")
        if (root / name).is_file()
    ] + sorted(
        path
        for name in SOURCE_DIRECTORIES
        for path in tree_files(root / name)
        if path.suffix == ".sh"
        or (path.parent == root / "bin" and path.name in BASH_LAUNCHERS)
    )


def publishable_files(root: Path) -> list[Path]:
    files = [
        root / name
        for name in PUBLIC_ROOT_FILES
        if not (root / name).is_symlink() and (root / name).is_file()
    ]
    for name in PUBLIC_DIRECTORIES:
        files.extend(
            path
            for path in tree_files(root / name)
            if path.suffix in PUBLIC_SUFFIXES
            or (
                path.parent == root / "bin"
                and path.name in PYTHON_LAUNCHERS | BASH_LAUNCHERS
            )
        )
    return sorted(files)


def run(arguments: list[str], **kwargs) -> None:
    # Only check tools with explicit argv; no shell or runtime provider commands.
    subprocess.run(arguments, check=True, timeout=300, **kwargs)  # nosec B603


def generated_launchers(destination: Path) -> list[Path]:
    """Render lifecycle templates using synthetic inputs, without installation."""
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from paranoid_podman.lifecycle import launchers, settings

    files = []
    for index, prefix in enumerate(("/synthetic", "/synthetic spaces/quote'$`")):
        providers = {
            name: f"{prefix}/{name}" for name in ("python", "podman", "compose")
        }
        for devpod in (False, True):
            selected = (
                {**providers, "devpod": f"{prefix}/devpod"} if devpod else providers
            )
            path = destination / f"release-{index}-{devpod}.sh"
            path.write_bytes(launchers.release_launcher(selected, "a" * 64))
            files.append(path)
        for entry in (*settings.ENTRY_POINTS, *settings.DEVPOD_ENTRY_POINTS):
            path = destination / f"command-{index}-{entry}.sh"
            path.write_bytes(launchers.command_launcher(Path(prefix), entry))
            files.append(path)
    return files


def check_bash(paths: list[Path], *, shellcheck: bool = False) -> None:
    for path in paths:
        run(["bash", "-n", str(path)])
    if shellcheck:
        run(["shellcheck", "--shell=bash", *map(str, paths)])


def syntax(root: Path, *, shellcheck: bool = False) -> None:
    sources = python_files(root)
    for path in sources:
        compile(path.read_bytes(), str(path), "exec")
    # TemporaryDirectory creates a private random directory; ignore caller TMPDIR.
    with tempfile.TemporaryDirectory(
        prefix="pp-launchers-",
        dir="/tmp",  # nosec B108
    ) as temporary:
        generated = generated_launchers(Path(temporary))
        shell = bash_files(root)
        check_bash([*shell, *generated], shellcheck=shellcheck)
    print(
        f"Syntax: {len(sources)} Python files, {len(shell)} Bash files, "
        f"{len(generated)} generated launchers",
        flush=True,
    )


def lint(root: Path) -> None:
    syntax(root, shellcheck=True)
    run([sys.executable, "-B", "scripts/runtime_requirements.py", "--check"])
    sources = python_files(root)
    run(["ruff", "check", *map(str, sources)])
    run(["ruff", "format", "--check", *map(str, sources)])
    run(["mypy", "--no-incremental", "--cache-dir=/dev/null"])
    run(
        [
            "bandit",
            "--configfile",
            str(root / "pyproject.toml"),
            "--severity-level",
            "medium",
            "--confidence-level",
            "medium",
            *(str(path) for path in sources if not path.is_relative_to(root / "tests")),
        ]
    )
    run(
        [
            "zizmor",
            "--offline",
            "--persona",
            "pedantic",
            "--strict-collection",
            ".github",
        ]
    )


def secrets(root: Path) -> None:
    # TemporaryDirectory creates a private random directory; ignore caller TMPDIR.
    with tempfile.TemporaryDirectory(
        prefix="pp-secrets-",
        dir="/tmp",  # nosec B108
    ) as temporary:
        staging = Path(temporary)
        source = staging / "source"
        source.mkdir()
        paths = publishable_files(root)
        for path in paths:
            target = source / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target, follow_symlinks=False)
        config = staging / "gitleaks.toml"
        config.write_text("[extend]\nuseDefault = true\n", encoding="utf-8")
        ignore = staging / "empty.ignore"
        ignore.touch()
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GITLEAKS_")
        }
        print(f"Secrets: scanning {len(paths)} publishable files", flush=True)
        run(
            [
                "gitleaks",
                "dir",
                "--redact=100",
                "--no-banner",
                "--no-color",
                "--ignore-gitleaks-allow",
                "--max-archive-depth=1",
                "--config",
                str(config),
                "--gitleaks-ignore-path",
                str(ignore),
                str(source),
            ],
            cwd=staging,
            env=environment,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("syntax", "lint", "format", "secrets"))
    mode = parser.parse_args().mode
    os.chdir(PROJECT_ROOT)
    try:
        if mode == "format":
            run(["ruff", "format", *map(str, python_files(PROJECT_ROOT))])
        else:
            {"syntax": syntax, "lint": lint, "secrets": secrets}[mode](PROJECT_ROOT)
    except FileNotFoundError as error:
        print(
            f"check: missing file/tool {error.filename}; see CONTRIBUTING.md",
            file=sys.stderr,
        )
        return 1
    except subprocess.CalledProcessError as error:
        return error.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
