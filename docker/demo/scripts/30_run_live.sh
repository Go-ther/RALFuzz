#!/usr/bin/env bash
set -euo pipefail

RALFUZZ_ROOT="${RALFUZZ_ROOT:-/opt/ralfuzz}"
cd "${RALFUZZ_ROOT}"

if [[ -z "${SEED_API_KEY:-}" ]]; then
  echo "Missing SEED_API_KEY. Pass your key at runtime, e.g.:" >&2
  echo "  docker run --rm -e SEED_API_KEY=\$SEED_API_KEY -e MUTATION_API_KEY=\$MUTATION_API_KEY <image> run-live" >&2
  exit 2
fi

if [[ -z "${MUTATION_API_KEY:-}" ]]; then
  echo "Missing MUTATION_API_KEY. Pass your key at runtime (never bake keys into the image)." >&2
  exit 2
fi

export CC="${CC:-clang-18}"
export CXX="${CXX:-clang++-18}"
export COVERAGE_TOOL="${COVERAGE_TOOL:-llvm-cov gcov}"
export ENABLE_SANITIZER="${ENABLE_SANITIZER:-1}"
export API_DIR="${API_DIR:-${RALFUZZ_ROOT}/api/cJSON}"
export RUNTIME_ROOT="${RUNTIME_ROOT:-${RALFUZZ_ROOT}/runtime_data/demo_live_cjson_parse}"
export TEST_API="${TEST_API:-cJSON_Parse}"
export API_NAME_REGEX="${API_NAME_REGEX:-^cJSON_Parse$}"
export SEED_BASE_URL="${SEED_BASE_URL:-https://api.deepseek.com}"
export SEED_MODEL="${SEED_MODEL:-deepseek-v4-flash}"
export SEED_ENDPOINT_MODE="${SEED_ENDPOINT_MODE:-chat}"
export SEED_SAMPLES_PER_API="${SEED_SAMPLES_PER_API:-8}"
export SEED_TARGET_VALID_PER_API="${SEED_TARGET_VALID_PER_API:-6}"
export MUTATION_PROVIDER="${MUTATION_PROVIDER:-openai_compatible}"
export MUTATION_BASE_URL="${MUTATION_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}"
export MUTATION_MODEL="${MUTATION_MODEL:-qwen3-coder-next}"
export MUTATION_MAX_VALID="${MUTATION_MAX_VALID:-30}"
export MUTATION_BATCH_SIZE="${MUTATION_BATCH_SIZE:-4}"
export MODE=full
export MUTATION_TIMEOUT="${MUTATION_TIMEOUT:-1200}"

echo "=== RALFuzz live cJSON_Parse demo (LLM keys supplied at runtime) ==="
echo "Runtime root: ${RUNTIME_ROOT}"
echo "Note: live LLM output is not bit-for-bit deterministic; compare paper numbers to repro_artifacts/illustrative/."
echo

exec bash "${RALFUZZ_ROOT}/pipeline/run_full_pipeline.sh" \
  --no-emit-fuzz-targets \
  "$@"
