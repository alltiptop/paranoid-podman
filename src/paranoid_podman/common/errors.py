"""Typed policy failures; diagnostics never depend on message wording."""

from enum import Enum, auto


class ViolationCategory(Enum):
    """Stable decision categories shared by direct and Compose guards."""

    BUILD_CONTEXT = auto()
    SECRET = auto()
    INTERPOLATION = auto()
    PROVENANCE = auto()
    INTERRUPTED = auto()
    CANCELLED = auto()
    COMPOSE_FILE = auto()
    PULL = auto()
    PORT = auto()
    HOST_MAPPING = auto()
    NETWORK = auto()
    MOUNT = auto()
    ENVIRONMENT = auto()
    PRIVILEGE = auto()
    INSTALLATION = auto()
    UNSUPPORTED = auto()
    PROVIDER = auto()
    INPUT = auto()
    POLICY = auto()


class GuardViolation(ValueError):
    def __init__(self, reason: str, *, category: ViolationCategory) -> None:
        super().__init__(reason)
        self.category = category


class BuildInputViolation(GuardViolation):
    """A Dockerfile or build context rejected by shared input validation."""
