from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Mapping, Sequence

RUNTIME_HIT_REPORT_PREFIX = "SEED_RUNTIME_HITS"


@dataclass
class RuntimeHitReport:
    observed: bool = False
    target_count: int = 0
    init_count: int = 0
    cleanup_count: int = 0

    @property
    def target_hit(self) -> bool:
        return self.target_count > 0

    @property
    def init_hit(self) -> bool:
        return self.init_count > 0

    @property
    def cleanup_hit(self) -> bool:
        return self.cleanup_count > 0


def build_runtime_name_map(target_api: str, init_candidates: Sequence[str], cleanup_candidates: Sequence[str]) -> Dict[str, str]:
    name_map: Dict[str, str] = {}
    if target_api:
        name_map[target_api] = "target"
    for name in init_candidates:
        if name and name not in name_map:
            name_map[name] = "init"
    for name in cleanup_candidates:
        if name and name not in name_map:
            name_map[name] = "cleanup"
    return name_map


def build_runtime_hit_prelude() -> str:
    return f"""#include <stdio.h>
#include <stdlib.h>

static unsigned seed_runtime_target_hits = 0;
static unsigned seed_runtime_init_hits = 0;
static unsigned seed_runtime_cleanup_hits = 0;

static void seed_mark_target_hit(void) {{ seed_runtime_target_hits++; }}
static void seed_mark_init_hit(void) {{ seed_runtime_init_hits++; }}
static void seed_mark_cleanup_hit(void) {{ seed_runtime_cleanup_hits++; }}

static void seed_report_runtime_hits(void) {{
    fprintf(
        stdout,
        "\\n{RUNTIME_HIT_REPORT_PREFIX} target=%u init=%u cleanup=%u\\n",
        seed_runtime_target_hits,
        seed_runtime_init_hits,
        seed_runtime_cleanup_hits
    );
    fflush(stdout);
}}

static void seed_register_runtime_report(void) {{
    static int registered = 0;
    if (!registered) {{
        atexit(seed_report_runtime_hits);
        registered = 1;
    }}
}}

#define SEED_WRAP_TARGET(fn) (seed_mark_target_hit(), (fn))
#define SEED_WRAP_INIT(fn) (seed_mark_init_hit(), (fn))
#define SEED_WRAP_CLEANUP(fn) (seed_mark_cleanup_hit(), (fn))
"""


def inject_runtime_hit_reporting(code: str) -> str:
    marker = "seed_register_runtime_report();"
    if marker in code:
        return code
    match = re.search(r"\bint\s+main\s*\([^)]*\)\s*\{", code, flags=re.MULTILINE | re.DOTALL)
    if not match:
        return code
    insert_at = match.end()
    return code[:insert_at] + "\n    " + marker + code[insert_at:]


def _wrap_named_calls(code: str, name_map: Mapping[str, str]) -> str:
    if not name_map:
        return code

    out: list[str] = []
    i = 0
    state = "normal"
    line_start = True

    while i < len(code):
        ch = code[i]

        if state == "line_comment":
            out.append(ch)
            i += 1
            if ch == "\n":
                state = "normal"
                line_start = True
            continue

        if state == "block_comment":
            out.append(ch)
            i += 1
            if ch == "*" and i < len(code) and code[i] == "/":
                out.append("/")
                i += 1
                state = "normal"
            continue

        if state == "string":
            out.append(ch)
            i += 1
            if ch == "\\" and i < len(code):
                out.append(code[i])
                i += 1
                continue
            if ch == "\"":
                state = "normal"
            continue

        if state == "char":
            out.append(ch)
            i += 1
            if ch == "\\" and i < len(code):
                out.append(code[i])
                i += 1
                continue
            if ch == "'":
                state = "normal"
            continue

        if state == "preprocessor":
            out.append(ch)
            i += 1
            if ch == "\n":
                state = "normal"
                line_start = True
            continue

        if ch == "/" and i + 1 < len(code) and code[i + 1] == "/":
            out.append("//")
            i += 2
            state = "line_comment"
            continue
        if ch == "/" and i + 1 < len(code) and code[i + 1] == "*":
            out.append("/*")
            i += 2
            state = "block_comment"
            continue
        if ch == "\"":
            out.append(ch)
            i += 1
            state = "string"
            line_start = False
            continue
        if ch == "'":
            out.append(ch)
            i += 1
            state = "char"
            line_start = False
            continue
        if line_start and ch == "#":
            out.append(ch)
            i += 1
            state = "preprocessor"
            line_start = False
            continue

        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < len(code) and (code[j].isalnum() or code[j] == "_"):
                j += 1
            word = code[i:j]
            k = j
            while k < len(code) and code[k].isspace():
                k += 1
            if word in name_map and k < len(code) and code[k] == "(":
                macro = f"SEED_WRAP_{name_map[word].upper()}({word})"
                out.append(macro)
            else:
                out.append(word)
            line_start = False
            i = j
            continue

        out.append(ch)
        i += 1
        if ch == "\n":
            line_start = True
        elif not ch.isspace():
            line_start = False

    return "".join(out)


def instrument_runtime_hits(code: str, target_api: str, init_candidates: Sequence[str], cleanup_candidates: Sequence[str]) -> str:
    name_map = build_runtime_name_map(target_api, init_candidates, cleanup_candidates)
    instrumented = _wrap_named_calls(code, name_map)
    instrumented = inject_runtime_hit_reporting(instrumented)
    return build_runtime_hit_prelude() + "\n" + instrumented


def parse_runtime_hit_report(output: str) -> RuntimeHitReport:
    match = re.search(
        rf"{re.escape(RUNTIME_HIT_REPORT_PREFIX)}\s+target=(\d+)\s+init=(\d+)\s+cleanup=(\d+)",
        output or "",
    )
    if not match:
        return RuntimeHitReport()
    return RuntimeHitReport(
        observed=True,
        target_count=int(match.group(1)),
        init_count=int(match.group(2)),
        cleanup_count=int(match.group(3)),
    )
