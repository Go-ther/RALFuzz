from __future__ import annotations

import os
import pathlib
import re
import shlex
import subprocess
import tempfile
from typing import List, Sequence, Tuple

from execution_context import extract_call_sequence, looks_cleanup, looks_init, strip_c_comments
from instrumentation import instrument_runtime_hits, parse_runtime_hit_report
from risk_logic import (
    expected_risk_families,
    extract_aligned_risk_families,
    extract_risk_markers_from_code,
    should_enforce_risk_hit,
)
from seed_types import ApiSpec, ExecutionContext, RiskContext, SeedValidation


PROMPT_ECHO_MARKERS: Sequence[str] = (
    "Library:",
    "Target:",
    "Header:",
    "Risk hard constraints",
    "Write one standalone C11 harness",
    "Follow these 5 steps exactly",
)


def strip_leading_prompt_echo(text: str) -> str:
    s = (text or "").replace("\r\n", "\n").lstrip()
    while s.startswith("/*"):
        end = s.find("*/")
        if end < 0:
            break
        comment = s[: end + 2]
        if comment.lstrip().startswith("/* Prompt") or any(marker in comment for marker in PROMPT_ECHO_MARKERS):
            s = s[end + 2 :].lstrip()
            continue
        break
    return s


def extract_code_from_llm_output(text: str) -> str:
    s = strip_leading_prompt_echo(text).strip()
    block = re.search(r"```(?:c|C)?[ \t]*\n?(.*?)```", s, flags=re.DOTALL)
    if block:
        return block.group(1).strip()
    open_fence = re.search(r"```(?:c|C)?[^\n]*\n?", s)
    if open_fence:
        return s[open_fence.end() :].replace("```", "").strip()
    return s


def has_main_function(code: str) -> bool:
    return bool(re.search(r"\bint\s+main\s*\(", code))


def has_unclosed_code_fence(text: str) -> bool:
    return (text or "").count("```") % 2 == 1


def likely_truncated_c_code(code: str) -> bool:
    s = (code or "").strip()
    if not s:
        return True
    cleaned = strip_c_comments(s)
    cleaned = re.sub(r'"([^"\\]|\\.)*"', '""', cleaned)
    cleaned = re.sub(r"'([^'\\]|\\.)*'", "''", cleaned)
    if cleaned.count("{") > cleaned.count("}"):
        return True
    if cleaned.count("(") > cleaned.count(")"):
        return True
    tail = s.rstrip()
    if tail and tail[-1] not in {";", "}"}:
        last = tail.splitlines()[-1].strip()
        if last and (not last.startswith("#")):
            return True
    return False


def should_retry_generation_for_truncation(raw_completion: str, val: SeedValidation) -> bool:
    if val.reason not in {"compile_failed", "syntax_fix_failed"}:
        return False
    if "WinMain" in val.compile_msg:
        return True
    if has_unclosed_code_fence(raw_completion):
        return True
    extracted = extract_code_from_llm_output(raw_completion)
    if likely_truncated_c_code(extracted):
        return True
    if has_main_function(raw_completion) and (not has_main_function(val.syntax_fixed_code)):
        return True
    return False


def ensure_header(code: str, header: str) -> str:
    if not header:
        return code
    normalized_line = f'#include "{header}"'
    include_pat = re.compile(
        rf'^\s*#\s*include\s*(?:<\s*{re.escape(header)}\s*>|"\s*{re.escape(header)}\s*")\s*$'
    )
    generic_include_pat = re.compile(r"^\s*#\s*include\b")
    lines = code.splitlines()
    out: List[str] = []
    first_include_idx = None
    seen_header = False
    for line in lines:
        if first_include_idx is None and generic_include_pat.match(line):
            first_include_idx = len(out)
        if include_pat.match(line):
            if not seen_header:
                out.append(normalized_line)
                seen_header = True
            continue
        out.append(line)
    if not seen_header:
        insert_at = first_include_idx if first_include_idx is not None else 0
        out.insert(insert_at, normalized_line)
    return "\n".join(out)


def maybe_wrap_main(code: str, auto_wrap_main: bool) -> str:
    if (not auto_wrap_main) or re.search(r"\bint\s+main\s*\(", code):
        return code
    pre, body = [], []
    for line in code.splitlines():
        if line.strip().startswith("#"):
            pre.append(line)
        else:
            body.append(line)
    out = pre + ([""] if pre else []) + ["int main(void) {"]
    out.extend([("    " + ln) if ln.strip() else "" for ln in body])
    out.extend(["    return 0;", "}"])
    return "\n".join(out)


def any_call_present(code: str, names: Sequence[str]) -> bool:
    if not names:
        return False
    callset = set(extract_call_sequence(code))
    return any(n in callset for n in names)


def run_command(cmd: List[str], timeout_sec: int) -> Tuple[bool, str]:
    try:
        out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout_sec, check=False)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    msg = ""
    if out.stdout:
        msg += out.stdout.decode("utf-8", errors="replace")
    if out.stderr:
        msg += out.stderr.decode("utf-8", errors="replace")
    return out.returncode == 0, msg.strip()


def split_flags(flags: str) -> List[str]:
    if not flags:
        return []
    if os.name == "nt":
        return shlex.split(flags, posix=False)
    return shlex.split(flags)


def check_c_syntax(code: str, compiler: str, c_standard: str, cflags: str, compile_timeout: int) -> Tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix="cseed_syntax_") as td:
        src = pathlib.Path(td) / "seed.c"
        src.write_text(code, encoding="utf-8")
        cmd = [compiler, str(src), f"-std={c_standard}", "-fsyntax-only"] + split_flags(cflags)
        return run_command(cmd, compile_timeout)


def syntax_fix_remove_last_line(code: str, compiler: str, c_standard: str, cflags: str, compile_timeout: int, target_api: str = "") -> str:
    lines = code.splitlines()
    must_keep_main = has_main_function(code)
    original_calls = set(extract_call_sequence(code))
    must_keep_target = bool(target_api) and (target_api in original_calls)
    while lines:
        cand = "\n".join(lines).strip()
        if not cand:
            break
        ok, _ = check_c_syntax(cand, compiler, c_standard, cflags, compile_timeout)
        if ok:
            if must_keep_main and (not has_main_function(cand)):
                return ""
            if must_keep_target and target_api and (target_api not in set(extract_call_sequence(cand))):
                return ""
            return cand
        lines = lines[:-1]
    return ""


def compile_and_run(
    code: str,
    compiler: str,
    c_standard: str,
    cflags: str,
    ldflags: str,
    compile_timeout: int,
    run_timeout: int,
    skip_run_validation: bool,
) -> Tuple[bool, bool, str, str]:
    with tempfile.TemporaryDirectory(prefix="cseed_run_") as td:
        src = pathlib.Path(td) / "seed.c"
        exe = pathlib.Path(td) / ("seed.exe" if os.name == "nt" else "seed.out")
        src.write_text(code, encoding="utf-8")
        cmd = [compiler, str(src), f"-std={c_standard}", "-O0", "-o", str(exe)] + split_flags(cflags) + split_flags(ldflags)
        compile_ok, compile_msg = run_command(cmd, compile_timeout)
        if not compile_ok:
            return False, False, compile_msg, ""
        if skip_run_validation:
            return True, True, compile_msg, ""
        run_ok, run_msg = run_command([str(exe)], run_timeout)
        return True, run_ok, compile_msg, run_msg


def should_enforce_init_hit(spec: ApiSpec, execution_context: ExecutionContext, require_init_cleanup: bool) -> bool:
    if not require_init_cleanup:
        return False
    if not execution_context.init_path:
        return False
    if looks_init(spec.api_name):
        return False
    return True


def should_enforce_cleanup_hit(spec: ApiSpec, execution_context: ExecutionContext, require_init_cleanup: bool) -> bool:
    if not require_init_cleanup:
        return False
    if not execution_context.cleanup_path:
        return False
    if looks_cleanup(spec.api_name):
        return False
    return True


def should_enforce_target_early_order(spec: ApiSpec) -> bool:
    name = spec.api_name.lower()
    return any(k in name for k in ("inithooks", "init", "setup", "sethook", "config"))


def is_primary_work_call(name: str) -> bool:
    lower = name.lower()
    return any(k in lower for k in ("parse", "create", "alloc", "build", "decode", "deserialize"))


def target_called_early_enough(code: str, spec: ApiSpec, execution_context: ExecutionContext) -> bool:
    seq = extract_call_sequence(code)
    if spec.api_name not in seq:
        return False
    target_idx = seq.index(spec.api_name)
    for i, fn in enumerate(seq):
        if i >= target_idx:
            break
        if fn in execution_context.neighbor_apis and is_primary_work_call(fn):
            return False
    return True


def validate_seed(
    raw_code: str,
    spec: ApiSpec,
    execution_context: ExecutionContext,
    risk_context: RiskContext,
    compiler: str,
    c_standard: str,
    cflags: str,
    ldflags: str,
    compile_timeout: int,
    run_timeout: int,
    skip_run_validation: bool,
    auto_wrap_main: bool,
    require_init_cleanup: bool,
    risk_relevance_policy: str,
    enforce_init_target_order: bool,
) -> SeedValidation:
    prepared = maybe_wrap_main(ensure_header(extract_code_from_llm_output(raw_code), spec.header), auto_wrap_main)
    fixed = syntax_fix_remove_last_line(prepared, compiler, c_standard, cflags, compile_timeout, spec.api_name)
    if not fixed:
        return SeedValidation(valid=False, reason="syntax_fix_failed", syntax_fixed_code="")
    instrumented = instrument_runtime_hits(fixed, spec.api_name, execution_context.init_path, execution_context.cleanup_path)
    compile_ok, run_ok, compile_msg, run_msg = compile_and_run(instrumented, compiler, c_standard, cflags, ldflags, compile_timeout, run_timeout, skip_run_validation)
    call_seq = extract_call_sequence(fixed)
    runtime_hits = parse_runtime_hit_report(run_msg) if run_ok and (not skip_run_validation) else parse_runtime_hit_report("")
    static_target_hit = spec.api_name in call_seq
    static_init_hit = any_call_present(fixed, execution_context.init_path)
    static_cleanup_hit = any_call_present(fixed, execution_context.cleanup_path)
    if runtime_hits.observed:
        target_hit = runtime_hits.target_hit
        init_hit = runtime_hits.init_hit
        cleanup_hit = runtime_hits.cleanup_hit
    elif skip_run_validation:
        target_hit = static_target_hit
        init_hit = static_init_hit
        cleanup_hit = static_cleanup_hit
    else:
        target_hit = False
        init_hit = False
        cleanup_hit = False
    risk_markers = extract_risk_markers_from_code(fixed)
    raw_risk_hit = bool(risk_markers)
    expected_families = expected_risk_families(risk_context)
    aligned_families = extract_aligned_risk_families(fixed, spec, execution_context, risk_context)
    risk_hit = bool(aligned_families) if expected_families else raw_risk_hit
    aligned_score = (len(aligned_families) / len(expected_families)) if expected_families else float(raw_risk_hit)
    enforce_risk = should_enforce_risk_hit(spec, risk_context, risk_relevance_policy)
    enforce_order = enforce_init_target_order and should_enforce_target_early_order(spec)
    require_init_hit = should_enforce_init_hit(spec, execution_context, require_init_cleanup)
    require_cleanup = should_enforce_cleanup_hit(spec, execution_context, require_init_cleanup)
    if not compile_ok:
        return SeedValidation(
            False,
            "compile_failed",
            fixed,
            False,
            False,
            target_hit,
            init_hit,
            cleanup_hit,
            risk_hit,
            risk_markers,
            raw_risk_hit=raw_risk_hit,
            aligned_risk_families=aligned_families,
            expected_risk_families=expected_families,
            aligned_risk_score=aligned_score,
            runtime_hits_observed=runtime_hits.observed,
            target_count=runtime_hits.target_count,
            init_count=runtime_hits.init_count,
            cleanup_count=runtime_hits.cleanup_count,
            compile_msg=compile_msg,
            run_msg=run_msg,
        )
    if not run_ok:
        return SeedValidation(
            False,
            "run_failed",
            fixed,
            True,
            False,
            target_hit,
            init_hit,
            cleanup_hit,
            risk_hit,
            risk_markers,
            raw_risk_hit=raw_risk_hit,
            aligned_risk_families=aligned_families,
            expected_risk_families=expected_families,
            aligned_risk_score=aligned_score,
            runtime_hits_observed=runtime_hits.observed,
            target_count=runtime_hits.target_count,
            init_count=runtime_hits.init_count,
            cleanup_count=runtime_hits.cleanup_count,
            compile_msg=compile_msg,
            run_msg=run_msg,
        )
    if not target_hit:
        return SeedValidation(
            False,
            "target_api_not_called",
            fixed,
            True,
            True,
            False,
            init_hit,
            cleanup_hit,
            risk_hit,
            risk_markers,
            raw_risk_hit=raw_risk_hit,
            aligned_risk_families=aligned_families,
            expected_risk_families=expected_families,
            aligned_risk_score=aligned_score,
            runtime_hits_observed=runtime_hits.observed,
            target_count=runtime_hits.target_count,
            init_count=runtime_hits.init_count,
            cleanup_count=runtime_hits.cleanup_count,
            compile_msg=compile_msg,
            run_msg=run_msg,
        )
    if enforce_order and (not target_called_early_enough(fixed, spec, execution_context)):
        return SeedValidation(
            False,
            "target_order_not_early",
            fixed,
            True,
            True,
            True,
            init_hit,
            cleanup_hit,
            risk_hit,
            risk_markers,
            raw_risk_hit=raw_risk_hit,
            aligned_risk_families=aligned_families,
            expected_risk_families=expected_families,
            aligned_risk_score=aligned_score,
            runtime_hits_observed=runtime_hits.observed,
            target_count=runtime_hits.target_count,
            init_count=runtime_hits.init_count,
            cleanup_count=runtime_hits.cleanup_count,
            compile_msg=compile_msg,
            run_msg=run_msg,
        )
    if enforce_risk and (not risk_hit):
        return SeedValidation(
            False,
            "risk_marker_not_hit",
            fixed,
            True,
            True,
            True,
            init_hit,
            cleanup_hit,
            False,
            risk_markers,
            raw_risk_hit=raw_risk_hit,
            aligned_risk_families=aligned_families,
            expected_risk_families=expected_families,
            aligned_risk_score=aligned_score,
            runtime_hits_observed=runtime_hits.observed,
            target_count=runtime_hits.target_count,
            init_count=runtime_hits.init_count,
            cleanup_count=runtime_hits.cleanup_count,
            compile_msg=compile_msg,
            run_msg=run_msg,
        )
    if require_init_hit and not init_hit:
        return SeedValidation(
            False,
            "init_path_not_hit",
            fixed,
            True,
            True,
            True,
            False,
            cleanup_hit,
            risk_hit,
            risk_markers,
            raw_risk_hit=raw_risk_hit,
            aligned_risk_families=aligned_families,
            expected_risk_families=expected_families,
            aligned_risk_score=aligned_score,
            runtime_hits_observed=runtime_hits.observed,
            target_count=runtime_hits.target_count,
            init_count=runtime_hits.init_count,
            cleanup_count=runtime_hits.cleanup_count,
            compile_msg=compile_msg,
            run_msg=run_msg,
        )
    if require_cleanup and not cleanup_hit:
        return SeedValidation(
            False,
            "cleanup_path_not_hit",
            fixed,
            True,
            True,
            True,
            init_hit,
            False,
            risk_hit,
            risk_markers,
            raw_risk_hit=raw_risk_hit,
            aligned_risk_families=aligned_families,
            expected_risk_families=expected_families,
            aligned_risk_score=aligned_score,
            runtime_hits_observed=runtime_hits.observed,
            target_count=runtime_hits.target_count,
            init_count=runtime_hits.init_count,
            cleanup_count=runtime_hits.cleanup_count,
            compile_msg=compile_msg,
            run_msg=run_msg,
        )
    return SeedValidation(
        True,
        "ok",
        fixed,
        True,
        True,
        True,
        init_hit,
        cleanup_hit,
        risk_hit,
        risk_markers,
        raw_risk_hit=raw_risk_hit,
        aligned_risk_families=aligned_families,
        expected_risk_families=expected_families,
        aligned_risk_score=aligned_score,
        runtime_hits_observed=runtime_hits.observed,
        target_count=runtime_hits.target_count,
        init_count=runtime_hits.init_count,
        cleanup_count=runtime_hits.cleanup_count,
        compile_msg=compile_msg,
        run_msg=run_msg,
    )
