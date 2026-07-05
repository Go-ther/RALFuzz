#!/usr/bin/env bash
set -euo pipefail

RALFUZZ_ROOT="${RALFUZZ_ROOT:-/opt/ralfuzz}"
fail=0

check_cmd() {
  local name="$1"
  if command -v "$name" >/dev/null 2>&1; then
    echo "[ok] $name: $(command -v "$name")"
  else
    echo "[FAIL] missing: $name"
    fail=1
  fi
}

echo "=== RALFuzz demo preflight ==="
check_cmd clang-18
check_cmd python3.11
check_cmd llvm-cov-18

echo "--- versions ---"
clang-18 --version | head -n 1
python3.11 --version

echo "--- layout ---"
for path in \
  "${RALFUZZ_ROOT}/repro_artifacts/illustrative/golden_harness.c" \
  "${RALFUZZ_ROOT}/api/cJSON/cJSON.c" \
  "${RALFUZZ_ROOT}/pipeline/run_full_pipeline.py" \
  "${RALFUZZ_ROOT}/mutation/ev_generation.py" \
  "${RALFUZZ_ROOT}/seed_generation/generate_c_seeds.py"
do
  if [[ -f "$path" ]]; then
    echo "[ok] $path"
  else
    echo "[FAIL] missing: $path"
    fail=1
  fi
done

echo "--- LLM env (optional; only needed for run-live) ---"
for var in SEED_API_KEY MUTATION_API_KEY; do
  if [[ -n "${!var:-}" ]]; then
    echo "[set] $var"
  else
    echo "[unset] $var"
  fi
done

if [[ "$fail" -ne 0 ]]; then
  echo "=== preflight FAILED ==="
  exit 1
fi

echo "=== preflight OK ==="
