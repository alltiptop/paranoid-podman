"""Record an exact runtime-only lock from an already downloaded wheelhouse."""

import argparse
import hashlib
import json
import zipfile
from email.parser import BytesParser
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    directory = parser.parse_args().directory
    inventory: dict[str, str] = {}
    requirements = []
    for wheel in sorted(directory.glob("*.whl")):
        if wheel.is_symlink():
            raise ValueError("runtime wheels must be regular local files")
        with zipfile.ZipFile(wheel) as archive:
            metadata_names = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_names) != 1:
                raise ValueError("ambiguous runtime wheel metadata")
            metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
        name = metadata["Name"].lower().replace("_", "-")
        if name in inventory:
            raise ValueError(
                "wheelhouse must contain one wheel per runtime distribution"
            )
        inventory[name] = metadata["Version"]
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        requirements.append(f"{name}=={inventory[name]} --hash=sha256:{digest}")
    if set(inventory) != {"paranoid-podman", "pyyaml", "python-dotenv"}:
        raise ValueError(
            "wheelhouse must contain only the project, PyYAML, and python-dotenv"
        )
    (directory / "runtime.lock").write_text(
        "\n".join(requirements) + "\n", encoding="utf-8"
    )
    (directory / "inventory.json").write_text(
        json.dumps(inventory, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
