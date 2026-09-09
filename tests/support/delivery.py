"""Explicitly supplied build artifacts for lifecycle acceptance tests."""

from dataclasses import dataclass
from pathlib import Path
from unittest import SkipTest

from paranoid_podman.lifecycle.artifacts import Delivery, load_delivery


@dataclass(frozen=True)
class PreparedDelivery:
    delivery: Delivery
    wheelhouse: Path

    def arguments(self):
        project = self.delivery.project
        return (
            "--wheel",
            str(project.path),
            "--sha256",
            project.sha256,
            "--wheelhouse",
            str(self.wheelhouse),
            "--installer-python",
            str(self.delivery.installer_python),
        )


prepared: PreparedDelivery | None = None


def configure(wheelhouse: Path, installer: Path):
    import hashlib

    global prepared
    wheelhouse = wheelhouse.absolute()
    wheel = next(wheelhouse.glob("paranoid_podman-*.whl"))
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    prepared = PreparedDelivery(
        load_delivery(wheel, digest, wheelhouse, installer.absolute()),
        wheelhouse,
    )


def require():
    if prepared is None:
        raise SkipTest("requires prepared wheels; run tests.lifecycle.test_wheel")
    return prepared
