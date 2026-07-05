#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mutation.toolchain_env import configure_clang_environment, default_coverage_tool, normalize_clang_compiler


EXCLUDED_DIR_PARTS = {
    ".git",
    ".svn",
    "__pycache__",
    "build",
    "cmake-build-debug",
    "cmake-build-release",
    "coverage",
    "doc",
    "docs",
    "example",
    "examples",
    "fuzz",
    "fuzzing",
    "sample",
    "samples",
    "test",
    "tests",
    "testing",
    "third_party",
    "vendor",
}

EXCLUDED_FILE_PATTERNS = (
    re.compile(r"(^|[_-])(test|tests|example|examples|demo|benchmark|bench|fuzz)($|[_-])", re.I),
)


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parent.parent
    default_runtime_root = repo_root / "runtime_data"
    parser = argparse.ArgumentParser(
        description="Run the end-to-end RALFuzz pipeline: seed generation followed by mutation fuzzing."
    )
    parser.add_argument("--runtime-root", default=str(default_runtime_root))
    parser.add_argument("--api-dir", required=True, help="Single target C library directory used by both stages.")
    parser.add_argument("--mode", choices=["full", "seed", "mutation"], default="full")
    parser.add_argument("--resume", action="store_true", default=False)
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--skip-seed-generation", action="store_true", default=False)
    parser.add_argument("--skip-mutation", action="store_true", default=False)
    parser.add_argument("--api", default="all", help="Target API for mutation stage. Use 'all' to fuzz every discovered API.")
    parser.add_argument("--api-name-regex", default=None, help="Optional regex for seed-generation API filtering.")
    parser.add_argument("--auto-style", choices=["auto", "cjson", "generic"], default="auto")
    parser.add_argument("--library-name", default=None)
    parser.add_argument("--library-version", default="unknown")
    parser.add_argument("--compiler", default="clang")
    parser.add_argument("--coverage-tool", default=default_coverage_tool())
    parser.add_argument("--extra-cflags", default="")
    parser.add_argument("--extra-ldflags", default="")
    parser.add_argument("--enable-sanitizer", action="store_true", default=False)
    parser.add_argument("--random-seed", type=int, default=420)

    parser.add_argument("--seed-base-url", default="http://localhost:11434")
    parser.add_argument("--seed-api-key", default=os.environ.get("OPENAI_API_KEY", "ollama"))
    parser.add_argument("--seed-model", default="deepseek-v3.2:cloud")
    parser.add_argument("--seed-endpoint-mode", choices=["auto", "chat", "completion", "ollama"], default="ollama")
    parser.add_argument("--seed-request-timeout", type=int, default=120)
    parser.add_argument("--seed-network-retries", type=int, default=2)
    parser.add_argument("--seed-network-retry-backoff-sec", type=float, default=2.0)
    parser.add_argument("--seed-samples-per-api", type=int, default=12)
    parser.add_argument("--seed-target-valid-per-api", type=int, default=8)
    parser.add_argument("--seed-temperature", type=float, default=0.35)
    parser.add_argument("--seed-top-p", type=float, default=0.90)
    parser.add_argument("--seed-max-tokens", type=int, default=2048)
    parser.add_argument("--seed-single-shot-delay-ms", type=int, default=0)
    parser.add_argument("--seed-compile-timeout", type=int, default=15)
    parser.add_argument("--seed-run-timeout", type=int, default=3)
    parser.add_argument("--seed-max-per-skeleton", type=int, default=2)
    parser.add_argument("--seed-risk-relevance-policy", choices=["off", "auto", "strict"], default="auto")
    parser.add_argument("--seed-risk-card", dest="seed_risk_card", action="store_true")
    parser.add_argument("--no-seed-risk-card", dest="seed_risk_card", action="store_false")
    parser.set_defaults(seed_risk_card=True)
    parser.add_argument("--seed-risk-min-marker-kinds", type=int, default=1)
    parser.add_argument("--seed-risk-boost-retries", type=int, default=0)
    parser.add_argument("--seed-risk-boost-temperature", type=float, default=0.72)
    parser.add_argument("--seed-risk-boost-top-p", type=float, default=0.98)
    parser.add_argument("--seed-retry-on-truncation", type=int, default=0)
    parser.add_argument("--seed-truncation-retry-max-tokens", type=int, default=1024)
    parser.add_argument("--seed-truncation-retry-temperature", type=float, default=0.35)
    parser.add_argument("--seed-truncation-retry-top-p", type=float, default=0.9)
    parser.add_argument("--seed-truncation-retry-max-lines", type=int, default=96)
    parser.add_argument("--seed-truncation-retry-min-marker-kinds", type=int, default=1)
    parser.add_argument("--seed-enforce-init-target-order", action="store_true", default=False)
    parser.add_argument("--seed-signature-only-prompt", action="store_true", default=False)
    parser.add_argument("--seed-no-execution-context", action="store_true", default=False)
    parser.add_argument("--seed-api-spec-file", default=None)
    parser.add_argument("--seed-risk-cards-file", default=None)

    parser.add_argument("--mutation-signature-only-prompt", action="store_true", default=False)
    parser.add_argument("--mutation-no-execution-context", action="store_true", default=False)
    parser.add_argument("--mutation-no-risk-context", action="store_true", default=False)

    parser.add_argument(
        "--mutation-llm-provider",
        choices=["mock", "local_hf", "deepseek", "openai_compatible"],
        default="openai_compatible",
    )
    parser.add_argument("--mutation-model", default="qwen3-coder:480b-cloud")
    parser.add_argument("--mutation-api-base", default=None)
    parser.add_argument("--mutation-api-key", default=None)
    parser.add_argument("--mutation-request-timeout", type=int, default=60)
    parser.add_argument("--mutation-max-tokens", type=int, default=512)
    parser.add_argument("--mutation-temperature", type=float, default=0.8)
    parser.add_argument("--mutation-max-valid", type=int, default=50)
    parser.add_argument("--mutation-batch-size", type=int, default=4)
    parser.add_argument("--mutation-num-selection", type=int, default=1)
    parser.add_argument("--mutation-timeout", type=int, default=1200)
    parser.add_argument("--mutation-seed-pool-size", type=int, default=20)
    parser.add_argument("--mutation-seed-selection-algo", choices=["fitness", "random", "coverage"], default="fitness")
    parser.add_argument(
        "--mutation-mutator-selection-algo",
        choices=["heuristic", "epsgreedy", "ucb", "random", "ts"],
        default="ts",
    )
    parser.add_argument("--mutation-compile-timeout", type=int, default=20)
    parser.add_argument("--mutation-test-timeout", type=int, default=10)
    parser.add_argument("--mutation-max-stagnation-rounds", type=int, default=25)
    parser.add_argument("--mutation-max-rounds", type=int, default=0, help="Stop Stage 2 after this many mutation rounds (0 disables).")
    parser.add_argument(
        "--emit-fuzz-targets",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="After mutation, emit libFuzzer / AFL++ fuzz harnesses under mutation/fuzz/",
    )
    parser.add_argument(
        "--fuzz-backend",
        choices=["libfuzzer", "afl", "both"],
        default="both",
        help="Fuzz backends to emit when --emit-fuzz-targets is enabled",
    )
    parser.add_argument("--fuzz-per-harness", action="store_true", default=False)
    return parser.parse_args()


def quote_flag_token(path: Path | str) -> str:
    text = str(path)
    escaped = text.replace('"', '\\"')
    return f'"{escaped}"'


def join_flag_tokens(tokens: list[str]) -> str:
    return " ".join(token for token in tokens if token)


def seed_folder_has_inputs(seed_fix_dir: Path, api: str) -> bool:
    if not seed_fix_dir.exists():
        return False
    if api == "all":
        return any(path.is_file() and path.suffix == ".c" for path in seed_fix_dir.rglob("*.c"))
    api_dir = seed_fix_dir / api
    return api_dir.exists() and any(path.is_file() and path.suffix == ".c" for path in api_dir.glob("*.c"))


def count_seed_inputs(seed_fix_dir: Path, api: str) -> int:
    if not seed_fix_dir.exists():
        return 0
    if api == "all":
        return sum(1 for path in seed_fix_dir.rglob("*.c") if path.is_file())
    api_dir = seed_fix_dir / api
    if not api_dir.exists():
        return 0
    return sum(1 for path in api_dir.glob("*.c") if path.is_file())


def file_is_excluded(path: Path, base_dir: Path) -> bool:
    rel = path.relative_to(base_dir)
    if any(part.lower() in EXCLUDED_DIR_PARTS for part in rel.parts[:-1]):
        return True
    return any(pattern.search(path.stem.lower()) for pattern in EXCLUDED_FILE_PATTERNS)


def discover_include_dirs(api_dir: Path) -> list[Path]:
    include_dirs: list[Path] = [api_dir.resolve()]
    seen = {include_dirs[0]}
    for header in sorted(api_dir.rglob("*.h")):
        if not header.is_file() or file_is_excluded(header, api_dir):
            continue
        parent = header.parent.resolve()
        if parent not in seen:
            seen.add(parent)
            include_dirs.append(parent)
    return include_dirs


def discover_link_sources(api_dir: Path) -> list[Path]:
    sources: list[Path] = []
    for source in sorted(api_dir.rglob("*.c")):
        if not source.is_file() or file_is_excluded(source, api_dir):
            continue
        sources.append(source.resolve())
    return sources


def reset_runtime_dirs(runtime_root: Path, names: tuple[str, ...]) -> None:
    for name in names:
        target = runtime_root / name
        if target.exists():
            import shutil

            shutil.rmtree(target, ignore_errors=True)


def build_env(
    repo_root: Path,
    runtime_root: Path,
    *,
    create_dirs: bool = True,
    compiler: str | None = None,
    enable_sanitizer: bool = False,
    require_clang: bool = True,
) -> dict[str, str]:
    env = os.environ.copy()
    pythonpath_entries = [str(repo_root)]
    if env.get("PYTHONPATH"):
        pythonpath_entries.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)

    cache_root = runtime_root / "cache"
    metadata_root = cache_root / "metadata"
    build_root = cache_root / "build"
    temp_root = cache_root / "temp"
    if create_dirs:
        metadata_root.mkdir(parents=True, exist_ok=True)
        build_root.mkdir(parents=True, exist_ok=True)
        temp_root.mkdir(parents=True, exist_ok=True)

    env["RALFUZZ_CACHE_ROOT"] = str(metadata_root)
    env["RALFUZZ_BUILD_ROOT"] = str(build_root)
    env["TMP"] = str(temp_root)
    env["TEMP"] = str(temp_root)
    env["TMPDIR"] = str(temp_root)
    configure_clang_environment(
        compiler=compiler,
        enable_sanitizer=enable_sanitizer,
        env=env,
        require_clang=require_clang,
    )
    return env


SECRET_FLAGS = {
    "--api-key",
    "--llm_api_key",
    "--seed-api-key",
    "--mutation-api-key",
}


def redact_command(cmd: list[str]) -> list[str]:
    redacted = list(cmd)
    for index, token in enumerate(redacted[:-1]):
        if token in SECRET_FLAGS:
            redacted[index + 1] = "<redacted>"
    for index, token in enumerate(redacted):
        for flag in SECRET_FLAGS:
            prefix = f"{flag}="
            if token.startswith(prefix):
                redacted[index] = f"{prefix}<redacted>"
    return redacted


def print_command(label: str, cmd: list[str], cwd: Path) -> None:
    rendered = subprocess.list2cmdline(redact_command(cmd))
    print(f"[{label}] cwd={cwd}")
    print(f"[{label}] cmd={rendered}")


def run_command(label: str, cmd: list[str], cwd: Path, env: dict[str, str], dry_run: bool) -> None:
    print_command(label, cmd, cwd)
    if dry_run:
        return
    subprocess.run(cmd, cwd=str(cwd), env=env, check=True)


def main() -> int:
    args = parse_args()
    args.compiler = normalize_clang_compiler(args.compiler, source="pipeline --compiler")
    repo_root = Path(__file__).resolve().parent.parent
    seed_generation_dir = repo_root / "seed_generation"
    api_dir = Path(args.api_dir).resolve()
    runtime_root = Path(args.runtime_root).resolve()
    seed_output_dir = runtime_root / "seed_generation"
    mutation_output_dir = runtime_root / "mutation"
    seed_fix_dir = seed_output_dir / "fix"
    run_seed_generation = args.mode in {"full", "seed"} and not args.skip_seed_generation
    run_mutation = args.mode in {"full", "mutation"} and not args.skip_mutation

    if not api_dir.exists():
        raise FileNotFoundError(f"api dir not found: {api_dir}")

    if not args.resume and not args.dry_run:
        reset_targets: list[str] = ["cache"]
        if run_seed_generation:
            reset_targets.append("seed_generation")
        if run_mutation:
            reset_targets.append("mutation")
        reset_runtime_dirs(runtime_root, tuple(reset_targets))

    env = build_env(
        repo_root,
        runtime_root,
        create_dirs=not args.dry_run,
        compiler=args.compiler,
        enable_sanitizer=args.enable_sanitizer,
        require_clang=not args.dry_run,
    )

    include_dirs = discover_include_dirs(api_dir)
    link_sources = discover_link_sources(api_dir)
    if not link_sources:
        raise RuntimeError(f"no linkable C sources discovered under {api_dir}")

    cflag_tokens = [f"-I{path}" for path in include_dirs]
    if args.extra_cflags.strip():
        cflag_tokens.append(args.extra_cflags.strip())
    ldflag_tokens = [str(path) for path in link_sources]
    if args.extra_ldflags.strip():
        ldflag_tokens.append(args.extra_ldflags.strip())
    if args.enable_sanitizer:
        sanitizer_flags = ["-fsanitize=address", "-fsanitize=undefined"]
        cflag_tokens.extend(sanitizer_flags)
        ldflag_tokens.extend(sanitizer_flags)
    cflags = join_flag_tokens(cflag_tokens)
    ldflags = join_flag_tokens(ldflag_tokens)

    library_name = args.library_name or api_dir.name
    mutation_api_base = args.mutation_api_base or args.seed_base_url
    mutation_api_key = args.mutation_api_key or args.seed_api_key

    if run_seed_generation:
        if not args.dry_run:
            seed_output_dir.mkdir(parents=True, exist_ok=True)
        seed_cmd = [
            sys.executable,
            "generate_c_seeds.py",
            "--auto-api-dir",
            str(api_dir),
            "--auto-style",
            args.auto_style,
            "--output-dir",
            str(seed_output_dir),
            "--base-url",
            args.seed_base_url,
            "--api-key",
            args.seed_api_key,
            "--model",
            args.seed_model,
            "--endpoint-mode",
            args.seed_endpoint_mode,
            "--request-timeout",
            str(args.seed_request_timeout),
            "--network-retries",
            str(args.seed_network_retries),
            "--network-retry-backoff-sec",
            str(args.seed_network_retry_backoff_sec),
            "--library-name",
            library_name,
            "--library-version",
            args.library_version,
            "--samples-per-api",
            str(args.seed_samples_per_api),
            "--target-valid-per-api",
            str(args.seed_target_valid_per_api),
            "--temperature",
            str(args.seed_temperature),
            "--top-p",
            str(args.seed_top_p),
            "--max-tokens",
            str(args.seed_max_tokens),
            "--single-shot-delay-ms",
            str(args.seed_single_shot_delay_ms),
            "--compiler",
            args.compiler,
            "--compile-timeout",
            str(args.seed_compile_timeout),
            "--run-timeout",
            str(args.seed_run_timeout),
            "--risk-relevance-policy",
            args.seed_risk_relevance_policy,
            "--risk-card" if args.seed_risk_card else "--no-risk-card",
            "--risk-prompt-hardening",
            "--risk-min-marker-kinds",
            str(args.seed_risk_min_marker_kinds),
            "--risk-require-boundary-value",
            "--no-risk-require-high-risk-neighbor",
            "--risk-boost-retries",
            str(args.seed_risk_boost_retries),
            "--risk-boost-temperature",
            str(args.seed_risk_boost_temperature),
            "--risk-boost-top-p",
            str(args.seed_risk_boost_top_p),
            "--retry-on-truncation",
            str(args.seed_retry_on_truncation),
            "--truncation-retry-max-tokens",
            str(args.seed_truncation_retry_max_tokens),
            "--truncation-retry-temperature",
            str(args.seed_truncation_retry_temperature),
            "--truncation-retry-top-p",
            str(args.seed_truncation_retry_top_p),
            "--truncation-retry-max-lines",
            str(args.seed_truncation_retry_max_lines),
            "--truncation-retry-min-marker-kinds",
            str(args.seed_truncation_retry_min_marker_kinds),
            "--truncation-retry-require-boundary-value",
            "--no-truncation-retry-require-high-risk-neighbor",
            "--max-per-skeleton",
            str(args.seed_max_per_skeleton),
            f"--cflags={cflags}",
            f"--ldflags={ldflags}",
            "--source-dir",
            str(api_dir),
            "--source-file-pattern",
            "*.c",
            "--overwrite-existing",
        ]
        if args.api_name_regex:
            seed_cmd.extend(["--auto-api-name-regex", args.api_name_regex])
        if args.seed_enforce_init_target_order:
            seed_cmd.append("--enforce-init-target-order")
        else:
            seed_cmd.append("--no-enforce-init-target-order")
        if args.seed_signature_only_prompt:
            seed_cmd.append("--signature-only-prompt")
        if args.seed_no_execution_context:
            seed_cmd.append("--no-execution-context")
        if args.seed_api_spec_file:
            seed_cmd.extend(["--api-spec-file", args.seed_api_spec_file])
        if args.seed_risk_cards_file:
            seed_cmd.extend(["--risk-cards-file", args.seed_risk_cards_file])
        run_command("seed-generation", seed_cmd, seed_generation_dir, env, args.dry_run)
        if not args.dry_run:
            valid_seed_count = count_seed_inputs(seed_fix_dir, args.api)
            print(f"[seed-generation] valid seed files for api={args.api!r}: {valid_seed_count}")
        if run_mutation and not args.dry_run and not seed_folder_has_inputs(seed_fix_dir, args.api):
            raise RuntimeError(
                "seed generation produced no valid seeds for api={!r} under {}. "
                "Check {}/summary.json and {}/outputs.json for validation reasons; mutation needs .c files under fix/.".format(
                    args.api,
                    seed_fix_dir,
                    seed_output_dir,
                    seed_output_dir,
                )
            )

    if run_mutation:
        if not args.dry_run and not seed_folder_has_inputs(seed_fix_dir, args.api):
            raise FileNotFoundError(
                "mutation mode requires existing valid seeds for api={!r} under {}. "
                "Run mode=seed/full first and make sure seed_generation/fix contains .c files.".format(
                    args.api,
                    seed_fix_dir
                )
            )
        if not args.dry_run:
            mutation_output_dir.mkdir(parents=True, exist_ok=True)
        mutation_cmd = [
            sys.executable,
            "-m",
            "mutation.ev_generation",
            "--llm_provider",
            args.mutation_llm_provider,
            "--target",
            "generic",
            "--target_root",
            str(api_dir),
            "--seedfolder",
            str(seed_fix_dir),
            "--api",
            args.api,
            "--folder",
            str(mutation_output_dir),
            "--random_seed",
            str(args.random_seed),
            "--max_valid",
            str(args.mutation_max_valid),
            "--batch_size",
            str(args.mutation_batch_size),
            "--num_selection",
            str(args.mutation_num_selection),
            "--timeout",
            str(args.mutation_timeout),
            "--seed_pool_size",
            str(args.mutation_seed_pool_size),
            "--only_valid",
            "--relaxargmut",
            "--seed_selection_algo",
            args.mutation_seed_selection_algo,
            "--mutator_selection_algo",
            args.mutation_mutator_selection_algo,
            "--compiler",
            args.compiler,
            "--coverage-tool",
            args.coverage_tool,
            "--compile-timeout",
            str(args.mutation_compile_timeout),
            "--test-timeout",
            str(args.mutation_test_timeout),
            "--llm_request_timeout",
            str(args.mutation_request_timeout),
            "--llm_max_tokens",
            str(args.mutation_max_tokens),
            "--llm_temperature",
            str(args.mutation_temperature),
            "--max_stagnation_rounds",
            str(args.mutation_max_stagnation_rounds),
        ]
        if args.mutation_max_rounds > 0:
            mutation_cmd.extend(["--max_rounds", str(args.mutation_max_rounds)])
        if args.enable_sanitizer:
            mutation_cmd.append("--enable-sanitizer")
        if args.mutation_llm_provider != "mock":
            mutation_cmd.extend(["--model_name", args.mutation_model])
        if args.mutation_llm_provider in {"openai_compatible", "deepseek"}:
            mutation_cmd.extend(["--llm_api_base", mutation_api_base, "--llm_api_key", mutation_api_key])
        if args.mutation_signature_only_prompt:
            mutation_cmd.append("--mutation-signature-only-prompt")
        if args.mutation_no_execution_context:
            mutation_cmd.append("--mutation-no-execution-context")
        if args.mutation_no_risk_context:
            mutation_cmd.append("--mutation-no-risk-context")
        run_command("mutation", mutation_cmd, repo_root, env, args.dry_run)

    if run_mutation and args.emit_fuzz_targets and not args.dry_run:
        if seed_folder_has_inputs(seed_fix_dir, args.api) and mutation_output_dir.is_dir():
            from pipeline.fuzz_adapter import emit_fuzz_targets

            backends = ["libfuzzer", "afl"] if args.fuzz_backend == "both" else [args.fuzz_backend]
            try:
                manifest = emit_fuzz_targets(
                    mutation_dir=mutation_output_dir,
                    api_dir=api_dir,
                    api_name=args.api,
                    backends=backends,
                    compiler=args.compiler,
                    include_dirs=discover_include_dirs(api_dir),
                    source_files=discover_link_sources(api_dir),
                    enable_sanitizer=args.enable_sanitizer,
                    per_harness=args.fuzz_per_harness,
                )
                print(
                    "[fuzz-adapter] emitted fuzz harnesses for api={!r} from {} accepted sources".format(
                        args.api,
                        manifest["source_harness_count"],
                    )
                )
            except (FileNotFoundError, KeyError) as exc:
                print(f"[fuzz-adapter] skipped: {exc}")

    print(f"[done] mode: {args.mode}")
    print(f"[done] runtime root: {runtime_root}")
    print(f"[done] api dir: {api_dir}")
    print(f"[done] seed outputs: {seed_output_dir}")
    print(f"[done] mutation outputs: {mutation_output_dir}")
    print(f"[done] cache root: {runtime_root / 'cache'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
