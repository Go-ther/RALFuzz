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
API_DIR="${API_DIR:-${REPO_ROOT}/api/cJSON}"
RUNTIME_ROOT="${RUNTIME_ROOT:-${REPO_ROOT}/runtime_data/illustrative_cjson_parse_v1}"
TEST_API="${TEST_API:-cJSON_Parse}"
API_NAME_REGEX="${API_NAME_REGEX:-^cJSON_Parse$}"
COMPILER="${COMPILER:-clang}"
COVERAGE_TOOL="${COVERAGE_TOOL:-llvm-cov gcov}"

SEED_BASE_URL="${SEED_BASE_URL:-https://api.deepseek.com}"
SEED_API_KEY="${SEED_API_KEY:-${DEEPSEEK_API_KEY:-${OPENAI_API_KEY:-}}}"
SEED_MODEL="${SEED_MODEL:-deepseek-v4-flash}"
SEED_ENDPOINT_MODE="${SEED_ENDPOINT_MODE:-chat}"
SEED_SAMPLES_PER_API="${SEED_SAMPLES_PER_API:-8}"
SEED_TARGET_VALID_PER_API="${SEED_TARGET_VALID_PER_API:-6}"
SEED_NETWORK_RETRIES="${SEED_NETWORK_RETRIES:-2}"
SEED_NETWORK_RETRY_BACKOFF_SEC="${SEED_NETWORK_RETRY_BACKOFF_SEC:-2.0}"

MUTATION_PROVIDER="${MUTATION_PROVIDER:-openai_compatible}"
MUTATION_BASE_URL="${MUTATION_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}"
MUTATION_API_KEY="${MUTATION_API_KEY:-${DASHSCOPE_API_KEY:-${QWEN_API_KEY:-${LLM_API_KEY:-}}}}"
MUTATION_MODEL="${MUTATION_MODEL:-qwen3-coder-next}"
MUTATION_MAX_VALID="${MUTATION_MAX_VALID:-30}"
MUTATION_BATCH_SIZE="${MUTATION_BATCH_SIZE:-4}"
MUTATION_TIMEOUT="${MUTATION_TIMEOUT:-1200}"

RUN_SEED=0
RUN_MUTATION=0
if [[ "${MODE}" == "full" || "${MODE}" == "seed" ]]; then
  RUN_SEED=1
fi
if [[ "${MODE}" == "full" || "${MODE}" == "mutation" ]]; then
  RUN_MUTATION=1
fi

if [[ "${DRY_RUN:-0}" == "0" && "${RUN_SEED}" == "1" && "${SEED_BASE_URL}" == https://api.deepseek.com* && -z "${SEED_API_KEY}" ]]; then
  echo "Missing seed API key. Set SEED_API_KEY." >&2
  exit 2
fi

if [[ "${DRY_RUN:-0}" == "0" && "${RUN_MUTATION}" == "1" ]] && [[ "${MUTATION_PROVIDER}" == "openai_compatible" || "${MUTATION_PROVIDER}" == "deepseek" ]] && [[ -z "${MUTATION_API_KEY}" ]]; then
  echo "Missing mutation API key. Set MUTATION_API_KEY." >&2
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
  --seed-model "${SEED_MODEL}"
  --seed-endpoint-mode "${SEED_ENDPOINT_MODE}"
  --seed-samples-per-api "${SEED_SAMPLES_PER_API}"
  --seed-target-valid-per-api "${SEED_TARGET_VALID_PER_API}"
  --seed-network-retries "${SEED_NETWORK_RETRIES}"
  --seed-network-retry-backoff-sec "${SEED_NETWORK_RETRY_BACKOFF_SEC}"
  --mutation-llm-provider "${MUTATION_PROVIDER}"
  --mutation-max-valid "${MUTATION_MAX_VALID}"
  --mutation-batch-size "${MUTATION_BATCH_SIZE}"
  --mutation-timeout "${MUTATION_TIMEOUT}"
  --coverage-tool "${COVERAGE_TOOL}"
)

if [[ -n "${API_NAME_REGEX}" ]]; then
  CMD+=(--api-name-regex "${API_NAME_REGEX}")
fi
if [[ -n "${SEED_API_KEY}" ]]; then
  CMD+=(--seed-api-key "${SEED_API_KEY}")
fi

if [[ "${MUTATION_PROVIDER}" != "mock" ]]; then
  CMD+=(--mutation-model "${MUTATION_MODEL}")
fi

if [[ "${MUTATION_PROVIDER}" == "openai_compatible" || "${MUTATION_PROVIDER}" == "deepseek" ]]; then
  CMD+=(--mutation-api-base "${MUTATION_BASE_URL}")
  if [[ -n "${MUTATION_API_KEY}" ]]; then
    CMD+=(--mutation-api-key "${MUTATION_API_KEY}")
  fi
fi

if [[ "${DRY_RUN:-0}" != "0" ]]; then
  CMD+=(--dry-run)
fi
if [[ "${RESUME:-0}" != "0" ]]; then
  CMD+=(--resume)
fi
if [[ "${ENABLE_SANITIZER:-1}" != "0" ]]; then
  CMD+=(--enable-sanitizer)
fi

exec "${CMD[@]}" "$@"
