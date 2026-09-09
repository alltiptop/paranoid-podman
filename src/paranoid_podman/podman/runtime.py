"""Run/create policy and hardened provider arguments."""

import re
from pathlib import Path

from paranoid_podman.common.environment import SENSITIVE_ENV_KEYS
from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.common.hostnames import DEVPOD_ID_LABEL, workspace_hostname
from paranoid_podman.common.namespaces import is_keep_id
from paranoid_podman.common.provenance import (
    GUARD_INSTALLATION_ID,
    GUARD_INSTALLATION_LABEL,
    GUARD_POLICY_LABEL,
    GUARD_POLICY_VERSION,
)
from paranoid_podman.podman.arguments import (
    FALSE_ONLY_OPTIONS,
    FLAG_OPTIONS,
    VALUE_OPTIONS,
    append_checked_option,
    attached_short_value,
    has_arg,
    is_short_flag_bundle,
    printable_option,
    runtime_options,
)
from paranoid_podman.podman.errors import reject
from paranoid_podman.podman.models import CheckedOption, MountPolicy
from paranoid_podman.podman.mounts import (
    normalize_container_path,
    normalize_mount,
    normalize_volume,
    protected_mount_arguments,
    validate_mount_layout,
)
from paranoid_podman.podman.paths import project_directory
from paranoid_podman.podman.settings import FORCE_CAP_DROP, FORCE_KEEP_ID, REAL_PODMAN
from paranoid_podman.podman.validation import (
    normalize_environment,
    normalize_environment_file,
    validate_exposed_port,
    validate_image_reference,
    validate_label,
    validate_published_port,
    validate_ulimit,
)

SAFE_SECURITY_OPTIONS = {"no-new-privileges", "no-new-privileges=true"}
SAFE_NETWORKS = {"bridge", "none", "pasta", "slirp4netns"}
PRIVATE_NAMESPACE_OPTIONS = {"--cgroupns", "--ipc", "--pid", "--uts"}


def check_runtime_arg(flag: str, value: str | None, project_dir: Path) -> CheckedOption:
    if value is None or value == "":
        reject(f"missing value for {flag}", category=ViolationCategory.INPUT)

    if flag in {"-e", "--env"}:
        return normalize_environment(value)
    if flag == "--env-file":
        return normalize_environment_file(value, project_dir)
    if flag in {"-l", "--label"}:
        validate_label(value)
        return CheckedOption(value)
    if flag in {"-v", "--volume"}:
        return normalize_volume(value, project_dir)
    if flag == "--mount":
        return normalize_mount(value, project_dir)
    if flag == "--tmpfs":
        target = normalize_container_path(value)
        return CheckedOption(target, mount=MountPolicy(target))
    if flag in {"-p", "--publish"}:
        validate_published_port(value)
    elif flag == "--add-host" and value not in {
        "host.docker.internal:host-gateway",
        "host.containers.internal:host-gateway",
    }:
        reject(
            "blocked unreviewed host mapping", category=ViolationCategory.HOST_MAPPING
        )
    elif flag == "--expose":
        validate_exposed_port(value)
    elif flag in {"--network", "--net"} and value not in SAFE_NETWORKS:
        reject(
            "blocked host, joined, custom, or configured network",
            category=ViolationCategory.NETWORK,
        )
    elif flag in PRIVATE_NAMESPACE_OPTIONS and value != "private":
        reject(
            "blocked non-private container namespace",
            category=ViolationCategory.PRIVILEGE,
        )
    elif flag == "--userns" and not is_keep_id(value):
        reject(
            "blocked unreviewed user namespace", category=ViolationCategory.PRIVILEGE
        )
    elif flag == "--cap-drop" and value.upper() != "ALL":
        reject(
            "blocked partial capability drop; use --cap-drop=ALL",
            category=ViolationCategory.PRIVILEGE,
        )
    elif flag == "--security-opt" and value not in SAFE_SECURITY_OPTIONS:
        reject(
            "blocked unreviewed security option", category=ViolationCategory.PRIVILEGE
        )
    elif flag == "--pids-limit":
        if not value.isdigit() or not 1 <= int(value) <= 32_768:
            reject(
                "blocked PID limit outside the allowed range of 1 to 32768",
                category=ViolationCategory.INPUT,
            )
    elif flag == "--pull" and value != "never":
        reject(
            "blocked image pull policy; use --pull=never",
            category=ViolationCategory.PULL,
        )
    elif flag == "--restart" and value not in {"no", "none"}:
        reject("blocked persistent restart policy", category=ViolationCategory.POLICY)
    elif flag == "--unsetenv":
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            reject(
                "blocked invalid environment variable name",
                category=ViolationCategory.ENVIRONMENT,
            )
    elif flag == "--attach" or flag == "-a":
        if value not in {"stdin", "stdout", "stderr"}:
            reject("blocked invalid attach stream", category=ViolationCategory.INPUT)
    elif flag in {"--user", "-u"}:
        user, separator, group = value.partition(":")
        identity = r"(?:[0-9]+|[A-Za-z_][A-Za-z0-9_.-]*)"
        if not re.fullmatch(identity, user) or (
            separator and not re.fullmatch(identity, group)
        ):
            reject(
                "blocked invalid container user", category=ViolationCategory.PRIVILEGE
            )
        user_is_root = user == "root" or (user.isdigit() and int(user) == 0)
        group_is_root = group == "root" or (group.isdigit() and int(group) == 0)
        if user_is_root or group_is_root:
            reject(
                "blocked explicit root container user",
                category=ViolationCategory.PRIVILEGE,
            )
    elif flag == "--workdir" or flag == "-w":
        if not value.startswith("/"):
            reject(
                "blocked non-absolute container working directory",
                category=ViolationCategory.POLICY,
            )
    elif flag == "--network-alias":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", value):
            reject("blocked invalid network alias", category=ViolationCategory.NETWORK)
    elif flag == "--ulimit":
        validate_ulimit(value)

    return CheckedOption(value)


def safe_args_for(runtime: list[str]) -> list[str]:
    safe = [
        f"--label={GUARD_POLICY_LABEL}={GUARD_POLICY_VERSION}",
        f"--label={GUARD_INSTALLATION_LABEL}={GUARD_INSTALLATION_ID}",
    ]

    if not has_arg(runtime, {"--hostname", "-h", "--no-hostname"}):
        labels: dict[str, str] = {}
        for option, label in runtime_options(runtime):
            if option in {"--label", "-l"} and label is not None:
                key, _, value = label.partition("=")
                labels[key] = value
        hostname = workspace_hostname(labels.get(DEVPOD_ID_LABEL))
        if hostname:
            safe.append(f"--hostname={hostname}")

    # Rootless Podman already has a user namespace. keep-id is convenient, but can break DevPod
    # containers that specify their own remoteUser/home. Add it only when it is safe to do so.
    has_userns = has_arg(
        runtime,
        {"--userns"},
    )
    has_user = has_arg(runtime, {"--user", "-u"})
    if FORCE_KEEP_ID == "1" and not has_userns:
        safe.append("--userns=keep-id")
    elif FORCE_KEEP_ID == "auto" and not has_userns and not has_user:
        safe.append("--userns=keep-id")

    # Important DevPod/devcontainer compatibility note:
    # If the container is explicitly started as a non-root user (--user node, --user vscode, etc.),
    # forcing --cap-drop=ALL can break image bootstrap, UID/GID adjustment, server install,
    # or later podman exec --user operations. For non-root runtime users, cap-drop is mostly
    # redundant anyway because the process has no effective root capabilities.
    if not has_arg(runtime, {"--cap-drop"}):
        if FORCE_CAP_DROP == "1":
            safe.append("--cap-drop=ALL")
        elif FORCE_CAP_DROP == "auto" and not has_user:
            safe.append("--cap-drop=ALL")

    if not has_arg(runtime, {"--security-opt"}):
        safe.append("--security-opt=no-new-privileges")

    if not has_arg(runtime, {"--pids-limit"}):
        safe.append("--pids-limit=512")
    if not has_arg(runtime, {"--http-proxy"}):
        safe.append("--http-proxy=false")
    if not has_arg(runtime, {"--pull"}):
        safe.append("--pull=never")
    if not has_arg(runtime, {"--restart"}):
        safe.append("--restart=no")

    for key in sorted(SENSITIVE_ENV_KEYS):
        safe.append(f"--unsetenv={key}")
    return safe


def build_run_create_command(argv: list[str], subcmd_index: int) -> list[str]:
    before = argv[: subcmd_index + 1]
    after = argv[subcmd_index + 1 :]
    runtime: list[str] = []
    image_cmd: list[str] = []
    mounts: list[MountPolicy] = []
    separator: list[str] = []
    current_project = project_directory()

    i = 0
    while i < len(after):
        a = after[i]

        if a == "--":
            separator = [a]
            image_cmd = after[i + 1 :]
            break

        if not a.startswith("-"):
            image_cmd = after[i:]
            break

        if a in VALUE_OPTIONS:
            if i + 1 >= len(after):
                reject(f"missing value for {a}", category=ViolationCategory.INPUT)
            checked = check_runtime_arg(a, after[i + 1], current_project)
            append_checked_option(runtime, mounts, a, checked, "separate")
            i += 2
            continue

        if a.startswith("--") and "=" in a:
            option, val = a.split("=", 1)
            if option in VALUE_OPTIONS:
                checked = check_runtime_arg(option, val, current_project)
                append_checked_option(runtime, mounts, option, checked, "long")
                i += 1
                continue
            if option in FALSE_ONLY_OPTIONS:
                if val not in {"0", "false"}:
                    reject(
                        "blocked unsafe boolean runtime option",
                        category=ViolationCategory.POLICY,
                    )
                runtime.append(f"{option}=false")
                i += 1
                continue
            if option == "--publish-all" and val in {"0", "1", "false", "true"}:
                runtime.append(a)
                i += 1
                continue
            if option in FLAG_OPTIONS:
                reject(
                    "blocked value attached to a flag-only runtime option",
                    category=ViolationCategory.POLICY,
                )
            reject(
                f"blocked unknown runtime option {printable_option(a)}; "
                "add it to the tested allowlist before use",
                category=ViolationCategory.UNSUPPORTED,
            )

        if a in FLAG_OPTIONS:
            runtime.append(a)
            i += 1
            continue

        if a in FALSE_ONLY_OPTIONS:
            reject(
                "blocked unsafe boolean runtime option",
                category=ViolationCategory.POLICY,
            )

        short_option, short_value = attached_short_value(a)
        if short_option:
            if not short_value or short_value.startswith("="):
                reject(
                    "blocked malformed short runtime option",
                    category=ViolationCategory.INPUT,
                )
            checked = check_runtime_arg(short_option, short_value, current_project)
            append_checked_option(runtime, mounts, short_option, checked, "short")
            i += 1
            continue

        if is_short_flag_bundle(a):
            runtime.append(a)
            i += 1
            continue

        reject(
            f"blocked unknown runtime option {printable_option(a)}; "
            "add it to the tested allowlist before use",
            category=ViolationCategory.UNSUPPORTED,
        )

    validate_mount_layout(mounts)
    if not image_cmd:
        reject("missing container image", category=ViolationCategory.INPUT)
    validate_image_reference(image_cmd[0])
    protected_mounts = protected_mount_arguments(mounts)
    return (
        [REAL_PODMAN]
        + before
        + safe_args_for(runtime)
        + runtime
        + protected_mounts
        + separator
        + image_cmd
    )
