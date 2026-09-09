"""Validate named resources, ports, environments, labels, and dependencies."""

from __future__ import annotations

import math
import re
from typing import Any

from paranoid_podman.common.environment import is_sensitive_env_key
from paranoid_podman.common.errors import ViolationCategory
from paranoid_podman.common.ports import is_ip_address, is_published_port, port_range
from paranoid_podman.common.provenance import is_guard_reserved_label
from paranoid_podman.compose.errors import reject
from paranoid_podman.compose.schema import _mapping, _string_list, _valid_name

RESERVED_NETWORK_NAMES = {
    "bridge",
    "container",
    "default",
    "host",
    "none",
    "ns",
    "pasta",
    "private",
    "slirp4netns",
}


def validate_resource_limits(service: dict[str, Any]) -> None:
    if "cpus" in service:
        cpus = service["cpus"]
        if isinstance(cpus, bool) or not isinstance(cpus, (str, int, float)):
            reject("invalid Compose CPU limit", category=ViolationCategory.INPUT)
        try:
            number = float(cpus)
        except (ValueError, OverflowError):
            reject("invalid Compose CPU limit", category=ViolationCategory.INPUT)
        if not math.isfinite(number) or number < 0:
            reject("invalid Compose CPU limit", category=ViolationCategory.INPUT)
    for key in ("mem_limit", "mem_reservation", "memswap_limit", "shm_size"):
        if key not in service:
            continue
        value = service[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (str, int))
            or not (
                re.fullmatch(
                    r"[0-9]+(?:\.[0-9]+)?(?:[bkmg](?:b)?)?", str(value), re.IGNORECASE
                )
                or (key == "memswap_limit" and str(value) == "-1")
            )
        ):
            reject("invalid Compose memory limit", category=ViolationCategory.INPUT)


def validate_extra_hosts(value: Any) -> list[str]:
    if isinstance(value, dict):
        entries = [f"{name}:{address}" for name, address in value.items()]
    else:
        entries = _string_list(
            value, "malformed host mappings", category=ViolationCategory.HOST_MAPPING
        )
    allowed = {
        "host.docker.internal:host-gateway",
        "host.containers.internal:host-gateway",
    }
    entries = [entry.replace("=", ":", 1) for entry in entries]
    if any(entry not in allowed for entry in entries):
        reject(
            "unsupported host mapping; use host.docker.internal or host.containers.internal with host-gateway",
            category=ViolationCategory.HOST_MAPPING,
        )
    return entries


def validate_ulimits(value: Any) -> None:
    limits = _mapping(
        value, "malformed resource limits", category=ViolationCategory.INPUT
    )
    for name, definition in limits.items():
        if name not in {"nofile", "nproc"}:
            reject(
                "unsupported resource limit; accepted names are nofile and nproc",
                category=ViolationCategory.UNSUPPORTED,
            )
        if isinstance(definition, dict):
            if set(definition) != {"soft", "hard"}:
                reject(
                    "resource limits require soft and hard values",
                    category=ViolationCategory.INPUT,
                )
            soft, hard = definition["soft"], definition["hard"]
        else:
            soft = hard = definition
        if (
            any(
                isinstance(number, bool) or not isinstance(number, int)
                for number in (soft, hard)
            )
            or not 1 <= soft <= hard <= 1_048_576
        ):
            reject(
                "resource limits must satisfy 1 <= soft <= hard <= 1048576",
                category=ViolationCategory.INPUT,
            )


def validate_named_resources(model: dict[str, Any]) -> tuple[set[str], set[str]]:
    volumes = _mapping(
        model.get("volumes", {}),
        "malformed named volumes",
        category=ViolationCategory.MOUNT,
    )
    networks = _mapping(
        model.get("networks", {}),
        "malformed networks",
        category=ViolationCategory.NETWORK,
    )

    for name, definition in volumes.items():
        if not _valid_name(name):
            reject(
                "blocked resolved configuration: invalid named volume",
                category=ViolationCategory.MOUNT,
            )
        if definition is None:
            definition = {}
            volumes[name] = definition
        definition = _mapping(
            definition, "malformed named volume", category=ViolationCategory.MOUNT
        )
        if set(definition) - {"driver", "external"}:
            reject(
                "blocked resolved configuration: unreviewed named volume settings",
                category=ViolationCategory.MOUNT,
            )
        if definition.get("external") not in (None, False):
            reject(
                "blocked resolved configuration: external named volume",
                category=ViolationCategory.MOUNT,
            )
        if definition.get("driver") not in (None, "local"):
            reject(
                "blocked resolved configuration: custom volume driver",
                category=ViolationCategory.MOUNT,
            )
        volumes[name] = {}

    for name, definition in networks.items():
        if not _valid_name(name):
            reject(
                "blocked resolved configuration: invalid network name",
                category=ViolationCategory.NETWORK,
            )
        if definition is None:
            definition = {}
            networks[name] = definition
        definition = _mapping(
            definition, "malformed network", category=ViolationCategory.NETWORK
        )
        if set(definition) - {
            "driver",
            "enable_ipv4",
            "enable_ipv6",
            "external",
            "internal",
            "name",
        }:
            reject(
                "blocked resolved configuration: unreviewed network settings",
                category=ViolationCategory.NETWORK,
            )
        if definition.get("external") not in (None, False):
            reject(
                "blocked resolved configuration: external network",
                category=ViolationCategory.NETWORK,
            )
        if definition.get("driver") not in (None, "bridge"):
            reject(
                "blocked resolved configuration: custom network driver",
                category=ViolationCategory.NETWORK,
            )
        if "name" in definition:
            engine_name = definition["name"]
            if (
                not isinstance(engine_name, str)
                or not _valid_name(engine_name)
                or engine_name.lower() in RESERVED_NETWORK_NAMES
            ):
                reject(
                    "blocked resolved configuration: invalid or reserved network name",
                    category=ViolationCategory.NETWORK,
                )
        for key in ("enable_ipv4", "enable_ipv6", "internal"):
            if key in definition and not isinstance(definition[key], bool):
                reject(
                    "blocked resolved configuration: malformed network setting",
                    category=ViolationCategory.NETWORK,
                )
        definition.pop("external", None)

    return set(volumes), set(networks)


def validate_ports(value: Any) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        reject(
            "blocked resolved configuration: malformed published ports",
            category=ViolationCategory.PORT,
        )
    validated: list[Any] = []
    for port in value:
        if isinstance(port, (str, int)) and not isinstance(port, bool):
            if not is_published_port(str(port)):
                reject(
                    "blocked resolved configuration: invalid published port",
                    category=ViolationCategory.PORT,
                )
            validated.append(port)
            continue
        port_mapping = dict(
            _mapping(port, "malformed published port", category=ViolationCategory.PORT)
        )
        allowed_keys = {
            "app_protocol",
            "host_ip",
            "mode",
            "name",
            "protocol",
            "published",
            "target",
        }
        if set(port_mapping) - allowed_keys:
            reject(
                "blocked resolved configuration: unreviewed published port setting",
                category=ViolationCategory.PORT,
            )
        address = port_mapping.get("host_ip")
        if address is not None and (
            not isinstance(address, str) or (address and not is_ip_address(address))
        ):
            reject(
                "blocked resolved configuration: invalid published port address",
                category=ViolationCategory.PORT,
            )
        if isinstance(address, str) and ":" in address and not address.startswith("["):
            # podman-compose 1.6.x concatenates host_ip directly into -p syntax.
            port_mapping["host_ip"] = f"[{address}]"
        if port_mapping.get("protocol", "tcp") not in ("tcp", "udp"):
            reject(
                "blocked resolved configuration: unsupported port protocol",
                category=ViolationCategory.UNSUPPORTED,
            )
        for key in ("published", "target"):
            number = port_mapping.get(key)
            if key == "published" and number in (None, ""):
                port_mapping.pop(key, None)
                continue
            if port_range(number) is None:
                reject(
                    "blocked resolved configuration: invalid published port",
                    category=ViolationCategory.PORT,
                )
        if port_mapping.get("mode", "host") not in ("host", "ingress"):
            reject(
                "blocked resolved configuration: unsupported published port mode",
                category=ViolationCategory.PORT,
            )
        validated.append(port_mapping)
    return validated


def validate_environment(value: Any) -> dict[str, Any]:
    environment = _mapping(
        value or {},
        "malformed service environment",
        category=ViolationCategory.ENVIRONMENT,
    )
    validated = {}
    for key, env_value in environment.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            reject(
                "blocked resolved configuration: invalid environment variable name",
                category=ViolationCategory.ENVIRONMENT,
            )
        if env_value is None:
            reject(
                "blocked resolved configuration: implicit host environment forwarding",
                category=ViolationCategory.ENVIRONMENT,
            )
        if not isinstance(env_value, (str, int, float, bool)):
            reject(
                "blocked resolved configuration: malformed environment value",
                category=ViolationCategory.ENVIRONMENT,
            )
        validated[key] = env_value
    return validated


def validate_labels(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        entries: list[tuple[Any, Any]] = []
        for label in value:
            if not isinstance(label, str) or "=" not in label:
                reject(
                    "blocked resolved configuration: malformed service label",
                    category=ViolationCategory.INPUT,
                )
            key, label_value = label.split("=", 1)
            entries.append((key, label_value))
    else:
        entries = list(
            _mapping(
                value or {},
                "malformed service labels",
                category=ViolationCategory.INPUT,
            ).items()
        )

    labels: dict[str, Any] = {}
    reserved_prefixes = (
        "com.docker.compose.",
        "io.containers.autoupdate",
        "io.podman.compose.",
    )
    for key, label_value in entries:
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,254}", key)
            or key.startswith(reserved_prefixes)
            or is_guard_reserved_label(key)
            or is_sensitive_env_key(key)
        ):
            reject(
                "blocked resolved configuration: unsafe service label",
                category=ViolationCategory.POLICY,
            )
        if not isinstance(label_value, (str, int, float, bool)):
            reject(
                "blocked resolved configuration: malformed service label value",
                category=ViolationCategory.INPUT,
            )
        if isinstance(label_value, str) and len(label_value) > 4096:
            reject(
                "blocked resolved configuration: service label is too large",
                category=ViolationCategory.INPUT,
            )
        if key in labels:
            reject(
                "blocked resolved configuration: duplicate service label",
                category=ViolationCategory.INPUT,
            )
        labels[key] = label_value
    return labels


def validate_logging(value: Any) -> dict[str, Any]:
    logging_config = _mapping(
        value, "malformed logging configuration", category=ViolationCategory.INPUT
    )
    if set(logging_config) - {"driver", "options"}:
        reject(
            "blocked resolved configuration: unreviewed logging setting",
            category=ViolationCategory.UNSUPPORTED,
        )
    driver = logging_config.get("driver", "k8s-file")
    if driver not in {"journald", "json-file", "k8s-file", "none"}:
        reject(
            "blocked resolved configuration: unsupported log driver",
            category=ViolationCategory.UNSUPPORTED,
        )
    options = _mapping(
        logging_config.get("options", {}),
        "malformed log options",
        category=ViolationCategory.INPUT,
    )
    if set(options) - {"max-file", "max-size", "tag"}:
        reject(
            "blocked resolved configuration: log option may target the host",
            category=ViolationCategory.POLICY,
        )
    if any(not isinstance(value, (str, int)) for value in options.values()):
        reject(
            "blocked resolved configuration: malformed log option",
            category=ViolationCategory.INPUT,
        )
    return {"driver": driver, "options": options}


def validate_service_networks(value: Any, declared_networks: set[str]) -> Any:
    if value is None:
        return None
    if isinstance(value, list):
        if not all(isinstance(name, str) and _valid_name(name) for name in value):
            reject(
                "blocked resolved configuration: malformed service networks",
                category=ViolationCategory.NETWORK,
            )
        names = set(value)
    else:
        networks = _mapping(
            value, "malformed service networks", category=ViolationCategory.NETWORK
        )
        names = set(networks)
        for _name, settings in networks.items():
            if settings is None:
                continue
            settings = _mapping(
                settings,
                "malformed service network settings",
                category=ViolationCategory.NETWORK,
            )
            if set(settings) - {"aliases"}:
                reject(
                    "blocked resolved configuration: unreviewed service network settings",
                    category=ViolationCategory.NETWORK,
                )
            aliases = _string_list(
                settings.get("aliases", []),
                "malformed network aliases",
                category=ViolationCategory.NETWORK,
            )
            if not all(_valid_name(alias) for alias in aliases):
                reject(
                    "blocked resolved configuration: invalid network alias",
                    category=ViolationCategory.NETWORK,
                )
    if any(name != "default" and name not in declared_networks for name in names):
        reject(
            "blocked resolved configuration: undeclared network",
            category=ViolationCategory.NETWORK,
        )
    return value


def validate_dependencies(value: Any, service_names: set[str]) -> None:
    if value is None:
        return
    dependencies = _mapping(
        value, "malformed service dependencies", category=ViolationCategory.INPUT
    )
    if any(name not in service_names for name in dependencies):
        reject(
            "blocked resolved configuration: unknown service dependency",
            category=ViolationCategory.UNSUPPORTED,
        )
    for settings in dependencies.values():
        if settings is None:
            continue
        settings = _mapping(
            settings, "malformed service dependency", category=ViolationCategory.INPUT
        )
        if set(settings) - {"condition", "required", "restart"}:
            reject(
                "blocked resolved configuration: unreviewed dependency setting",
                category=ViolationCategory.UNSUPPORTED,
            )
        if settings.get("condition", "service_started") not in (
            "service_completed_successfully",
            "service_healthy",
            "service_started",
        ):
            reject(
                "blocked resolved configuration: invalid dependency condition",
                category=ViolationCategory.INPUT,
            )
        for key in ("required", "restart"):
            if key in settings and not isinstance(settings[key], bool):
                reject(
                    "blocked resolved configuration: malformed dependency setting",
                    category=ViolationCategory.INPUT,
                )
