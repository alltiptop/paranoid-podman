"""Prepare a private wheel release at its final path before activation."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

from paranoid_podman.lifecycle.artifacts import Delivery
from paranoid_podman.lifecycle.errors import fail
from paranoid_podman.lifecycle.integrity import (
    release_inventory,
    remove_new_tree,
    validate_venv_links,
    verify_inventory,
)
from paranoid_podman.lifecycle.runtime import install_runtime, verify_runtime


def create_wheel_release(
    install_root: Path,
    providers: dict[str, str],
    installation_id: str,
    delivery: Delivery,
    launcher: bytes,
    dry_run: bool,
) -> Path:
    identity = hashlib.sha256(
        delivery.lock_text().encode()
        + json.dumps(providers, sort_keys=True).encode()
        + launcher.replace(installation_id.encode(), b"0" * 64)
    ).hexdigest()[:16]
    release = install_root / "releases" / f"{delivery.project.version}-wheel-{identity}"
    provenance = hashlib.sha256(installation_id.encode()).hexdigest()
    if release.exists() or release.is_symlink():
        if release.is_symlink() or not release.is_dir():
            fail(f"invalid wheel release directory: {release}")
        metadata = release / ".manifest.json"
        if metadata.is_symlink() or not metadata.is_file():
            fail(f"incomplete wheel release: {release}")
        manifest = json.loads(metadata.read_text(encoding="utf-8"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("format") != 2
            or manifest.get("project") != "paranoid-podman"
            or manifest.get("release") != release.name
            or manifest.get("installation_id_sha256") != provenance
            or manifest.get("runtime_lock") != delivery.lock_text()
        ):
            fail("existing wheel release metadata does not match the requested release")
        verify_inventory(release, manifest)
        return release
    if dry_run:
        return release

    install_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    install_root.chmod(0o700)
    release.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Venv scripts contain absolute paths. Claim the final path, never rename it.
    release.mkdir(mode=0o700)
    try:
        (release / ".incomplete").touch(mode=0o600)
        with tempfile.TemporaryDirectory(
            prefix=".artifacts-", dir=install_root
        ) as temporary:
            scratch = Path(temporary)
            install_runtime(release, Path(providers["python"]), delivery, scratch)
            validate_venv_links(release, Path(providers["python"]))
            verify_runtime(release, delivery.inventory, scratch)
        (release / "launch").write_bytes(launcher)
        (release / "launch").chmod(0o700)
        (release / ".incomplete").unlink()
        manifest = {
            **release_inventory(release),
            "format": 2,
            "project": "paranoid-podman",
            "release": release.name,
            "version": delivery.project.version,
            "providers": providers,
            "installation_id_sha256": provenance,
            "runtime_lock": delivery.lock_text(),
        }
        descriptor = os.open(
            release / ".manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(manifest, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        verify_inventory(release, manifest)
    except BaseException:
        remove_new_tree(release)
        raise
    return release
