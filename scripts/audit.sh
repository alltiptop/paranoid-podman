#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$PROJECT_DIR"

if [[ $# -ne 1 ]]; then
  echo "usage: scripts/audit.sh {secrets|dependencies}" >&2
  exit 2
fi

case "$1" in
  secrets) exec python3 -B scripts/validation.py secrets ;;
  dependencies)
    # Separate resolutions may contact indexes and vulnerability services.
    result=0
    for requirements in requirements.txt requirements-dev.txt; do
      echo "Auditing $requirements"
      python3 -B -m pip_audit \
        --requirement "$requirements" \
        --strict \
        --progress-spinner off || result=1
    done
    exit "$result"
    ;;
  *)
    echo "usage: scripts/audit.sh {secrets|dependencies}" >&2
    exit 2
    ;;
esac
