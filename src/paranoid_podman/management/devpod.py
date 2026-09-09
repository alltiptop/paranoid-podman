"""Audit and configure workspace SSH access through narrow DevPod APIs."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from paranoid_podman.devpod import arguments as devpod_arguments
from paranoid_podman.devpod import errors as devpod_errors
from paranoid_podman.devpod import keys as devpod_keys
from paranoid_podman.devpod import models as devpod_models
from paranoid_podman.devpod import modes as devpod_modes
from paranoid_podman.devpod import paths as devpod_paths
from paranoid_podman.devpod import reporting as devpod_reporting
from paranoid_podman.devpod import ssh_config as devpod_ssh_config
from paranoid_podman.devpod import terminal as devpod_terminal


def management_workspace(arguments: argparse.Namespace) -> devpod_models.Workspace:
    context = arguments.context or "default"
    ssh_config = (
        devpod_paths.normalize_path(arguments.ssh_config)
        if arguments.ssh_config
        else devpod_paths.default_ssh_config()
    )
    return devpod_models.Workspace(
        context,
        devpod_arguments.canonical_workspace_name(arguments.workspace),
        ssh_config,
        arguments.devpod_home or os.environ.get("DEVPOD_HOME"),
    )


def show_audit(workspace: devpod_models.Workspace) -> int:
    mode = devpod_modes.infer_mode(workspace)
    identity = devpod_keys.managed_identity(workspace)
    if mode == devpod_models.SSHMode.PROJECT_KEY and identity is not None:
        print(f"workspace: {workspace.name}")
        print("mode: project-key")
        print(f"fingerprint: {devpod_keys.key_fingerprint(identity)}")
        public_key = devpod_keys.managed_public_key(workspace)
        print(f"public key: {public_key or 'missing'}")
    elif mode == devpod_models.SSHMode.IDE_ONLY:
        print(f"workspace: {workspace.name}")
        print("mode: ide-only")
        print("credentials: none")
    else:
        print(f"workspace: {workspace.name}")
        print("mode: unconfigured")
        devpod_reporting.warning(
            "the ambient agent could be forwarded until this workspace is configured"
        )
        return 1
    return 0


def configure_workspace(real_devpod: Path, workspace: devpod_models.Workspace) -> int:
    stream = devpod_terminal.interactive_stream()
    if stream is None:
        devpod_errors.fail("DevPod configuration requires an interactive terminal")
    try:
        mode = devpod_modes.prompt_choice(workspace, stream)
    finally:
        if stream is not sys.stdin:
            stream.close()
    paths = devpod_modes.prepare_mode(real_devpod, workspace, mode)
    if mode == devpod_models.SSHMode.UNSAFE_AMBIENT:
        devpod_reporting.info(
            "unsafe one-shot mode was not saved; no workspace setting changed"
        )
        return 0
    patched = devpod_ssh_config.patch_ssh_block(
        workspace,
        mode,
        devpod_paths.wrapper_path(),
        paths.socket if paths else None,
    )
    if patched:
        devpod_reporting.info(
            f"updated the DevPod-managed SSH block for {workspace.host}"
        )
    elif mode == devpod_models.SSHMode.IDE_ONLY:
        devpod_errors.fail(
            "the safe context baseline was applied, but DevPod has not created "
            "an SSH block for this workspace, so its IDE-only choice was not "
            "saved. Start the workspace with an interactive `devpod up` and "
            "choose IDE only there"
        )
    else:
        devpod_reporting.info(
            "the project key was saved; the next guarded up will protect "
            "the SSH block after DevPod creates it"
        )
    if mode == devpod_models.SSHMode.PROJECT_KEY:
        identity = devpod_keys.managed_identity(workspace)
        if identity is not None:
            public_key = devpod_keys.managed_public_key(workspace)
            print(f"Public key: {public_key or 'missing'}")
            print(f"Fingerprint: {devpod_keys.key_fingerprint(identity)}")
            print("Register the public key as a repository deploy key:")
            print(
                "  GitHub: https://docs.github.com/en/authentication/"
                "connecting-to-github-with-ssh/managing-deploy-keys"
            )
            print("  GitLab: https://docs.gitlab.com/user/project/deploy_keys/")
            print(
                "  Gitea: https://docs.gitea.com/api/next/operations/repo-create-key/"
            )
    return 0
