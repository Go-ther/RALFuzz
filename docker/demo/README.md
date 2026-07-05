# RALFuzz reproducibility capsule (`cJSON_Parse` demo)

Minimal Docker image for SoftwareX **Reproducible Capsule** (Reviewer #3 item 12).

**Default path needs no LLM API keys:** verify frozen paper artifacts and compile the bundled illustrative harnesses under Clang + ASan/UBSan.

Large-scale experiment outputs (`runtime_data/`), batch experiment scripts, and external-tool compare environments are **not** included in this image.

## Quick start

From repository root:

```bash
# Build (first time: ~5–15 min depending on network)
docker compose -f docker/demo/docker-compose.yml build

# Default: offline inspect + compile (no API keys)
docker compose -f docker/demo/docker-compose.yml run --rm demo

# Preflight checks
docker compose -f docker/demo/docker-compose.yml run --rm demo preflight
```

Standalone `docker run` (after build):

```bash
docker run --rm ralfuzz-demo:latest
docker run --rm ralfuzz-demo:latest preflight
```

## What the default demo does

1. Loads frozen metrics from `repro_artifacts/illustrative/` and checks paper numbers:
   - 6/6 Stage~1 valid seeds
   - 30 accepted Stage~2 harnesses, 8 behavior signatures
2. Compiles and runs `golden_harness.c` and `no_risk_harness.c` against pinned cJSON v1.7.19 with ASan+UBSan.

## Optional live rerun (your API keys at runtime only)

Live LLM reruns are **not bit-for-bit deterministic**. Paper numbers should be tied to the frozen snapshot, not a fresh live run.

```bash
# Option A: pass keys on the command line (recommended)
docker run --rm \
  -e SEED_API_KEY="$SEED_API_KEY" \
  -e MUTATION_API_KEY="$MUTATION_API_KEY" \
  ralfuzz-demo:latest run-live

# Option B: local env file (never commit)
cp docker/demo/env.example docker/demo/.env.llm
# edit docker/demo/.env.llm
docker compose -f docker/demo/docker-compose.yml --env-file docker/demo/.env.llm run --rm demo run-live
```

## Image contents (minimal)

| Included | Excluded |
|----------|----------|
| `mutation/`, `seed_generation/` | `runtime_data/` experiment outputs |
| `pipeline/run_full_pipeline.{py,sh}`, `fetch_cjson.sh` | Batch scripts (`run_baseline_*`, `run_downstream_*`, …) |
| `repro_artifacts/illustrative/` | `docker/compare/` (OSS-Fuzz-Gen baseline env) |
| cJSON v1.7.19 (baked at build) | `api/libpng_core`, `api/zlib_core`, `api/libucl_core` |
| Clang 18, Python 3.11, slim deps | `torch` / `transformers` (remote LLM mode only) |

## Publishing

After validation, tag and push for Metadata table C3, e.g.:

```bash
docker tag ralfuzz-demo:latest ghcr.io/go-ther/ralfuzz-demo:v0.1.1
docker push ghcr.io/go-ther/ralfuzz-demo:v0.1.1
```

## Related

- Host workflow and frozen snapshot details: [README.md](../../README.md)
- OSS-Fuzz-Gen comparison experiments: [docker/compare/README.md](../compare/README.md)
