#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

export MODE=mutation
exec "${SCRIPT_DIR}/run_full_pipeline.sh" "$@"
