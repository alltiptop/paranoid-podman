"""Validate and rewrite resolved services in the existing policy order."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from paranoid_podman.common.build_inputs import is_safe_image_reference
from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.common.hostnames import DEVPOD_ID_LABEL, workspace_hostname
from paranoid_podman.common.namespaces import is_keep_id
from paranoid_podman.common.provenance import (
    GUARD_INSTALLATION_ID,
    GUARD_INSTALLATION_LABEL,
    GUARD_POLICY_LABEL,
    GUARD_POLICY_VERSION,
)
from paranoid_podman.compose.build import validate_build
from paranoid_podman.compose.dotenv import validate_service_env_files
from paranoid_podman.compose.errors import PolicyViolation, reject
from paranoid_podman.compose.models import Invocation
from paranoid_podman.compose.mounts import validate_service_volumes
from paranoid_podman.compose.resources import (
    validate_dependencies,
    validate_environment,
    validate_extra_hosts,
    validate_labels,
    validate_logging,
    validate_named_resources,
    validate_ports,
    validate_resource_limits,
    validate_service_networks,
    validate_ulimits,
)
from paranoid_podman.compose.schema import _mapping, _string_list, _valid_name
from paranoid_podman.compose.settings import DEFAULT_PIDS, MAX_PIDS, MAX_SCALE

ALLOWED_TOP_LEVEL_KEYS = {"name", "networks", "services", "version", "volumes"}
ALLOWED_SERVICE_KEYS = {
    "build",
    "cap_add",
    "cap_drop",
    "command",
    "container_name",
    "cpus",
    "depends_on",
    "dns",
    "dns_opt",
    "dns_search",
    "domainname",
    "entrypoint",
    "environment",
    "env_file",
    "expose",
    "extra_hosts",
    "healthcheck",
    "hostname",
    "http_proxy",
    "image",
    "init",
    "labels",
    "logging",
    "mem_limit",
    "mem_reservation",
    "memswap_limit",
    "network_mode",
    "networks",
    "pids_limit",
    "platform",
    "ports",
    "privileged",
    "profiles",
    "pull_policy",
    "read_only",
    "restart",
    "scale",
    "security_opt",
    "shm_size",
    "stdin_open",
    "stop_grace_period",
    "stop_signal",
    "tmpfs",
    "tty",
    "ulimits",
    "user",
    "userns_mode",
    "volumes",
    "working_dir",
}
DENIED_SERVICE_KEYS = {
    "annotations": ("container annotations", ViolationCategory.POLICY),
    "cgroup_parent": ("custom cgroup placement", ViolationCategory.POLICY),
    "configs": ("config forwarding", ViolationCategory.POLICY),
    "credential_spec": ("credential specifications", ViolationCategory.POLICY),
    "deploy": ("unreviewed deploy settings", ViolationCategory.UNSUPPORTED),
    "develop": ("automatic host file synchronization", ViolationCategory.POLICY),
    "device_cgroup_rules": ("device access", ViolationCategory.PRIVILEGE),
    "devices": ("host device access", ViolationCategory.PRIVILEGE),
    "extends": ("unresolved service extension", ViolationCategory.POLICY),
    "external_links": ("external container links", ViolationCategory.POLICY),
    "gpus": ("host GPU access", ViolationCategory.POLICY),
    "group_add": ("supplementary host groups", ViolationCategory.POLICY),
    "ipc": ("custom IPC namespaces", ViolationCategory.PRIVILEGE),
    "isolation": ("custom isolation settings", ViolationCategory.POLICY),
    "links": ("container links", ViolationCategory.POLICY),
    "pid": ("custom PID namespaces", ViolationCategory.PRIVILEGE),
    "runtime": ("custom OCI runtimes", ViolationCategory.POLICY),
    "secrets": ("secret forwarding", ViolationCategory.POLICY),
    "storage_opt": ("custom storage settings", ViolationCategory.POLICY),
    "sysctls": ("custom kernel settings", ViolationCategory.POLICY),
    "use_api_socket": ("container engine socket access", ViolationCategory.PRIVILEGE),
    "uts": ("custom UTS namespaces", ViolationCategory.PRIVILEGE),
    "volumes_from": ("inherited container mounts", ViolationCategory.MOUNT),
}
SAFE_SECURITY_OPTIONS = {
    "no-new-privileges",
    "no-new-privileges=true",
    "no-new-privileges:true",
}


def validate_service(
    service: dict[str, Any],
    service_names: set[str],
    declared_volumes: set[str],
    declared_networks: set[str],
    project_dir: Path,
) -> None:
    for key in list(service):
        if key.startswith("x-"):
            service.pop(key)
    for key, (risk, category) in DENIED_SERVICE_KEYS.items():
        if key in service:
            reject(f"blocked resolved configuration: {risk}", category=category)
    if set(service) - ALLOWED_SERVICE_KEYS:
        fields = sorted(set(service) - ALLOWED_SERVICE_KEYS)
        field = (
            fields[0]
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", fields[0])
            else "<field>"
        )
        reject(
            f"unsupported Compose service setting: {field}",
            category=ViolationCategory.UNSUPPORTED,
        )

    image = service.get("image")
    if image is None and "build" not in service:
        reject(
            "blocked resolved configuration: every service requires image or build",
            category=ViolationCategory.POLICY,
        )
    if "image" in service and (
        not isinstance(image, str) or not is_safe_image_reference(image)
    ):
        reject(
            "blocked resolved configuration: unsafe image reference",
            category=ViolationCategory.POLICY,
        )
    if "build" in service:
        service["build"] = validate_build(service["build"], project_dir)
    container_name = service.get("container_name")
    if container_name is not None and (
        not isinstance(container_name, str) or not _valid_name(container_name)
    ):
        reject(
            "blocked resolved configuration: invalid container name",
            category=ViolationCategory.INPUT,
        )
    if service.get("privileged") not in (None, False):
        reject(
            "blocked resolved configuration: privileged container",
            category=ViolationCategory.PRIVILEGE,
        )
    if service.get("network_mode") not in (None, "bridge", "none"):
        reject(
            "blocked resolved configuration: host or joined network namespace",
            category=ViolationCategory.NETWORK,
        )
    if service.get("cap_add") not in (None, [], ()):
        reject(
            "blocked resolved configuration: added capabilities",
            category=ViolationCategory.PRIVILEGE,
        )

    cap_drop = _string_list(
        service.get("cap_drop", []),
        "malformed capability drop",
        category=ViolationCategory.PRIVILEGE,
    )
    if any(
        not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", capability)
        for capability in cap_drop
    ):
        reject(
            "blocked resolved configuration: invalid capability drop",
            category=ViolationCategory.PRIVILEGE,
        )
    if len({capability.upper() for capability in cap_drop}) != len(cap_drop):
        reject(
            "blocked resolved configuration: duplicate capability drop",
            category=ViolationCategory.PRIVILEGE,
        )
    if cap_drop:
        service["cap_drop"] = [capability.upper() for capability in cap_drop]
    else:
        service.pop("cap_drop", None)

    security_options = _string_list(
        service.get("security_opt", []),
        "malformed security options",
        category=ViolationCategory.PRIVILEGE,
    )
    if any(option not in SAFE_SECURITY_OPTIONS for option in security_options):
        reject(
            "blocked resolved configuration: custom security options",
            category=ViolationCategory.PRIVILEGE,
        )
    service["security_opt"] = ["no-new-privileges"]

    pids_limit = service.get("pids_limit", DEFAULT_PIDS)
    if isinstance(pids_limit, bool) or not isinstance(pids_limit, int):
        reject(
            "blocked resolved configuration: invalid PID limit",
            category=ViolationCategory.INPUT,
        )
    if not 1 <= pids_limit <= MAX_PIDS:
        reject(
            "blocked resolved configuration: PID limit outside the guarded range",
            category=ViolationCategory.INPUT,
        )
    service["pids_limit"] = pids_limit
    validate_resource_limits(service)
    if "extra_hosts" in service:
        service["extra_hosts"] = validate_extra_hosts(service["extra_hosts"])
    if "ulimits" in service:
        validate_ulimits(service["ulimits"])

    userns_mode = service.get("userns_mode")
    if userns_mode not in (None, "auto", "private") and not is_keep_id(userns_mode):
        reject(
            "blocked resolved configuration: unsafe user namespace",
            category=ViolationCategory.PRIVILEGE,
        )

    if service.get("http_proxy") not in (None, True, False):
        reject(
            "blocked resolved configuration: malformed proxy setting",
            category=ViolationCategory.INPUT,
        )

    restart = service.get("restart")
    if restart is not None and (
        not isinstance(restart, str)
        or not re.fullmatch(
            r"(?:no|none|always|unless-stopped|on-failure(?::[0-9]{1,4})?)",
            restart,
        )
    ):
        reject(
            "blocked resolved configuration: malformed restart policy",
            category=ViolationCategory.INPUT,
        )
    pull_policy = service.get("pull_policy")
    if pull_policy is not None and pull_policy not in {
        "always",
        "build",
        "if_not_present",
        "missing",
        "never",
        "newer",
    }:
        reject(
            "blocked resolved configuration: unsupported pull policy",
            category=ViolationCategory.PULL,
        )

    scale = service.get("scale", 1)
    if (
        isinstance(scale, bool)
        or not isinstance(scale, int)
        or not 0 <= scale <= MAX_SCALE
    ):
        reject(
            "blocked resolved configuration: service scale outside the guarded range",
            category=ViolationCategory.INPUT,
        )

    if "environment" in service:
        service["environment"] = validate_environment(service["environment"])
    labels = validate_labels(service.get("labels", {}))
    hostname = workspace_hostname(labels.get(DEVPOD_ID_LABEL))
    if "hostname" not in service and hostname:
        service["hostname"] = hostname
    labels[GUARD_POLICY_LABEL] = GUARD_POLICY_VERSION
    labels[GUARD_INSTALLATION_LABEL] = GUARD_INSTALLATION_ID
    service["labels"] = labels
    if "logging" in service:
        service["logging"] = validate_logging(service["logging"])
    if "env_file" in service:
        service["env_file"] = validate_service_env_files(
            service["env_file"], project_dir
        )
    validate_service_volumes(service, project_dir, declared_volumes)
    service["ports"] = validate_ports(service.get("ports", []))
    if "networks" in service:
        service["networks"] = validate_service_networks(
            service["networks"], declared_networks
        )
    validate_dependencies(service.get("depends_on"), service_names)
    # The first render has already selected the enabled profiles. Removing this
    # input prevents the snapshot provider from filtering the reviewed set a
    # second time with a different profile environment.
    service.pop("profiles", None)


def validate_and_rewrite(
    model: dict[str, Any], invocation: Invocation
) -> dict[str, Any]:
    """Validate a resolved model and return the exact model allowed for execution."""

    for key in list(model):
        if key.startswith("x-"):
            model.pop(key)
    if set(model) - ALLOWED_TOP_LEVEL_KEYS:
        reject(
            "blocked resolved configuration: unreviewed top-level setting",
            category=ViolationCategory.UNSUPPORTED,
        )
    services = _mapping(
        model.get("services"),
        "missing or malformed services",
        category=ViolationCategory.INPUT,
    )
    if not services:
        reject(
            "blocked resolved configuration: no enabled services",
            category=ViolationCategory.POLICY,
        )
    if not all(_valid_name(name) for name in services):
        reject(
            "blocked resolved configuration: invalid service name",
            category=ViolationCategory.INPUT,
        )
    service_names = set(services)
    declared_volumes, declared_networks = validate_named_resources(model)

    for name in sorted(service_names):
        service = _mapping(
            services[name], "malformed service", category=ViolationCategory.INPUT
        )
        try:
            validate_service(
                service,
                service_names,
                declared_volumes,
                declared_networks,
                invocation.project_dir,
            )
        except PolicyViolation as error:
            error.service_name = name
            raise
    model["name"] = invocation.project_name
    model["services"] = services
    return model
