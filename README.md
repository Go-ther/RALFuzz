# CTitanFuzz

CTitanFuzz is a C-library-oriented fuzzing workflow that combines two stages:

1. LLM-assisted seed generation for target C APIs
2. Mutation-based fuzzing over the validated seed corpus

This repository also includes frozen reproducibility artifacts under
`repro_artifacts/`.

## Status

This repository is a heavily modified derivative of the TitanFuzz artifact
released on Zenodo. CTitanFuzz is an independent research prototype and is not
an official extension of, nor affiliated with, the original TitanFuzz project
or its authors. See `NOTICE` for attribution details and `LICENSE` for the
applicable license.

Upstream artifact:
https://zenodo.org/records/7978832

## Relationship to TitanFuzz

CTitanFuzz builds on the TitanFuzz artifact but targets a different setting:
native C library API fuzzing. Major modifications include:

- C-library-oriented API discovery and target selection
- execution-context extraction from C headers and source files
- risk-card construction for target APIs
- LLM-assisted C11 harness generation
- compile-and-run validation for generated harnesses
- optional sanitizer and coverage-oriented validation hooks
- structured seed-bank and mutation artifact management
- frozen reproducibility artifacts for the illustrative `cJSON_Parse` run

## Repository Layout

```text
ctitanfuzz/             Namespace package entry point
mutation/               Mutation-stage implementation
seed_generation/        Seed-generation pipeline
pipeline/               End-to-end launch scripts
repro_artifacts/        Frozen reproducibility artifacts intended for release
runtime_data/           Local runtime outputs and caches (ignored by git)
```

## Requirements

### Python

- Python 3.10 or newer
- Python packages listed in `requirements.txt`

Install Python dependencies with:

```bash
pip install -r requirements.txt
```

### System tools

- `clang` available on `PATH` for seed-generation preprocessing
- `gcc` available on `PATH` by default for compilation and validation
- `gcov` available on `PATH` for coverage-guided mutation runs
- A shell environment for the helper scripts
  - PowerShell on Windows
  - `bash` on Linux or macOS

The current seed-generation pipeline uses `clang -E` internally when extracting
execution context from project source files. The mutation stage defaults to
`gcc` and `gcov`.

### LLM backends

The pipeline expects accessible LLM endpoints for generation and mutation unless
you explicitly use the mock mutation backend.

Examples supported by the current code:

- Ollama-compatible seed generation endpoint
- OpenAI-compatible mutation endpoint
- DeepSeek-compatible mutation endpoint
- Local Hugging Face mutation backend

## LLM Configuration

The seed-generation and mutation stages require accessible LLM backends unless
the mock mutation backend is used for testing. The launcher scripts expose the
backend settings as command-line parameters.

Seed-generation settings:

- `--seed-base-url`
- `--seed-api-key`, defaulting to `OPENAI_API_KEY` when available
- `--seed-model`
- `--seed-endpoint-mode`

Mutation settings:

- `--mutation-llm-provider`
- `--mutation-api-base`
- `--mutation-api-key`
- `--mutation-model`

For OpenAI-compatible mutation backends, the code also accepts `LLM_API_KEY`
when `--mutation-api-key` is not provided. For Ollama-style local endpoints,
make sure the server is running and set the matching base URL, model, and
endpoint mode.

## Quick Start

### PowerShell

Run the end-to-end pipeline on an explicit target library directory:

```powershell
.\pipeline\run_full_pipeline.ps1 `
  -Mode full `
  -ApiDir <path-to-target-library> `
  -RuntimeRoot .\runtime_data `
  -TestApi <target-api-name> `
  -ApiNameRegex "^<target-api-name>$"
```

Run only seed generation:

```powershell
.\pipeline\run_seed_generation.ps1 `
  -ApiDir <path-to-target-library> `
  -RuntimeRoot .\runtime_data `
  -TestApi <target-api-name>
```

Run only mutation, assuming validated seeds already exist under
`runtime_data/seed_generation/fix/`:

```powershell
.\pipeline\run_mutation.ps1 `
  -ApiDir <path-to-target-library> `
  -RuntimeRoot .\runtime_data `
  -TestApi <target-api-name>
```

### Bash

```bash
API_DIR=<path-to-target-library> \
bash pipeline/run_full_pipeline.sh \
  --mode full \
  --api <target-api-name>
```

## Main Outputs

By default, runtime outputs are written under `runtime_data/`:

- `runtime_data/seed_generation/`
- `runtime_data/mutation/`
- `runtime_data/cache/`

These directories are treated as local runtime artifacts and are not intended
for version control.

Frozen, release-oriented materials live under `repro_artifacts/`.

## Reproducibility Artifacts

`repro_artifacts/` contains frozen snapshots for the illustrative
`cJSON_Parse` run used during artifact preparation. These artifacts include
generated prompts and harness examples, seed-bank data, stage summaries, and a
mutation snapshot.

The frozen artifacts are intended for inspection and illustrative result
regeneration. They do not require re-querying the original LLM backend.
Re-running live LLM generation may produce different candidates because
external model endpoints, sampling behavior, and model versions can change.

## Notes

- The package entry point `ctitanfuzz/` maps into the source tree under
  `mutation/`. This is intentional for the current repository layout.
- The pipeline scripts require an explicit target library directory via
  `-ApiDir`, `API_DIR`, or `--api-dir`.
- The full pipeline resets selected `runtime_data/` subdirectories unless
  `--resume` is used.

## License

This repository is distributed under CC BY 4.0, following the license of the
upstream TitanFuzz artifact. See `LICENSE`.

Because this project is a modified derivative of the upstream
TitanFuzz artifact, redistribution should preserve attribution to both the
original TitanFuzz artifact and this CTitanFuzz repository.

## Citation

If you use this repository, please cite both the original TitanFuzz work and
this CTitanFuzz repository or accompanying software article.

```bibtex
@inproceedings{deng2023titanfuzz,
  title = {Large Language Models Are Zero-Shot Fuzzers: Fuzzing Deep-Learning Libraries via Large Language Models},
  author = {Deng, Yinlin and Xia, Chunqiu Steven and Peng, Haoran and Yang, Chenyuan and Zhang, Lingming},
  booktitle = {Proceedings of the 32nd ACM SIGSOFT International Symposium on Software Testing and Analysis},
  year = {2023}
}
```

The CTitanFuzz citation and software DOI will be added after archival.
