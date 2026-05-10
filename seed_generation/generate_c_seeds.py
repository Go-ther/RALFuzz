#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import os
import pathlib
import sys
import time
from collections import defaultdict
from dataclasses import asdict
from typing import Dict, List, Set

if __package__ in {None, ""}:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from api_specs import auto_discover_api_specs, enrich_missing_signatures, load_api_specs, load_prompt_template, sanitize_api_folder_name
from execution_context import build_call_graph, collect_source_files, extract_call_sequence, infer_execution_context
from llm_client import OpenAICompatClient, merge_usage
from mutation.toolchain_env import configure_clang_environment, normalize_clang_compiler
from prompting import build_prompt, build_risk_retry_prompt, build_truncation_retry_prompt
from risk_logic import infer_risk_context, load_risk_cards, risk_retry_score, should_retry_generation_for_risk
from seed_types import ApiSpec
from storage import build_seed_skeleton_key, list_numeric_stems, load_json_or_default, normalized_text_hash, save_json, write_raw_seed_file
from validation import should_retry_generation_for_truncation, validate_seed


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    rem = seconds - minutes * 60
    return f"{minutes}m{rem:04.1f}s"


def format_rate(value: float) -> str:
    return f"{value * 100:.1f}%"


def format_usage(usage: Dict) -> str:
    if not isinstance(usage, dict) or not usage.get("available"):
        return "tokens unavailable"
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")
    return f"{total_tokens} tokens (prompt {prompt_tokens}, output {completion_tokens})"


def yes_no(value: bool) -> str:
    return "yes" if value else "no"


def short_error(exc: Exception, limit: int = 220) -> str:
    text = str(exc).splitlines()[0].strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def outcome_label(record: Dict) -> str:
    if record.get("valid"):
        return "kept"
    reason = str(record.get("reason", "not_kept"))
    messages = {
        "generation_failed": "request failed",
        "generation_empty": "empty response",
        "compile_failed": "did not compile",
        "run_failed": "failed while running",
        "target_api_not_called": "missed target API",
        "missing_init_or_cleanup": "missing setup or cleanup",
        "risk_not_relevant": "missed risk guidance",
        "text_duplicate": "duplicate seed",
        "skeleton_limit_reached": "too similar to existing seeds",
    }
    return messages.get(reason, reason.replace("_", " "))


def log_run_header(args: argparse.Namespace, spec_count: int, out_root: pathlib.Path) -> None:
    quota = args.target_valid_per_api if args.target_valid_per_api > 0 else "no early stop"
    mode = "start fresh" if args.overwrite_existing else "continue existing run"
    print("")
    print("RALFuzz seed generator")
    print(f"Target APIs: {spec_count}")
    print(f"Attempts per API: {args.samples_per_api}; valid-seed goal: {quota}")
    print(f"Model: {args.model} via {args.endpoint_mode}")
    print(f"Run mode: {mode}")
    print(f"Results folder: {out_root}")
    print("")


def log_sample(
    api_name: str,
    sample_id: int,
    samples_per_api: int,
    valid_count: int,
    record: Dict,
    usage: Dict,
) -> None:
    status = "Kept" if record.get("valid") else "Not kept"
    checks = [
        f"compile {yes_no(bool(record.get('compile_success')))}",
        f"run {yes_no(bool(record.get('run_success')))}",
        f"target {yes_no(bool(record.get('target_hit')))}",
    ]
    print(
        f"  [{sample_id}/{samples_per_api}] {status}: {outcome_label(record)}. "
        f"Valid seeds: {valid_count}. Checks: {', '.join(checks)}. {format_usage(usage)}."
    )


def summarize_api(api_name: str, out: Dict) -> str:
    metrics = out.get("quality_metrics", {}) if isinstance(out, dict) else {}
    return (
        f"Finished {api_name}: kept {out.get('valid_count', 0)} valid seeds from "
        f"{out.get('samples_attempted', 0)} attempts in {format_duration(float(out.get('g_time_sec', 0.0) or 0.0))}. "
        f"Compile pass {format_rate(float(metrics.get('compile_rate', 0.0) or 0.0))}, "
        f"run pass {format_rate(float(metrics.get('run_rate', 0.0) or 0.0))}. "
        f"{format_usage(out.get('token_usage', {}))}."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate C seeds via OpenAI-compatible APIs or Ollama native chat.")
    parser.add_argument(
        "--api-spec-file",
        default=None,
        help="TXT/JSON API spec file. Optional when --auto-api-dir is used.",
    )
    parser.add_argument(
        "--auto-api-dir",
        action="append",
        default=[],
        help="Auto-discover API prototypes from these directories (repeatable).",
    )
    parser.add_argument(
        "--auto-header-glob",
        action="append",
        default=[],
        help="Header glob for auto discovery under --auto-api-dir (default: *.h).",
    )
    parser.add_argument(
        "--auto-exclude-header-glob",
        action="append",
        default=[],
        help="Exclude glob for auto discovery under --auto-api-dir.",
    )
    parser.add_argument(
        "--auto-style",
        choices=["auto", "cjson", "generic"],
        default="auto",
        help="Prototype style for auto discovery.",
    )
    parser.add_argument(
        "--auto-api-name-regex",
        default=None,
        help="Optional regex to filter auto-discovered API names.",
    )
    parser.add_argument(
        "--auto-non-recursive",
        action="store_true",
        default=False,
        help="Disable recursive header scan in auto discovery.",
    )
    parser.add_argument(
        "--auto-sort",
        action="store_true",
        default=False,
        help="Sort auto-discovered APIs by name.",
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", ""))
    parser.add_argument("--model", required=True)
    parser.add_argument("--endpoint-mode", default="auto", choices=["auto", "chat", "completion", "ollama"])
    parser.add_argument(
        "--single-shot-delay-ms",
        type=int,
        default=0,
        help="Delay between sequential single-shot requests (fixed single-shot mode).",
    )
    parser.add_argument("--library-name", default="libc")
    parser.add_argument("--library-version", default="unknown")
    parser.add_argument("--default-header", default="stdio.h")
    parser.add_argument("--doc-url-template", default=None)
    parser.add_argument("--prompt-template-file", default=str(pathlib.Path(__file__).parent / "templates" / "prompt_steps_c.txt"))
    parser.add_argument("--samples-per-api", type=int, default=25)
    parser.add_argument(
        "--target-valid-per-api",
        type=int,
        default=0,
        help="Stop early for one API when kept valid seeds reach this quota (0 disables early-stop).",
    )
    parser.add_argument("--temperature", type=float, default=0.4)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument(
        "--retry-on-truncation",
        type=int,
        default=1,
        help="Retry a sample when validation suggests output truncation/fence breakage.",
    )
    parser.add_argument("--truncation-retry-max-tokens", type=int, default=1024)
    parser.add_argument("--truncation-retry-temperature", type=float, default=0.35)
    parser.add_argument("--truncation-retry-top-p", type=float, default=0.9)
    parser.add_argument("--truncation-retry-max-lines", type=int, default=96)
    parser.add_argument("--truncation-retry-min-marker-kinds", type=int, default=1)
    parser.add_argument("--truncation-retry-require-boundary-value", dest="truncation_retry_require_boundary_value", action="store_true")
    parser.add_argument("--no-truncation-retry-require-boundary-value", dest="truncation_retry_require_boundary_value", action="store_false")
    parser.set_defaults(truncation_retry_require_boundary_value=True)
    parser.add_argument("--truncation-retry-require-high-risk-neighbor", dest="truncation_retry_require_high_risk_neighbor", action="store_true")
    parser.add_argument("--no-truncation-retry-require-high-risk-neighbor", dest="truncation_retry_require_high_risk_neighbor", action="store_false")
    parser.set_defaults(truncation_retry_require_high_risk_neighbor=False)
    parser.add_argument("--request-timeout", type=int, default=120)
    parser.add_argument(
        "--network-retries",
        type=int,
        default=2,
        help="Retry count for transient network/API failures during generation.",
    )
    parser.add_argument(
        "--network-retry-backoff-sec",
        type=float,
        default=2.0,
        help="Base exponential backoff in seconds between generation network retries.",
    )
    parser.add_argument("--crawl-timeout", type=int, default=20)
    parser.add_argument("--compiler", default="clang")
    parser.add_argument("--c-standard", default="c11")
    parser.add_argument("--cflags", default="")
    parser.add_argument("--ldflags", default="")
    parser.add_argument("--compile-timeout", type=int, default=15)
    parser.add_argument("--run-timeout", type=int, default=3)
    parser.add_argument("--skip-run-validation", action="store_true", default=False)
    parser.add_argument("--overwrite-existing", action="store_true", default=False)
    parser.add_argument("--auto-wrap-main", dest="auto_wrap_main", action="store_true")
    parser.add_argument("--no-auto-wrap-main", dest="auto_wrap_main", action="store_false")
    parser.set_defaults(auto_wrap_main=True)
    parser.add_argument("--source-dir", action="append", default=[])
    parser.add_argument("--source-file-pattern", action="append", default=None)
    parser.add_argument("--max-source-files", type=int, default=1000)
    parser.add_argument("--max-neighbor-apis", type=int, default=6)
    parser.add_argument("--max-init-candidates", type=int, default=3)
    parser.add_argument("--max-cleanup-candidates", type=int, default=3)
    parser.add_argument("--max-chain-len", type=int, default=4)
    parser.add_argument("--risk-cards-file", default=None)
    parser.add_argument("--max-risk-tags", type=int, default=6)
    parser.add_argument("--max-boundary-hints", type=int, default=6)
    parser.add_argument("--require-init-cleanup", dest="require_init_cleanup", action="store_true")
    parser.add_argument("--no-require-init-cleanup", dest="require_init_cleanup", action="store_false")
    parser.set_defaults(require_init_cleanup=True)
    parser.add_argument(
        "--risk-relevance-policy",
        choices=["off", "auto", "strict"],
        default="auto",
        help="Risk marker enforcement policy for validated seeds.",
    )
    parser.add_argument("--risk-prompt-hardening", dest="risk_prompt_hardening", action="store_true")
    parser.add_argument("--no-risk-prompt-hardening", dest="risk_prompt_hardening", action="store_false")
    parser.set_defaults(risk_prompt_hardening=True)
    parser.add_argument("--risk-card", dest="include_risk_card", action="store_true")
    parser.add_argument("--no-risk-card", dest="include_risk_card", action="store_false")
    parser.set_defaults(include_risk_card=True)
    parser.add_argument("--risk-min-marker-kinds", type=int, default=2)
    parser.add_argument("--risk-require-boundary-value", dest="risk_require_boundary_value", action="store_true")
    parser.add_argument("--no-risk-require-boundary-value", dest="risk_require_boundary_value", action="store_false")
    parser.set_defaults(risk_require_boundary_value=True)
    parser.add_argument("--risk-require-high-risk-neighbor", dest="risk_require_high_risk_neighbor", action="store_true")
    parser.add_argument("--no-risk-require-high-risk-neighbor", dest="risk_require_high_risk_neighbor", action="store_false")
    parser.set_defaults(risk_require_high_risk_neighbor=True)
    parser.add_argument("--risk-boost-retries", type=int, default=1)
    parser.add_argument("--risk-boost-temperature", type=float, default=0.72)
    parser.add_argument("--risk-boost-top-p", type=float, default=0.98)
    parser.add_argument("--enforce-init-target-order", dest="enforce_init_target_order", action="store_true")
    parser.add_argument("--no-enforce-init-target-order", dest="enforce_init_target_order", action="store_false")
    parser.set_defaults(enforce_init_target_order=False)
    parser.add_argument("--max-per-skeleton", type=int, default=2)
    parser.add_argument("--disable-text-dedup", action="store_true", default=False)
    args = parser.parse_args()
    args.compiler = normalize_clang_compiler(args.compiler, source="seed-generation --compiler")
    sanitizer_requested = "-fsanitize=" in args.cflags or "-fsanitize=" in args.ldflags
    configure_clang_environment(compiler=args.compiler, enable_sanitizer=sanitizer_requested)

    out_root = pathlib.Path(args.output_dir)
    raw_root, fix_root = out_root / "raw", out_root / "fix"
    raw_root.mkdir(parents=True, exist_ok=True)
    fix_root.mkdir(parents=True, exist_ok=True)

    specs: List[ApiSpec] = []
    if args.api_spec_file:
        specs = load_api_specs(pathlib.Path(args.api_spec_file), args.default_header, args.doc_url_template)

    if (not specs) and args.auto_api_dir:
        specs = auto_discover_api_specs(
            api_dirs=args.auto_api_dir,
            style=args.auto_style,
            api_name_regex=args.auto_api_name_regex,
            header_globs=args.auto_header_glob,
            exclude_header_globs=args.auto_exclude_header_glob,
            recursive=not args.auto_non_recursive,
            sort_by_name=args.auto_sort,
        )
    if not specs:
        raise RuntimeError("No API specs loaded. Provide --api-spec-file or --auto-api-dir.")
    enrich_missing_signatures(specs, args.crawl_timeout)
    template = load_prompt_template(pathlib.Path(args.prompt_template_file))
    log_run_header(args, len(specs), out_root)

    source_files = collect_source_files(args.source_dir, args.source_file_pattern or ["*.c"], args.max_source_files)
    calls: Dict[str, List[str]] = {}
    callers: Dict[str, List[str]] = {}
    if source_files:
        calls, callers = build_call_graph(
            source_files,
            source_roots=args.source_dir,
            cflags=args.cflags,
            preprocess_timeout=args.compile_timeout,
        )
        print(f"Analyzed {len(source_files)} source file(s) for call context.")
    else:
        print("No source files were provided; using API names to infer nearby calls.")

    risk_overrides = load_risk_cards(pathlib.Path(args.risk_cards_file)) if args.risk_cards_file else {}
    if risk_overrides:
        print(f"Loaded risk guidance for {len(risk_overrides)} API(s).")

    client = OpenAICompatClient(
        args.base_url,
        args.api_key,
        args.model,
        args.endpoint_mode,
        args.request_timeout,
        sequential_delay_ms=args.single_shot_delay_ms,
        network_retries=args.network_retries,
        network_retry_backoff_sec=args.network_retry_backoff_sec,
    )
    selected_api_catalog = [s.api_name for s in specs]
    context_api_catalog = list(selected_api_catalog)
    context_signature_map = {s.api_name: s.api_signature for s in specs if s.api_signature}
    if args.auto_api_dir:
        try:
            context_specs = auto_discover_api_specs(
                api_dirs=args.auto_api_dir,
                style=args.auto_style,
                api_name_regex=None,
                header_globs=args.auto_header_glob,
                exclude_header_globs=args.auto_exclude_header_glob,
                recursive=not args.auto_non_recursive,
                sort_by_name=False,
            )
        except Exception as exc:
            print(f"Could not expand the full API catalog; continuing with selected APIs. Details: {exc}")
        else:
            if context_specs:
                context_api_catalog = [s.api_name for s in context_specs]
                context_signature_map.update({s.api_name: s.api_signature for s in context_specs if s.api_signature})
                if len(context_api_catalog) > len(selected_api_catalog):
                    print(f"Prepared context hints from {len(context_api_catalog)} discovered API name(s).")
    if args.overwrite_existing:
        outputs: Dict[str, Dict] = {}
        seed_bank: Dict[str, Dict] = {}
    else:
        outputs = load_json_or_default(out_root / "outputs.json", dict, {})
        seed_bank = load_json_or_default(out_root / "seed_bank.json", dict, {})
    run_start = time.time()

    for idx, spec in enumerate(specs, 1):
        if not spec.api_signature:
            print(f"Skipping {spec.api_name}: no usable C signature was found.")
            continue
        api_folder = sanitize_api_folder_name(spec.api_name)
        raw_api_dir, fix_api_dir = raw_root / api_folder, fix_root / api_folder
        raw_api_dir.mkdir(parents=True, exist_ok=True)
        fix_api_dir.mkdir(parents=True, exist_ok=True)
        if args.overwrite_existing:
            for p in raw_api_dir.glob("*.c"):
                try:
                    p.unlink()
                except Exception:
                    pass
            for p in fix_api_dir.glob("*.c"):
                try:
                    p.unlink()
                except Exception:
                    pass
            existing_raw_ids = []
            existing_fix_ids = []
            next_sample_id = 1
        else:
            existing_raw_ids = list_numeric_stems(raw_api_dir)
            existing_fix_ids = list_numeric_stems(fix_api_dir)
            next_sample_id = (max(existing_raw_ids) + 1) if existing_raw_ids else 1

        if not args.overwrite_existing and existing_raw_ids:
            print(
                f"Continuing {spec.api_name}: found {len(existing_raw_ids)} previous attempt(s), "
                f"starting at attempt {next_sample_id}."
            )
            if next_sample_id > args.samples_per_api:
                print(f"Skipping {spec.api_name}: requested attempts are already complete.")
                continue

        execution_context = infer_execution_context(
            spec,
            context_api_catalog,
            calls,
            callers,
            len(source_files),
            args.max_neighbor_apis,
            args.max_init_candidates,
            args.max_cleanup_candidates,
            args.max_chain_len,
            context_signature_map,
        )
        risk_context = infer_risk_context(spec, execution_context, risk_overrides, args.max_risk_tags, args.max_boundary_hints)
        prompt = build_prompt(
            template,
            args.library_name,
            args.library_version,
            spec,
            execution_context,
            risk_context,
            include_risk_card=args.include_risk_card,
            risk_prompt_hardening=args.risk_prompt_hardening,
            risk_min_marker_kinds=args.risk_min_marker_kinds,
            risk_require_boundary_value=args.risk_require_boundary_value,
            risk_require_high_risk_neighbor=args.risk_require_high_risk_neighbor,
        )

        print("")
        print(f"Generating seeds for {spec.api_name} ({idx}/{len(specs)})")
        print(f"Signature: {spec.api_signature}")
        t0 = time.time()

        prev_out = outputs.get(spec.api_name, {}) if isinstance(outputs.get(spec.api_name, {}), dict) else {}
        prev_records_raw = prev_out.get("records", []) if isinstance(prev_out.get("records", []), list) else []
        recs: List[Dict] = []
        for r in prev_records_raw:
            if not isinstance(r, dict):
                continue
            try:
                rid = int(r.get("id", 0))
            except Exception:
                continue
            if rid < next_sample_id:
                rr = dict(r)
                rr["id"] = rid
                recs.append(rr)
        recs.sort(key=lambda x: x.get("id", 0))

        samples_attempted = len(recs)
        samples_received = sum(1 for r in recs if r.get("raw_file"))
        compile_ok_count = sum(1 for r in recs if r.get("compile_success"))
        run_ok_count = sum(1 for r in recs if r.get("run_success"))
        target_hit_count = sum(1 for r in recs if r.get("target_hit"))
        init_hit_count = sum(1 for r in recs if r.get("init_hit"))
        cleanup_hit_count = sum(1 for r in recs if r.get("cleanup_hit"))
        risk_hit_count = sum(1 for r in recs if r.get("risk_hit"))
        raw_risk_hit_count = sum(1 for r in recs if r.get("raw_risk_hit"))
        runtime_hits_observed_count = sum(1 for r in recs if r.get("runtime_hits_observed"))
        aligned_risk_score_sum = sum(float(r.get("aligned_risk_score", 0.0) or 0.0) for r in recs)
        validation_passed_count = sum(1 for r in recs if r.get("validation_reason") == "ok")
        endpoint_used = str(prev_out.get("endpoint_used", "")) if prev_out else ""

        seen_hashes: Set[str] = set()
        skeleton_counts: Dict[str, int] = defaultdict(int)
        kept: List[Dict] = []
        for fid in existing_fix_ids:
            fix_file = fix_api_dir / f"{fid}.c"
            if not fix_file.exists():
                continue
            try:
                code = fix_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            text_hash = normalized_text_hash(code)
            skeleton_key = build_seed_skeleton_key(code, spec, execution_context, risk_context)
            seen_hashes.add(text_hash)
            skeleton_counts[skeleton_key] += 1
            kept.append(
                {
                    "seed_id": fid,
                    "from_completion_id": 0,
                    "file": str(fix_file),
                    "skeleton_key": skeleton_key,
                    "text_hash": text_hash,
                }
            )
        kept.sort(key=lambda x: x["seed_id"])
        valid_count = len(kept)
        next_fix_seed_id = (max(existing_fix_ids) + 1) if existing_fix_ids else 1
        target_valid_per_api = max(0, int(args.target_valid_per_api))
        quota_reached_before_generation = target_valid_per_api > 0 and valid_count >= target_valid_per_api
        if quota_reached_before_generation:
            print(
                f"Already have {valid_count} valid seed(s), meeting the goal of {target_valid_per_api}."
            )

        for j in range(next_sample_id, args.samples_per_api + 1):
            if target_valid_per_api > 0 and valid_count >= target_valid_per_api:
                if not quota_reached_before_generation:
                    print(
                        f"Reached the valid-seed goal ({valid_count}/{target_valid_per_api}); stopping this API."
                    )
                break
            used_prompt = prompt
            gen_temperature = args.temperature
            gen_top_p = args.top_p
            gen_max_tokens = args.max_tokens
            request_usages: List[Dict] = []
            try:
                completions, used, usage = client.generate(
                    used_prompt,
                    1,
                    gen_max_tokens,
                    gen_temperature,
                    gen_top_p,
                )
                request_usages.append(usage)
            except Exception as exc:
                recs.append(
                    {
                        "id": j,
                        "raw_file": "",
                        "valid": False,
                        "reason": "generation_failed",
                        "validation_reason": "generation_failed",
                        "fix_file": "",
                        "compile_success": False,
                        "run_success": False,
                        "target_hit": False,
                        "init_hit": False,
                        "cleanup_hit": False,
                        "risk_hit": False,
                        "risk_markers": [],
                        "raw_risk_hit": False,
                        "aligned_risk_families": [],
                        "expected_risk_families": [],
                        "aligned_risk_score": 0.0,
                        "runtime_hits_observed": False,
                        "target_count": 0,
                        "init_count": 0,
                        "cleanup_count": 0,
                        "text_hash": "",
                        "skeleton_key": "",
                        "compile_msg": "",
                        "run_msg": str(exc),
                        "retry_count": 0,
                        "retry_note": "",
                        "risk_retry_count": 0,
                        "risk_retry_note": "",
                        "generation_temperature": gen_temperature,
                        "generation_top_p": gen_top_p,
                        "generation_max_tokens": gen_max_tokens,
                        "token_usage": merge_usage(request_usages),
                        "token_usage_by_attempt": request_usages,
                    }
                )
                samples_attempted += 1
                log_sample(spec.api_name, j, args.samples_per_api, valid_count, recs[-1], recs[-1].get("token_usage", {}))
                print(
                    "    The model request failed. Check the model server, base URL, API key, "
                    f"and timeout. Details: {short_error(exc)}"
                )
                continue
            if not completions:
                recs.append(
                    {
                        "id": j,
                        "raw_file": "",
                        "valid": False,
                        "reason": "generation_empty",
                        "validation_reason": "generation_empty",
                        "fix_file": "",
                        "compile_success": False,
                        "run_success": False,
                        "target_hit": False,
                        "init_hit": False,
                        "cleanup_hit": False,
                        "risk_hit": False,
                        "risk_markers": [],
                        "raw_risk_hit": False,
                        "aligned_risk_families": [],
                        "expected_risk_families": [],
                        "aligned_risk_score": 0.0,
                        "runtime_hits_observed": False,
                        "target_count": 0,
                        "init_count": 0,
                        "cleanup_count": 0,
                        "text_hash": "",
                        "skeleton_key": "",
                        "compile_msg": "",
                        "run_msg": "empty completion",
                        "retry_count": 0,
                        "retry_note": "",
                        "risk_retry_count": 0,
                        "risk_retry_note": "",
                        "generation_temperature": gen_temperature,
                        "generation_top_p": gen_top_p,
                        "generation_max_tokens": gen_max_tokens,
                        "token_usage": merge_usage(request_usages),
                        "token_usage_by_attempt": request_usages,
                    }
                )
                samples_attempted += 1
                log_sample(spec.api_name, j, args.samples_per_api, valid_count, recs[-1], recs[-1].get("token_usage", {}))
                continue

            if not endpoint_used:
                endpoint_used = used
            completion = completions[0]
            retry_count = 0
            retry_note = ""
            risk_retry_count = 0
            risk_retry_note = ""
            val = validate_seed(
                completion,
                spec,
                execution_context,
                risk_context,
                args.compiler,
                args.c_standard,
                args.cflags,
                args.ldflags,
                args.compile_timeout,
                args.run_timeout,
                args.skip_run_validation,
                args.auto_wrap_main,
                args.require_init_cleanup,
                args.risk_relevance_policy,
                args.enforce_init_target_order,
            )
            while retry_count < max(0, args.retry_on_truncation):
                if not should_retry_generation_for_truncation(completion, val):
                    break
                retry_count += 1
                retry_prompt = build_truncation_retry_prompt(
                    prompt,
                    spec,
                    risk_context,
                    max_lines=args.truncation_retry_max_lines,
                    min_marker_kinds=args.truncation_retry_min_marker_kinds,
                    require_boundary_value=args.truncation_retry_require_boundary_value,
                    require_high_risk_neighbor=args.truncation_retry_require_high_risk_neighbor,
                )
                retry_max_tokens = max(args.max_tokens, args.truncation_retry_max_tokens)
                retry_temperature = args.truncation_retry_temperature
                retry_top_p = args.truncation_retry_top_p
                try:
                    retry_completions, used_retry, retry_usage = client.generate(
                        retry_prompt,
                        1,
                        retry_max_tokens,
                        retry_temperature,
                        retry_top_p,
                    )
                    request_usages.append(retry_usage)
                except Exception as exc:
                    retry_note = f"retry_failed:{exc}"
                    break
                if not retry_completions:
                    retry_note = "retry_empty"
                    break
                completion = retry_completions[0]
                used_prompt = retry_prompt
                gen_temperature = retry_temperature
                gen_top_p = retry_top_p
                gen_max_tokens = retry_max_tokens
                if not endpoint_used:
                    endpoint_used = used_retry
                val = validate_seed(
                    completion,
                    spec,
                    execution_context,
                    risk_context,
                    args.compiler,
                    args.c_standard,
                    args.cflags,
                    args.ldflags,
                    args.compile_timeout,
                    args.run_timeout,
                    args.skip_run_validation,
                    args.auto_wrap_main,
                    args.require_init_cleanup,
                    args.risk_relevance_policy,
                    args.enforce_init_target_order,
                )

            while risk_retry_count < max(0, args.risk_boost_retries):
                if not should_retry_generation_for_risk(val):
                    break
                risk_retry_count += 1
                boosted_prompt = build_risk_retry_prompt(
                    prompt,
                    spec,
                    risk_context,
                    min_marker_kinds=args.risk_min_marker_kinds,
                    require_boundary_value=args.risk_require_boundary_value,
                    require_high_risk_neighbor=args.risk_require_high_risk_neighbor,
                )
                try:
                    risk_completions, used_risk, risk_usage = client.generate(
                        boosted_prompt,
                        1,
                        args.max_tokens,
                        args.risk_boost_temperature,
                        args.risk_boost_top_p,
                    )
                    request_usages.append(risk_usage)
                except Exception as exc:
                    risk_retry_note = f"risk_retry_failed:{exc}"
                    break
                if not risk_completions:
                    risk_retry_note = "risk_retry_empty"
                    break
                if not endpoint_used:
                    endpoint_used = used_risk
                candidate_completion = risk_completions[0]
                candidate_val = validate_seed(
                    candidate_completion,
                    spec,
                    execution_context,
                    risk_context,
                    args.compiler,
                    args.c_standard,
                    args.cflags,
                    args.ldflags,
                    args.compile_timeout,
                    args.run_timeout,
                    args.skip_run_validation,
                    args.auto_wrap_main,
                    args.require_init_cleanup,
                    args.risk_relevance_policy,
                    args.enforce_init_target_order,
                )
                if risk_retry_score(candidate_val) > risk_retry_score(val):
                    completion = candidate_completion
                    val = candidate_val
                    used_prompt = boosted_prompt
                    gen_temperature = args.risk_boost_temperature
                    gen_top_p = args.risk_boost_top_p
                    gen_max_tokens = args.max_tokens
                if val.risk_hit:
                    risk_retry_note = "risk_retry_hit"
                    break
            if risk_retry_count > 0 and not risk_retry_note:
                risk_retry_note = "risk_retry_no_gain"

            samples_received += 1
            raw_path = raw_api_dir / f"{j}.c"
            write_raw_seed_file(raw_path, used_prompt, completion)
            if val.compile_success:
                compile_ok_count += 1
            if val.run_success:
                run_ok_count += 1
            if val.target_hit:
                target_hit_count += 1
            if val.init_hit:
                init_hit_count += 1
            if val.cleanup_hit:
                cleanup_hit_count += 1
            if val.risk_hit:
                risk_hit_count += 1
            if val.raw_risk_hit:
                raw_risk_hit_count += 1
            if val.runtime_hits_observed:
                runtime_hits_observed_count += 1
            aligned_risk_score_sum += val.aligned_risk_score
            if val.valid:
                validation_passed_count += 1

            final_valid = val.valid
            final_reason = val.reason
            text_hash = ""
            skeleton_key = ""
            if val.valid:
                text_hash = normalized_text_hash(val.syntax_fixed_code)
                skeleton_key = build_seed_skeleton_key(val.syntax_fixed_code, spec, execution_context, risk_context)
                if (not args.disable_text_dedup) and text_hash in seen_hashes:
                    final_valid = False
                    final_reason = "text_duplicate"
                elif args.max_per_skeleton > 0 and skeleton_counts[skeleton_key] >= args.max_per_skeleton:
                    final_valid = False
                    final_reason = "skeleton_limit_reached"
                else:
                    seen_hashes.add(text_hash)
                    skeleton_counts[skeleton_key] += 1

            fix_path = ""
            if final_valid:
                fix_id = next_fix_seed_id
                next_fix_seed_id += 1
                valid_count += 1
                fix_file = fix_api_dir / f"{fix_id}.c"
                fix_file.write_text(val.syntax_fixed_code + "\n", encoding="utf-8")
                fix_path = str(fix_file)
                kept.append({"seed_id": fix_id, "from_completion_id": j, "file": fix_path, "skeleton_key": skeleton_key, "text_hash": text_hash})

            recs.append(
                {
                    "id": j,
                    "raw_file": str(raw_path),
                    "valid": final_valid,
                    "reason": final_reason,
                    "validation_reason": val.reason,
                    "fix_file": fix_path,
                    "compile_success": val.compile_success,
                    "run_success": val.run_success,
                    "target_hit": val.target_hit,
                    "init_hit": val.init_hit,
                    "cleanup_hit": val.cleanup_hit,
                    "risk_hit": val.risk_hit,
                    "risk_markers": val.risk_markers,
                    "raw_risk_hit": val.raw_risk_hit,
                    "aligned_risk_families": val.aligned_risk_families,
                    "expected_risk_families": val.expected_risk_families,
                    "aligned_risk_score": val.aligned_risk_score,
                    "runtime_hits_observed": val.runtime_hits_observed,
                    "target_count": val.target_count,
                    "init_count": val.init_count,
                    "cleanup_count": val.cleanup_count,
                    "text_hash": text_hash,
                    "skeleton_key": skeleton_key,
                    "compile_msg": val.compile_msg,
                    "run_msg": val.run_msg,
                    "retry_count": retry_count,
                    "retry_note": retry_note,
                    "risk_retry_count": risk_retry_count,
                    "risk_retry_note": risk_retry_note,
                    "generation_temperature": gen_temperature,
                    "generation_top_p": gen_top_p,
                    "generation_max_tokens": gen_max_tokens,
                    "token_usage": merge_usage(request_usages),
                    "token_usage_by_attempt": request_usages,
                }
            )
            samples_attempted += 1
            log_sample(spec.api_name, j, args.samples_per_api, valid_count, recs[-1], recs[-1].get("token_usage", {}))

            outputs[spec.api_name] = {
                "api_name": spec.api_name,
                "api_signature": spec.api_signature,
                "header": spec.header,
                "doc_url": spec.doc_url,
                "execution_context": asdict(execution_context),
                "risk_context": asdict(risk_context),
                "prompt": prompt,
                "endpoint_used": endpoint_used,
                "samples_requested": args.samples_per_api,
                "samples_attempted": samples_attempted,
                "samples_received": samples_received,
                "target_valid_per_api": target_valid_per_api,
                "validation_passed_count": validation_passed_count,
                "valid_count": valid_count,
                "g_time_sec": time.time() - t0,
                "token_usage": merge_usage([r.get("token_usage", {}) for r in recs if isinstance(r, dict)]),
                "quality_metrics": {
                    "compile_ok_count": compile_ok_count,
                    "run_ok_count": run_ok_count,
                    "target_hit_count": target_hit_count,
                    "init_hit_count": init_hit_count,
                    "cleanup_hit_count": cleanup_hit_count,
                    "risk_hit_count": risk_hit_count,
                    "raw_risk_hit_count": raw_risk_hit_count,
                    "runtime_hits_observed_count": runtime_hits_observed_count,
                    "compile_rate": (compile_ok_count / samples_attempted) if samples_attempted else 0.0,
                    "run_rate": (run_ok_count / samples_attempted) if samples_attempted else 0.0,
                    "target_api_hit_rate": (target_hit_count / samples_attempted) if samples_attempted else 0.0,
                    "init_path_hit_rate": (init_hit_count / samples_attempted) if samples_attempted else 0.0,
                    "cleanup_path_hit_rate": (cleanup_hit_count / samples_attempted) if samples_attempted else 0.0,
                    "aligned_risk_relevance_rate": (risk_hit_count / samples_attempted) if samples_attempted else 0.0,
                    "raw_risk_marker_rate": (raw_risk_hit_count / samples_attempted) if samples_attempted else 0.0,
                    "risk_relevance_rate": (risk_hit_count / samples_attempted) if samples_attempted else 0.0,
                    "runtime_hit_observation_rate": (runtime_hits_observed_count / samples_attempted) if samples_attempted else 0.0,
                    "avg_aligned_risk_score": (aligned_risk_score_sum / samples_attempted) if samples_attempted else 0.0,
                    "avg_call_chain_length": 0.0,
                    "unique_valid_seed_count": valid_count,
                },
                "records": recs,
            }
            seed_bank[spec.api_name] = {
                "target_api": spec.api_name,
                "api_signature": spec.api_signature,
                "execution_context": asdict(execution_context),
                "risk_context": asdict(risk_context),
                "seeds": kept,
            }
            save_json(out_root / "outputs.json", outputs)
            save_json(out_root / "seed_bank.json", seed_bank)

        g_time = time.time() - t0
        compile_rate = (compile_ok_count / samples_attempted) if samples_attempted else 0.0
        run_rate = (run_ok_count / samples_attempted) if samples_attempted else 0.0
        target_hit_rate = (target_hit_count / samples_attempted) if samples_attempted else 0.0
        init_hit_rate = (init_hit_count / samples_attempted) if samples_attempted else 0.0
        cleanup_hit_rate = (cleanup_hit_count / samples_attempted) if samples_attempted else 0.0
        aligned_risk_rate = (risk_hit_count / samples_attempted) if samples_attempted else 0.0
        raw_risk_rate = (raw_risk_hit_count / samples_attempted) if samples_attempted else 0.0
        runtime_observation_rate = (runtime_hits_observed_count / samples_attempted) if samples_attempted else 0.0
        avg_aligned_risk_score = (aligned_risk_score_sum / samples_attempted) if samples_attempted else 0.0
        avg_chain_len = 0.0
        if kept:
            lens = []
            for rec in kept:
                try:
                    code = pathlib.Path(rec["file"]).read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                lens.append(len(extract_call_sequence(code)))
            avg_chain_len = (sum(lens) / len(lens)) if lens else 0.0

        outputs[spec.api_name] = {
            "api_name": spec.api_name,
            "api_signature": spec.api_signature,
            "header": spec.header,
            "doc_url": spec.doc_url,
            "execution_context": asdict(execution_context),
            "risk_context": asdict(risk_context),
            "prompt": prompt,
            "endpoint_used": endpoint_used,
            "samples_requested": args.samples_per_api,
            "samples_attempted": samples_attempted,
            "samples_received": samples_received,
            "target_valid_per_api": target_valid_per_api,
            "validation_passed_count": validation_passed_count,
            "valid_count": valid_count,
            "g_time_sec": g_time,
            "token_usage": merge_usage([r.get("token_usage", {}) for r in recs if isinstance(r, dict)]),
            "quality_metrics": {
                "compile_ok_count": compile_ok_count,
                "run_ok_count": run_ok_count,
                "target_hit_count": target_hit_count,
                "init_hit_count": init_hit_count,
                "cleanup_hit_count": cleanup_hit_count,
                "risk_hit_count": risk_hit_count,
                "raw_risk_hit_count": raw_risk_hit_count,
                "runtime_hits_observed_count": runtime_hits_observed_count,
                "compile_rate": compile_rate,
                "run_rate": run_rate,
                "target_api_hit_rate": target_hit_rate,
                "init_path_hit_rate": init_hit_rate,
                "cleanup_path_hit_rate": cleanup_hit_rate,
                "aligned_risk_relevance_rate": aligned_risk_rate,
                "raw_risk_marker_rate": raw_risk_rate,
                "risk_relevance_rate": aligned_risk_rate,
                "runtime_hit_observation_rate": runtime_observation_rate,
                "avg_aligned_risk_score": avg_aligned_risk_score,
                "avg_call_chain_length": avg_chain_len,
                "unique_valid_seed_count": valid_count,
            },
            "records": recs,
        }
        seed_bank[spec.api_name] = {
            "target_api": spec.api_name,
            "api_signature": spec.api_signature,
            "execution_context": asdict(execution_context),
            "risk_context": asdict(risk_context),
            "seeds": kept,
        }

        print(summarize_api(spec.api_name, outputs[spec.api_name]))
        save_json(out_root / "outputs.json", outputs)
        save_json(out_root / "seed_bank.json", seed_bank)

    total_samples = sum(int(v.get("samples_attempted", len(v.get("records", []))) or 0) for v in outputs.values())
    total_received = sum(int(v.get("samples_received", 0) or 0) for v in outputs.values())
    total_compile_ok = sum(v["quality_metrics"]["compile_ok_count"] for v in outputs.values())
    total_run_ok = sum(v["quality_metrics"]["run_ok_count"] for v in outputs.values())
    total_target_hit = sum(v["quality_metrics"]["target_hit_count"] for v in outputs.values())
    total_init_hit = sum(v["quality_metrics"].get("init_hit_count", 0) for v in outputs.values())
    total_cleanup_hit = sum(v["quality_metrics"].get("cleanup_hit_count", 0) for v in outputs.values())
    total_risk_hit = sum(v["quality_metrics"].get("risk_hit_count", 0) for v in outputs.values())
    total_raw_risk_hit = sum(v["quality_metrics"].get("raw_risk_hit_count", 0) for v in outputs.values())
    total_runtime_observed = sum(v["quality_metrics"].get("runtime_hits_observed_count", 0) for v in outputs.values())
    total_aligned_risk_score = sum(
        float(v["quality_metrics"].get("avg_aligned_risk_score", 0.0) or 0.0)
        * float(v.get("samples_attempted", len(v.get("records", []))) or 0)
        for v in outputs.values()
    )
    total_valid = sum(v["valid_count"] for v in outputs.values())
    total_token_usage = merge_usage([v.get("token_usage", {}) for v in outputs.values() if isinstance(v, dict)])
    unique_valid_hashes: Set[str] = set()
    for out in outputs.values():
        for rec in out.get("records", []):
            if rec.get("valid") and rec.get("text_hash"):
                unique_valid_hashes.add(str(rec.get("text_hash")))
    summary_args = vars(args).copy()
    if summary_args.get("api_key"):
        summary_args["api_key"] = "***redacted***"
    summary = {
        "total_apis_loaded": len(specs),
        "total_apis_generated": len(outputs),
        "total_samples": total_samples,
        "total_samples_received": total_received,
        "total_valid_seeds": total_valid,
        "unique_valid_seed_count": len(unique_valid_hashes),
        "compile_rate": (total_compile_ok / total_samples) if total_samples else 0.0,
        "run_rate": (total_run_ok / total_samples) if total_samples else 0.0,
        "target_api_hit_rate": (total_target_hit / total_samples) if total_samples else 0.0,
        "init_path_hit_rate": (total_init_hit / total_samples) if total_samples else 0.0,
        "cleanup_path_hit_rate": (total_cleanup_hit / total_samples) if total_samples else 0.0,
        "aligned_risk_relevance_rate": (total_risk_hit / total_samples) if total_samples else 0.0,
        "raw_risk_marker_rate": (total_raw_risk_hit / total_samples) if total_samples else 0.0,
        "risk_relevance_rate": (total_risk_hit / total_samples) if total_samples else 0.0,
        "runtime_hit_observation_rate": (total_runtime_observed / total_samples) if total_samples else 0.0,
        "avg_aligned_risk_score": (total_aligned_risk_score / total_samples) if total_samples else 0.0,
        "token_usage": total_token_usage,
        "elapsed_sec": time.time() - run_start,
        "args": summary_args,
    }
    save_json(out_root / "summary.json", summary)
    print("")
    print("Run complete")
    print(
        f"Generated {summary['total_valid_seeds']} valid seed(s) "
        f"({summary['unique_valid_seed_count']} unique) from {summary['total_samples']} attempt(s)."
    )
    print(
        f"Quality: compile pass {format_rate(summary['compile_rate'])}, "
        f"run pass {format_rate(summary['run_rate'])}, "
        f"target hit {format_rate(summary['target_api_hit_rate'])}."
    )
    print(f"Usage: {format_usage(summary['token_usage'])}; elapsed {format_duration(summary['elapsed_sec'])}.")
    print("Saved files:")
    print(f"  Summary: {out_root / 'summary.json'}")
    print(f"  Detailed outputs: {out_root / 'outputs.json'}")
    print(f"  Seed bank: {out_root / 'seed_bank.json'}")


if __name__ == "__main__":
    main()


