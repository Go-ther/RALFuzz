# RALFuzz

RALFuzz is an execution- and risk-aware workflow for generating and mutating
fuzz harnesses for native C library APIs. It has two stages:

1. Seed generation: build a C harness prompt from API signatures, source-level
   execution context, cleanup hints, and risk hints, then validate generated C
   programs with a real compiler.
2. Evolutionary mutation: mutate validated seed harnesses, ask an LLM to fill
   or rewrite the changed regions, and validate the resulting candidates.

Frozen illustrative artifacts are stored under `repro_artifacts/`. Live runs
write local outputs under `runtime_data/`.

## Status

This repository is a heavily modified derivative of the TitanFuzz artifact
released on Zenodo:

https://zenodo.org/records/7978832

RALFuzz is an independent research prototype. It is not an official extension
of, nor affiliated with, the original TitanFuzz project or its authors. See
`NOTICE` for attribution details and `LICENSE` for the applicable license.

## Relationship to TitanFuzz

RALFuzz keeps TitanFuzz's broad two-stage idea, LLM-generated seeds followed by
LLM-assisted mutation, but retargets the workflow from Python deep-learning API
programs to native C library harnesses. Major changes include:

- C-library API discovery from headers
- execution-context extraction from C source files
- risk-card construction for target APIs
- C11 harness generation
- compile-and-run validation
- target-API reachability and cleanup-path checks
- optional sanitizer validation
- behaviour-signature and risk-aware mutation feedback
- structured seed-bank, mutation, and snapshot artifacts

## Repository Layout

```text
api/                    Local target-library input directory
mutation/               Mutation-stage package and validation logic
seed_generation/        Seed-generation pipeline
pipeline/               End-to-end launchers and artifact export helpers
pipeline/fetch_cjson.*  Fetch the external cJSON example input on demand
repro_artifacts/        Frozen illustrative snapshots and example harnesses
runtime_data/           Local run outputs and caches
mutation/toolchain_env.py
                        Clang/LLVM PATH and sanitizer-runtime helper
```

`runtime_data/` is local run state. It can be deleted and regenerated.
`repro_artifacts/` is the release-oriented snapshot area.
Third-party target libraries are not bundled as part of the RALFuzz software
distribution; `api/cJSON/` is created locally by the fetch scripts when users
want to run the illustrative cJSON workflow.

## Requirements

### Python

Use Python 3.10 or newer. Python 3.10-3.12 is the recommended range for the
published artifact because optional ML dependencies may lag the newest Python
release.

```bash
pip install -r requirements.txt
```

The requirements file includes all Python dependencies used by the packaged
remote and local workflows.

### Clang/LLVM

RALFuzz currently standardizes on Clang for preprocessing, compiling, and
validation.

Required tools:

- `clang`
- `llvm-cov`, needed for coverage-guided mutation modes

Check the toolchain:

```bash
clang --version
llvm-cov --version
```

On Windows, installing LLVM to the default path
`C:\Program Files\LLVM\bin` is supported. If Clang is installed elsewhere, put
its `bin` directory on `PATH` or pass `--compiler /path/to/clang`.

When sanitizer mode is enabled, the LLVM sanitizer runtime must also be
installed. On Windows, RALFuzz tries to add the matching LLVM runtime directory
to `PATH` automatically.

## LLM Configuration

RALFuzz does not require DeepSeek or Qwen specifically. Both stages can be
driven by remote models that expose an OpenAI-compatible HTTP API and can
follow the project's output constraints.

The bundled illustrative snapshot uses the following combination because it
produced the most stable outputs in this repository:

- Stage 1 seed generation: `deepseek-v4-flash`
- Stage 2 mutation: `qwen3-coder-next`

Other models behind OpenAI-compatible APIs may still work, but in our runs some
alternatives were noticeably less stable: Stage~1 could produce malformed C
harnesses that fell into `syntax_fix_failed`, and Stage~2 could return
responses that drifted from the expected JSON/list format. If you want to
reproduce the frozen snapshot or start from a known-good baseline, keep the
default model pair.

Set the keys before running the default launchers. The generic environment
variables are the most portable choice when moving between providers.

PowerShell:

```powershell
$env:SEED_API_KEY="<your-seed-provider-key>"
$env:MUTATION_API_KEY="<your-mutation-provider-key>"
```

Bash:

```bash
export SEED_API_KEY="<your-seed-provider-key>"
export MUTATION_API_KEY="<your-mutation-provider-key>"
```

Recognized environment variables:

- Seed stage: `SEED_API_KEY`, then `DEEPSEEK_API_KEY`, then `OPENAI_API_KEY`
- Mutation stage: `MUTATION_API_KEY`, then `DASHSCOPE_API_KEY`, then
  `QWEN_API_KEY`, then `LLM_API_KEY`

The seed stage supports `chat`, `completion`, `auto`, and `ollama` endpoint
modes. The mutation stage supports `openai_compatible`, `deepseek`, `mock`,
and `local_hf` providers.

Provider and model selection can be overridden through the launchers with:

- `SEED_BASE_URL`, `SEED_MODEL`, `SEED_ENDPOINT_MODE`
- `MUTATION_PROVIDER`, `MUTATION_BASE_URL`, `MUTATION_MODEL`

## Reviewer Quick Start

The fastest non-network check is to inspect the frozen illustrative artifacts
without rerunning the LLMs:

PowerShell:

```powershell
Get-Content .\repro_artifacts\illustrative\manifest.md
python -m json.tool .\repro_artifacts\illustrative\stage1_seed_summary.json > $null
python -m json.tool .\repro_artifacts\illustrative\stage2_mutation_snapshot.json > $null
```

Bash:

```bash
sed -n '1,40p' repro_artifacts/illustrative/manifest.md
python -m json.tool repro_artifacts/illustrative/stage1_seed_summary.json >/dev/null
python -m json.tool repro_artifacts/illustrative/stage2_mutation_snapshot.json >/dev/null
```

To validate the default launcher wiring, first fetch the pinned external cJSON
target input. The fetch scripts download cJSON `v1.7.19` from the upstream
MIT-licensed cJSON repository and place it under `api/cJSON/`, which is ignored
by git.

PowerShell:

```powershell
.\pipeline\fetch_cjson.ps1
$env:SEED_API_KEY="dummy"
$env:MUTATION_API_KEY="dummy"
.\pipeline\run_full_pipeline.ps1 -Mode full -DryRun -DisableSanitizer
```

Bash:

```bash
bash pipeline/fetch_cjson.sh
SEED_API_KEY=dummy MUTATION_API_KEY=dummy ENABLE_SANITIZER=0 DRY_RUN=1 \
  bash pipeline/run_full_pipeline.sh --mode full
```

For a live rerun, set real `SEED_API_KEY` and `MUTATION_API_KEY`, confirm
Clang/LLVM is installed, and run the full pipeline command in the next section.
Live reruns are stochastic and may differ from the frozen snapshot.

## Quick Start

The wrapper scripts default to an illustrative cJSON target:

- target library: `api/cJSON`, fetched locally from the upstream cJSON release
- target API: `cJSON_Parse`
- runtime root: `runtime_data/illustrative_cjson_parse_v1`
- seed model: `deepseek-v4-flash`
- mutation model: `qwen3-coder-next`
- mutation accepted-target limit: `30`
- mutation batch size: `4`
- sanitizer mode: enabled by the wrapper scripts

These defaults are meant to reproduce the bundled illustrative snapshot rather
than to constrain the framework to specific vendors.

Fetch the external cJSON target input once before running the default workflow:

PowerShell:

```powershell
.\pipeline\fetch_cjson.ps1
```

Bash:

```bash
bash pipeline/fetch_cjson.sh
```

### PowerShell

Run the full illustrative workflow:

```powershell
.\pipeline\run_full_pipeline.ps1 -Mode full
```

Run only seed generation:

```powershell
.\pipeline\run_seed_generation.ps1
```

Run only mutation after seed generation has produced validated seeds:

```powershell
.\pipeline\run_mutation.ps1
```

Resume without clearing existing stage outputs:

```powershell
.\pipeline\run_full_pipeline.ps1 -Mode full -Resume
```

Disable sanitizer in the PowerShell wrapper:

```powershell
.\pipeline\run_full_pipeline.ps1 -Mode full -DisableSanitizer
```

### Bash

Run the full illustrative workflow:

```bash
bash pipeline/run_full_pipeline.sh --mode full
```

Run only seed generation:

```bash
bash pipeline/run_seed_generation.sh
```

Run only mutation:

```bash
bash pipeline/run_mutation.sh
```

Disable sanitizer in the Bash wrapper:

```bash
ENABLE_SANITIZER=0 bash pipeline/run_full_pipeline.sh --mode full
```

### Dry Run

To print the commands without executing LLM calls or compilation, use the
Python entry point directly after fetching `api/cJSON/`:

```bash
python pipeline/run_full_pipeline.py \
  --mode full \
  --api-dir api/cJSON \
  --runtime-root runtime_data/illustrative_cjson_parse_v1 \
  --api cJSON_Parse \
  --api-name-regex "^cJSON_Parse$" \
  --seed-base-url https://api.deepseek.com \
  --seed-model deepseek-v4-flash \
  --seed-endpoint-mode chat \
  --seed-samples-per-api 8 \
  --seed-target-valid-per-api 6 \
  --mutation-llm-provider openai_compatible \
  --mutation-model qwen3-coder-next \
  --mutation-api-base https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --mutation-max-valid 30 \
  --mutation-batch-size 4 \
  --enable-sanitizer \
  --dry-run
```

## Running Another C Library

For a different target library, provide the library directory, target API, and
API-name filter.

PowerShell:

```powershell
.\pipeline\run_full_pipeline.ps1 `
  -Mode full `
  -ApiDir "C:\path\to\library" `
  -RuntimeRoot ".\runtime_data\my_library_run" `
  -TestApi "target_function" `
  -ApiNameRegex "^target_function$"
```

Bash:

```bash
API_DIR=/path/to/library \
RUNTIME_ROOT=runtime_data/my_library_run \
TEST_API=target_function \
API_NAME_REGEX='^target_function$' \
bash pipeline/run_full_pipeline.sh --mode full
```

Useful overrides:

- `-SeedModel` / `SEED_MODEL`
- `-SeedBaseUrl` / `SEED_BASE_URL`
- `-MutationModel` / `MUTATION_MODEL`
- `-MutationBaseUrl` / `MUTATION_BASE_URL`
- `-MutationMaxValid` / `MUTATION_MAX_VALID`
- `-MutationBatchSize` / `MUTATION_BATCH_SIZE`
- `-Compiler` / `COMPILER`
- `-CoverageTool` / `COVERAGE_TOOL`
- `-DisableSanitizer` / `ENABLE_SANITIZER=0`

If the library needs extra include or link flags, use the Python entry point
with `--extra-cflags` and `--extra-ldflags`.

## Direct Stage Commands

Seed generation can also be run directly after fetching `api/cJSON/`:

```bash
python seed_generation/generate_c_seeds.py \
  --auto-api-dir api/cJSON \
  --auto-style auto \
  --auto-api-name-regex "^cJSON_Parse$" \
  --output-dir runtime_data/illustrative_cjson_parse_v1/seed_generation \
  --base-url https://api.deepseek.com \
  --api-key "$SEED_API_KEY" \
  --model deepseek-v4-flash \
  --endpoint-mode chat \
  --samples-per-api 8 \
  --target-valid-per-api 6 \
  --source-dir api/cJSON \
  --source-file-pattern "*.c" \
  --cflags="-Iapi/cJSON -fsanitize=address -fsanitize=undefined" \
  --ldflags="api/cJSON/cJSON.c api/cJSON/cJSON_Utils.c -fsanitize=address -fsanitize=undefined" \
  --risk-card \
  --overwrite-existing
```

Mutation can be run directly after validated seeds exist:

```bash
python -m mutation.ev_generation \
  --llm_provider openai_compatible \
  --model_name qwen3-coder-next \
  --llm_api_base https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm_api_key "$MUTATION_API_KEY" \
  --target generic \
  --target_root api/cJSON \
  --seedfolder runtime_data/illustrative_cjson_parse_v1/seed_generation/fix \
  --api cJSON_Parse \
  --folder runtime_data/illustrative_cjson_parse_v1/mutation \
  --mutator_selection_algo ts \
  --seed_selection_algo fitness \
  --batch_size 4 \
  --max_valid 30 \
  --coverage-tool "llvm-cov gcov" \
  --enable-sanitizer
```

To run the direct stage commands without sanitizer instrumentation, remove the
`-fsanitize=...` flags from seed generation and omit `--enable-sanitizer` from
mutation.

## Outputs

For the default illustrative run, outputs are written under:

```text
runtime_data/illustrative_cjson_parse_v1/
  seed_generation/
    raw/                Prompt/completion attempts
    fix/                Validated seed harnesses
    outputs.json        Per-API seed-generation records
    seed_bank.json      Validated seed bank
    summary.json        Aggregate seed-generation summary
  mutation/
    seed/               Seeds imported into mutation
    valid/              Accepted mutated harnesses
    exception/          Candidates rejected by exception classification
    crash/ hangs/ notarget/
    outputs.json        Seed and accepted-variant metadata
    generation.log      Mutation-loop log and aggregate counts
  cache/
    metadata/ build/ temp/
```

The full pipeline clears selected stage directories unless `--resume` or
`-Resume` is used.

## Reproducibility

RALFuzz is designed for artifact-level reproducibility and workflow-level
reruns. The frozen files under `repro_artifacts/illustrative/` summarize the
illustrative `cJSON_Parse` run without requiring users to re-query the original
LLM endpoints.

To refresh the illustrative release snapshot from a local runtime directory:

```bash
python pipeline/export_illustrative_assets.py \
  --runtime-root runtime_data/illustrative_cjson_parse_v1 \
  --api cJSON_Parse \
  --output-dir repro_artifacts/illustrative
```

Live LLM reruns are not guaranteed to be bit-for-bit deterministic. Outputs can
change with model versions, endpoint behavior, sampling, network retries, and
toolchain versions. For this reason, paper numbers should be tied to the frozen
snapshot artifacts rather than to a fresh live run.

## Troubleshooting

- `clang was not found`: install LLVM/Clang, add it to `PATH`, or pass
  `--compiler /path/to/clang`.
- Windows sanitizer runtime error: install the LLVM runtime files, rerun
  PowerShell with `-DisableSanitizer`, or rerun Bash with `ENABLE_SANITIZER=0`.
- Missing API key: set `SEED_API_KEY` for seed generation and
  `MUTATION_API_KEY` for mutation. Service-specific aliases such as
  `DEEPSEEK_API_KEY`, `DASHSCOPE_API_KEY`, `QWEN_API_KEY`, and `LLM_API_KEY`
  are also accepted.
- Empty mutation output: make sure seed generation produced `.c` files under
  `seed_generation/fix/<API>/`, and run mutation with the matching `--api`.
- Different live results: this is expected with remote LLMs; compare against
  `repro_artifacts/` for frozen evidence.

## Authors

- Jinyu Zhou
- Zitong Zhu
- Yuqi Xiong
- Zhijie Li
- Jianxi Wu

## Acknowledgements

RALFuzz is a heavily modified derivative of the TitanFuzz artifact. We
gratefully acknowledge Yinlin Deng, Chunqiu Steven Xia, Haoran Peng, Chenyuan
Yang, and Lingming Zhang for the original TitanFuzz paper and public artifact.
Their Zenodo artifact is available at https://zenodo.org/records/7978832. See
`NOTICE` for detailed attribution and licensing information.

## License

The RALFuzz contributors' original software contributions are released under
the MIT License. See `LICENSE`.

Because this project is a modified derivative of the upstream TitanFuzz
artifact, redistribution should preserve attribution to both the original
TitanFuzz artifact and this RALFuzz repository. The upstream TitanFuzz artifact
was released under CC BY 4.0; see `NOTICE` for upstream attribution details.

## Citation

If you use this repository, please cite both the original TitanFuzz work and
this RALFuzz repository or accompanying software article.

```bibtex
@inproceedings{deng2023titanfuzz,
  title = {Large Language Models Are Zero-Shot Fuzzers: Fuzzing Deep-Learning Libraries via Large Language Models},
  author = {Deng, Yinlin and Xia, Chunqiu Steven and Peng, Haoran and Yang, Chenyuan and Zhang, Lingming},
  booktitle = {Proceedings of the 32nd ACM SIGSOFT International Symposium on Software Testing and Analysis},
  year = {2023}
}
```

The RALFuzz citation and software DOI will be added after archival.
