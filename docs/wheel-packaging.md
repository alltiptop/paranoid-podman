# Advanced: build and install offline

For normal installation, run `./install.sh install`. It prepares the Python tools,
application wheel, and runtime dependencies automatically. This guide is for
maintainers and users who need to prepare an offline installation.

Run these commands in **Bash**, from the repository root, using the Python
version that will run the application. An explicit wheel install uses four inputs: the
project wheel, its trusted SHA-256, a runtime wheelhouse, and an external
installer Python. A wheelhouse is a directory of local wheels and their hash lock.

## 1. Prepare the build environment

This setup downloads build tools into a separate environment:

```bash
python3 -m venv .venv-build
.venv-build/bin/python -m pip install --only-binary=:all: -r requirements-build.txt
```

Build the application using those prepared tools. The build command does not
install dependencies or run validation:

```bash
PATH="$PWD/.venv-build/bin:$PATH" scripts/build.sh
```

Output: `dist/paranoid_podman-<version>-py3-none-any.whl` and `<wheel>.sha256`.
The current `VERSION` value is `0.1.0`. Use
`scripts/build.sh --outdir DIRECTORY` to change the output directory.
Reproducible builds use pinned tools and `SOURCE_DATE_EPOCH` (default `315532800`).

## 2. Prepare runtime dependencies

This setup downloads the installer and runtime dependency wheels for the
current Python/platform. Use a fresh `runtime-wheels` directory: the lock
requires exactly one wheel each for paranoid-podman, PyYAML, and python-dotenv.

```bash
python3 -m venv .venv-installer
.venv-installer/bin/python -m pip install --only-binary=:all: -r requirements-installer.txt
mkdir runtime-wheels
.venv-installer/bin/python -m pip download --only-binary=:all: -r requirements.txt \
  -c tests/runtime-constraints.txt --dest runtime-wheels
```

Select the wheel just built. The filename below matches the current version;
use the new filename if `VERSION` changes:

```bash
pp_wheel="$PWD/dist/paranoid_podman-0.1.0-py3-none-any.whl"
read -r pp_sha256 _ < "$pp_wheel.sha256"
cp "$pp_wheel" runtime-wheels/
python3 -B scripts/wheelhouse_lock.py runtime-wheels
```

Reading the checksum beside the wheel is appropriate for your own verified
build. For a downloaded release, obtain the expected checksum through a trusted
release channel. A hash supplied with an untrusted file does not authenticate it.

## 3. Install offline

Review `runtime-wheels/runtime.lock`, then select the prepared inputs:

```bash
wheel_options=(
  --wheel "$pp_wheel"
  --sha256 "$pp_sha256"
  --wheelhouse "$PWD/runtime-wheels"
  --installer-python "$PWD/.venv-installer/bin/python"
)
./install.sh install "${wheel_options[@]}" --dry-run
./install.sh install "${wheel_options[@]}"
./install.sh status
```

For provider selection, command backups, PATH setup, or optional DevPod support,
use the [installation options](../README.md#install). Choose those options before
the actual install. Add `--bindir` and `--libdir` consistently for custom locations.

With explicit wheel options, install/update use only local hashed wheels. The external installer supplies
pip; each installed release gets a private environment without pip, build tools,
validation tools, or type stubs. Missing, modified, or incompatible wheels fail
without a download or source-build fallback. `--dry-run` validates the inputs
without creating an installation.

An explicit wheel update uses all four options for the new wheel and its matching wheelhouse.
Prepare that wheelhouse separately and regenerate its lock before updating.
Status and uninstall need no wheel arguments and do not invoke pip.
Version 0.1.0 is the first supported installation format.

## Validation and artifact tests

Runtime dependencies are declared in `pyproject.toml`. After changing them, run
`python3 -B scripts/runtime_requirements.py` to regenerate `requirements.txt`.
Build, installer, and validation requirements stay in separate files. See
[CONTRIBUTING.md](../CONTRIBUTING.md) for source checks.

For the explicit wheel tests, first prepare a reference runtime with the same
local wheels. This creates only a local test environment:

```bash
python3 -m venv --without-pip .venv-runtime
.venv-installer/bin/python -I -m pip --isolated \
  --python "$PWD/.venv-runtime/bin/python" install \
  --no-index --no-cache-dir --find-links "$PWD/runtime-wheels" \
  --only-binary=:all: --require-hashes --no-compile \
  --requirement "$PWD/runtime-wheels/runtime.lock"
python3 -B scripts/smoke_wheel.py \
  --wheel "$pp_wheel" --sha256 "$pp_sha256" \
  --python "$PWD/.venv-runtime/bin/python" \
  --inventory "$PWD/runtime-wheels/inventory.json"
python3 -B -m tests.lifecycle.test_wheel \
  --wheelhouse "$PWD/runtime-wheels" \
  --installer-python "$PWD/.venv-installer/bin/python"
```

These tests use real offline pip/venv and fake providers. They cover artifact
integrity, runtime isolation, installation, backups, updates, rollback, SSH
restoration, and repeated updates. Local discovery skips artifact-dependent
cases until these inputs are explicitly supplied. CI builds twice and runs the
artifact checks on Python 3.10 and 3.14. Installing the wheel alone creates no
public command launchers; the installation lifecycle owns those commands.

The automatic installation has a separate offline test. Prepare a directory with
the wheels from `requirements-build.txt`, `requirements-installer.txt`, and
`requirements.txt` for the test Python version, then run:

```bash
python3 -B -m tests.lifecycle.test_bootstrap --wheelhouse /absolute/path/setup-wheels
```

This runs the plain install, update, and uninstall commands in temporary homes,
with and without DevPod. It uses real builds and pip installations with
`PIP_NO_INDEX=1` and fake providers; it starts no containers.
