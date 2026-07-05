#!/usr/bin/env bash
set -euo pipefail

RALFUZZ_ROOT="${RALFUZZ_ROOT:-/opt/ralfuzz}"
ARTIFACTS="${RALFUZZ_ROOT}/repro_artifacts/illustrative"
CJSON_DIR="${RALFUZZ_ROOT}/api/cJSON"
CC="${CC:-clang-18}"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "${BUILD_DIR}"' EXIT

echo "=== RALFuzz cJSON_Parse frozen demo (no API keys) ==="
echo "Artifacts: ${ARTIFACTS}"
echo

python3.11 - <<'PY'
import json
import sys
from pathlib import Path

root = Path("/opt/ralfuzz/repro_artifacts/illustrative")
stage1 = json.loads((root / "stage1_seed_summary.json").read_text(encoding="utf-8"))
stage2 = json.loads((root / "stage2_mutation_snapshot.json").read_text(encoding="utf-8"))

checks = [
    ("Stage 1 valid seeds", stage1["summary"]["total_valid_seeds"], 6),
    ("Stage 1 samples", stage1["summary"]["total_samples"], 6),
    ("Stage 2 accepted harnesses", stage2["valid_count"], 30),
    ("Stage 2 behavior signatures", stage2["unique_behavior_signature_count"], 8),
    ("Stage 2 seed corpus size", stage2["seed_count"], 6),
    ("Stage 2 duplicates", stage2["duplicate_count"], 52),
    ("Stage 2 exceptions", stage2["exception_count"], 8),
]

print("Frozen snapshot metrics (paper illustrative run):")
failed = False
for label, actual, expected in checks:
    ok = actual == expected
    mark = "OK" if ok else "MISMATCH"
    print(f"  [{mark}] {label}: {actual} (expected {expected})")
    if not ok:
        failed = True

print()
print("Bundled example files:")
for name in (
    "golden_harness.c",
    "golden_prompt.txt",
    "no_risk_harness.c",
    "no_risk_prompt.txt",
    "seed_bank.json",
    "manifest.md",
):
    path = root / name
    print(f"  {'[ok]' if path.is_file() else '[missing]'} {name}")

if failed:
    sys.exit(2)
PY

echo
echo "--- compile golden_harness.c (ASan+UBSan) ---"
"${CC}" -std=c11 \
  -I"${CJSON_DIR}" \
  -fsanitize=address,undefined \
  -o "${BUILD_DIR}/golden_harness" \
  "${ARTIFACTS}/golden_harness.c" \
  "${CJSON_DIR}/cJSON.c" \
  -fsanitize=address,undefined

echo "--- run golden_harness ---"
"${BUILD_DIR}/golden_harness"
echo "[ok] golden_harness compiled and executed under sanitizers"

echo
echo "--- compile no_risk_harness.c (ASan+UBSan) ---"
"${CC}" -std=c11 \
  -I"${CJSON_DIR}" \
  -fsanitize=address,undefined \
  -o "${BUILD_DIR}/no_risk_harness" \
  "${ARTIFACTS}/no_risk_harness.c" \
  "${CJSON_DIR}/cJSON.c" \
  -fsanitize=address,undefined

echo "--- run no_risk_harness ---"
"${BUILD_DIR}/no_risk_harness"
echo "[ok] no_risk_harness compiled and executed under sanitizers"

echo
echo "=== inspect-frozen PASSED ==="
echo "Optional live rerun (requires your own API keys at runtime):"
echo "  docker run --rm -e SEED_API_KEY=... -e MUTATION_API_KEY=... <image> run-live"
