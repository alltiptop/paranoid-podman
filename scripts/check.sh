#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$PROJECT_DIR"

if [[ $# -gt 1 ]]; then
  echo "usage: scripts/check.sh [local|tests|syntax|lint|all]" >&2
  exit 2
fi

case "${1:-local}" in
  local|all)
    scripts/test.sh
    scripts/lint.sh
    scripts/audit.sh secrets
    if [[ "${1:-local}" == all ]]; then
      scripts/audit.sh dependencies
    fi
    ;;
  tests) exec scripts/test.sh ;;
  syntax) exec python3 -B scripts/validation.py syntax ;;
  lint) exec scripts/lint.sh ;;
  *)
    echo "usage: scripts/check.sh [local|tests|syntax|lint|all]" >&2
    exit 2
    ;;
esac
