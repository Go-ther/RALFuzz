#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_DEFAULT="python3"
else
  PYTHON_DEFAULT="python"
fi

PYTHON_BIN="${PYTHON_BIN:-$PYTHON_DEFAULT}"

# Important run parameters. Override by editing here or exporting env vars.
MODE="${MODE:-full}"
API_DIR="${API_DIR:-}"
RUNTIME_ROOT="${RUNTIME_ROOT:-${REPO_ROOT}/runtime_data}"
TEST_API="${TEST_API:-all}"
API_NAME_REGEX="${API_NAME_REGEX:-}"
COMPILER="${COMPILER:-gcc}"

SEED_BASE_URL="${SEED_BASE_URL:-http://localhost:11434}"
SEED_API_KEY="${SEED_API_KEY:-ollama}"
SEED_MODEL="${SEED_MODEL:-deepseek-v3.2:cloud}"
SEED_ENDPOINT_MODE="${SEED_ENDPOINT_MODE:-ollama}"
SEED_SAMPLES_PER_API="${SEED_SAMPLES_PER_API:-12}"
SEED_TARGET_VALID_PER_API="${SEED_TARGET_VALID_PER_API:-8}"

MUTATION_PROVIDER="${MUTATION_PROVIDER:-openai_compatible}"
MUTATION_BASE_URL="${MUTATION_BASE_URL:-http://localhost:11434/v1}"
MUTATION_API_KEY="${MUTATION_API_KEY:-ollama}"
MUTATION_MODEL="${MUTATION_MODEL:-qwen3-coder:480b-cloud}"
MUTATION_MAX_VALID="${MUTATION_MAX_VALID:-50}"
MUTATION_BATCH_SIZE="${MUTATION_BATCH_SIZE:-4}"
MUTATION_TIMEOUT="${MUTATION_TIMEOUT:-1200}"

if [[ -z "${API_DIR}" ]]; then
  echo "API_DIR is required. Set API_DIR or pass --api-dir <path-to-target-library>." >&2
  exit 2
fi

CMD=(
  "${PYTHON_BIN}" "${SCRIPT_DIR}/run_full_pipeline.py"
  --mode "${MODE}"
  --api-dir "${API_DIR}"
  --runtime-root "${RUNTIME_ROOT}"
  --api "${TEST_API}"
  --compiler "${COMPILER}"
  --seed-base-url "${SEED_BASE_URL}"
  --seed-api-key "${SEED_API_KEY}"
  --seed-model "${SEED_MODEL}"
  --seed-endpoint-mode "${SEED_ENDPOINT_MODE}"
  --seed-samples-per-api "${SEED_SAMPLES_PER_API}"
  --seed-target-valid-per-api "${SEED_TARGET_VALID_PER_API}"
  --mutation-llm-provider "${MUTATION_PROVIDER}"
  --mutation-max-valid "${MUTATION_MAX_VALID}"
  --mutation-batch-size "${MUTATION_BATCH_SIZE}"
  --mutation-timeout "${MUTATION_TIMEOUT}"
)

if [[ -n "${API_NAME_REGEX}" ]]; then
  CMD+=(--api-name-regex "${API_NAME_REGEX}")
fi

if [[ "${MUTATION_PROVIDER}" != "mock" ]]; then
  CMD+=(--mutation-model "${MUTATION_MODEL}")
fi

if [[ "${MUTATION_PROVIDER}" == "openai_compatible" || "${MUTATION_PROVIDER}" == "deepseek" ]]; then
  CMD+=(--mutation-api-base "${MUTATION_BASE_URL}" --mutation-api-key "${MUTATION_API_KEY}")
fi

if [[ "${DRY_RUN:-0}" != "0" ]]; then
  CMD+=(--dry-run)
fi
if [[ "${RESUME:-0}" != "0" ]]; then
  CMD+=(--resume)
fi
if [[ "${ENABLE_SANITIZER:-0}" != "0" ]]; then
  CMD+=(--enable-sanitizer)
fi

exec "${CMD[@]}" "$@"
