"""Audit or update reviewed build-context ignore entries."""

from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path
from typing import TextIO

from paranoid_podman.common.build_inputs import inspect_build_context
from paranoid_podman.common.errors import BuildInputViolation
from paranoid_podman.devpod import errors as devpod_errors
from paranoid_podman.devpod import files as devpod_files
from paranoid_podman.devpod import reporting as devpod_reporting
from paranoid_podman.devpod import ssh_config as devpod_ssh_config
from paranoid_podman.devpod import terminal as devpod_terminal


def build_context_findings(context: Path) -> tuple[Path | None, list[str]]:
    try:
        return inspect_build_context(context)
    except BuildInputViolation as error:
        raise devpod_errors.DevPodGuardError(str(error)) from error


def show_build_context_audit(context: Path) -> int:
    ignore_file, findings = build_context_findings(context)
    print(f"build context: {context}")
    print(f"active ignore file: {ignore_file or 'none'}")
    if not findings:
        print("sensitive root-level paths requiring exclusion: none")
        return 0
    print("sensitive root-level paths requiring exclusion:")
    for finding in findings:
        print(f"  {finding}")
    return 1


def selected_findings(findings: list[str], selection: str) -> list[str]:
    if not selection.strip() or selection.strip().lower() == "all":
        return findings
    indexes: set[int] = set()
    for token in re.split(r"[\s,]+", selection.strip()):
        if not token.isdigit() or not 1 <= int(token) <= len(findings):
            devpod_errors.fail(
                "selection must contain listed numbers, commas, spaces, or `all`"
            )
        indexes.add(int(token) - 1)
    return [finding for index, finding in enumerate(findings) if index in indexes]


def append_build_ignores(ignore_file: Path, findings: list[str]) -> None:
    if ignore_file.exists():
        try:
            metadata = ignore_file.lstat()
        except OSError as error:
            raise devpod_errors.DevPodGuardError(
                "cannot inspect the build ignore file"
            ) from error
        if (
            ignore_file.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
        ):
            devpod_errors.fail(
                "build ignore file must be a user-owned regular non-symlink file"
            )
        mode = stat.S_IMODE(metadata.st_mode)
        try:
            original = ignore_file.read_bytes().decode("utf-8")
        except (OSError, UnicodeError) as error:
            raise devpod_errors.DevPodGuardError(
                "cannot read the build ignore file"
            ) from error
    else:
        original = ""
        mode = 0o644
    newline = devpod_ssh_config.ssh_config_newline(original)
    separator = "" if not original or original.endswith(newline) else newline
    heading_text = "# Added by paranoid-podman: excluded from image build context"
    heading = "" if heading_text in original.splitlines() else heading_text + newline
    addition = heading + "".join(f"{finding}{newline}" for finding in findings)
    devpod_files.atomic_write_text(ignore_file, original + separator + addition, mode)


def protect_build_context(
    context: Path, *, protect_all: bool, input_stream: TextIO = sys.stdin
) -> int:
    ignore_file, findings = build_context_findings(context)
    if not findings:
        devpod_reporting.info(
            "build context already excludes obvious credential inputs"
        )
        return 0
    target = ignore_file or context / ".containerignore"
    chosen = findings
    if not protect_all:
        if not input_stream.isatty():
            devpod_errors.fail(
                "interactive selection requires a terminal; rerun with --all "
                "to exclude every listed path"
            )
        print("Select paths to exclude from the image build context:")
        for index, finding in enumerate(findings, start=1):
            print(f"  {index}. {finding}")
        print("Selection [all]: ", end="", flush=True)
        chosen = selected_findings(
            findings, devpod_terminal.read_interactive_line(input_stream)
        )
    if not chosen:
        devpod_errors.fail("no build-context path was selected; no file was changed")
    append_build_ignores(target, chosen)
    _active, remaining = build_context_findings(context)
    unresolved = [finding for finding in chosen if finding in remaining]
    if unresolved:
        devpod_errors.fail(
            "the selected paths remain active after updating the ignore file"
        )
    devpod_reporting.info(f"added {len(chosen)} reviewed exclusion(s) to {target}")
    if remaining:
        devpod_reporting.warning(
            "other sensitive build-context paths remain unexcluded: "
            + ", ".join(remaining)
        )
        return 1
    return 0
