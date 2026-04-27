# CTitanFuzz

CTitanFuzz is a C-library-oriented fuzzing workflow that combines two stages:

1. LLM-assisted seed generation for target C APIs
2. Mutation-based fuzzing over the validated seed corpus

This repository also includes frozen reproducibility artifacts under
`repro_artifacts/`.

## Status

This repository is a heavily modified derivative of the TitanFuzz artifact
released on Zenodo. See `NOTICE` for attribution details and `LICENSE` for the
applicable license.

Upstream artifact:
https://zenodo.org/records/7978832

## Repository Layout

```text
ctitanfuzz/             Namespace package entry point
mutation/               Mutation-stage implementation and bundled sample target
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

`repro_artifacts/` contains frozen snapshots prepared for inspection and
release. These files are meant to be stable reference artifacts rather than
working runtime state.

## Notes

- The package entry point `ctitanfuzz/` maps into the source tree under
  `mutation/`. This is intentional for the current repository layout.
- The pipeline scripts require an explicit target library directory via
  `-ApiDir`, `API_DIR`, or `--api-dir`.
- The full pipeline resets selected `runtime_data/` subdirectories unless
  `--resume` is used.

## License

This repository is distributed under CC BY 4.0. See `LICENSE`.

Because this project is a modified derivative of the upstream
TitanFuzz artifact, please preserve attribution to both the original TitanFuzz
artifact and this CTitanFuzz repository when redistributing derived work.
