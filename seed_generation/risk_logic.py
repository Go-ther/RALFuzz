from __future__ import annotations

import json
import pathlib
import re
from typing import Dict, Iterable, List, Optional, Tuple

from execution_context import extract_call_sequence, strip_c_comments
from seed_types import ApiSpec, ExecutionContext, RiskContext, SeedValidation


def _unique(items: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def load_risk_cards(path: Optional[pathlib.Path]) -> Dict[str, Dict]:
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    if isinstance(raw, list):
        out: Dict[str, Dict] = {}
        for rec in raw:
            if isinstance(rec, dict):
                api = str(rec.get("api_name", "")).strip()
                if api:
                    out[api] = rec
        return out
    return {}


def infer_risk_context(spec: ApiSpec, execution_context: ExecutionContext, risk_overrides: Dict[str, Dict], max_risk_tags: int, max_boundary_hints: int) -> RiskContext:
    text = f"{spec.api_name} {spec.api_signature}".lower()
    tags: List[str] = []
    hints: List[str] = []
    score = 0

    def add(v: str) -> None:
        if v and v not in tags:
            tags.append(v)

    def add_hint(v: str) -> None:
        if v and v not in hints:
            hints.append(v)

    buffer_keywords = ["memcpy", "memmove", "strcpy", "strncpy", "strcat", "sprintf", "vsprintf", "snprintf", "scan", "read", "write", "copy"]
    parser_keywords = ["parse", "json", "xml", "decode", "encode", "deserialize"]
    lifecycle_keywords = ["alloc", "create", "new", "free", "delete", "destroy", "release"]

    if any(k in text for k in buffer_keywords):
        score += 3
        add("buffer-boundary")
        add("memory-corruption")
        add_hint("Try size values 0, 1, near buffer length, and SIZE_MAX.")
        add_hint("Mix NULL pointers with non-zero sizes and non-NULL with zero sizes.")
    if any(k in text for k in parser_keywords):
        score += 2
        add("input-structure")
        add("parser-state")
        add_hint("Use malformed, truncated, oversized, and deeply nested inputs.")
        add_hint("Try invalid encodings and random binary payloads.")
    if "*" in spec.api_signature:
        score += 1
        add("pointer-lifetime")
        add_hint("Test NULL, aliasing, and repeated pointer reuse patterns.")
    if re.search(r"\b(size_t|ssize_t|int|long|short|float|double)\b", spec.api_signature):
        score += 1
        add("numeric-boundary")
        add_hint("Try -1, 0, 1, INT_MIN, INT_MAX, and overflow-adjacent values.")
    if any(k in text for k in lifecycle_keywords):
        score += 1
        add("resource-lifecycle")
        add_hint("Try mismatched create/free order and repeated cleanup paths.")
    if execution_context.cleanup_path:
        add("cleanup-sensitive")

    high_risk_neighbors = _unique(
        [
            n
            for n in execution_context.neighbor_apis
            if any(k in n.lower() for k in buffer_keywords + parser_keywords + lifecycle_keywords)
        ]
    )

    level = "high" if score >= 5 else ("medium" if score >= 3 else "low")
    history_summary = ""
    if "memory-corruption" in tags:
        history_summary = "Historically memory boundary misuse often leads to crash or overwrite signals."
    elif "input-structure" in tags:
        history_summary = "Parser-like APIs often fail on malformed nesting and length mismatches."

    override = risk_overrides.get(spec.api_name, {})
    if override:
        ov_level = str(override.get("risk_level", "")).strip()
        if ov_level:
            level = ov_level
        ov_tags = override.get("risk_tags", [])
        if isinstance(ov_tags, list):
            for t in ov_tags:
                add(str(t).strip())
        ov_hints = override.get("boundary_hints", [])
        if isinstance(ov_hints, list):
            for h in ov_hints:
                add_hint(str(h).strip())
        ov_summary = str(override.get("history_summary", "")).strip()
        if ov_summary:
            history_summary = ov_summary
        ov_neighbors = override.get("high_risk_neighbors", [])
        if isinstance(ov_neighbors, list):
            high_risk_neighbors = _unique(high_risk_neighbors + [str(x).strip() for x in ov_neighbors if str(x).strip()])

    return RiskContext(
        risk_level=level,
        risk_tags=tags[:max_risk_tags],
        boundary_hints=hints[:max_boundary_hints],
        history_summary=history_summary,
        high_risk_neighbors=high_risk_neighbors[: max(1, max_risk_tags)],
    )


def extract_risk_markers_from_code(code: str) -> List[str]:
    cleaned = strip_c_comments(code or "")
    lower = cleaned.lower()
    markers: List[str] = []

    def add(name: str) -> None:
        if name not in markers:
            markers.append(name)

    if re.search(r"\b(int_max|int_min|size_max|ssize_max|long_max|long_min|uint_max|ptrdiff_max|ptrdiff_min)\b", lower):
        add("numeric-extreme")
    if re.search(r"(?<![a-z0-9_])-1(?![a-z0-9_])", lower):
        add("numeric-extreme")

    if re.search(r"\b(invalid|malformed|truncated|overflow|underflow|random|fuzz|corrupt|broken|unterminated|partial)\b", lower):
        add("malformed-input")

    if re.search(r"\b(binary|hex|encoding)\b", lower) or re.search(r"0x[0-9a-f]{6,}", lower):
        add("binary-payload")

    if re.search(r"\b(nested|deep)\b", lower):
        add("deep-structure")
    elif re.search(r"\\\"[^\\n]{1,80}\\\"\s*:\s*\{\\\"", cleaned):
        add("deep-structure")

    if re.search(r"\b(alias|reuse|double[ _-]?free|use[- ]after[- ]free)\b", lower):
        add("pointer-aliasing")

    return markers


def risk_marker_profile_from_code(code: str) -> str:
    markers = extract_risk_markers_from_code(code)
    return ",".join(markers) if markers else "none"


def expected_risk_families(risk_context: RiskContext) -> List[str]:
    tags = set(risk_context.risk_tags)
    families: List[str] = []

    def add(name: str) -> None:
        if name not in families:
            families.append(name)

    if {"input-structure", "parser-state"} & tags or "Parser-like APIs" in risk_context.history_summary:
        add("structured-input")
    if {"numeric-boundary", "buffer-boundary"} & tags or risk_context.boundary_hints:
        add("boundary-literal")
    if "pointer-lifetime" in tags:
        add("pointer-safety")
    if risk_context.high_risk_neighbors:
        add("high-risk-neighbor")
    if {"resource-lifecycle", "cleanup-sensitive"} & tags:
        add("lifecycle-stress")
    return families


def _contains_boundary_literals(code: str) -> bool:
    checks = [
        r"\bNULL\b",
        r"(?<![A-Za-z0-9_])-1(?![A-Za-z0-9_])",
        r"(?<![A-Za-z0-9_])0(?![A-Za-z0-9_])",
        r"(?<![A-Za-z0-9_])1(?![A-Za-z0-9_])",
        r"\"\"",
        r"\bINT_MAX\b",
        r"\bINT_MIN\b",
        r"\bSIZE_MAX\b",
    ]
    return any(re.search(pat, code) for pat in checks)


def _has_repeated_calls(call_seq: List[str], names: Iterable[str]) -> bool:
    wanted = set(names)
    seen: set[str] = set()
    for name in call_seq:
        if name not in wanted:
            continue
        if name in seen:
            return True
        seen.add(name)
    return False


def extract_aligned_risk_families(
    code: str,
    spec: ApiSpec,
    execution_context: ExecutionContext,
    risk_context: RiskContext,
) -> List[str]:
    cleaned = strip_c_comments(code or "")
    raw_markers = set(extract_risk_markers_from_code(cleaned))
    call_seq = extract_call_sequence(cleaned)
    call_set = set(call_seq)
    aligned: List[str] = []

    def add(name: str) -> None:
        if name not in aligned:
            aligned.append(name)

    for family in expected_risk_families(risk_context):
        if family == "structured-input" and raw_markers.intersection({"malformed-input", "binary-payload", "deep-structure"}):
            add(family)
        elif family == "boundary-literal" and _contains_boundary_literals(cleaned):
            add(family)
        elif family == "pointer-safety":
            null_call = re.search(rf"\b{re.escape(spec.api_name)}\s*\(\s*NULL\b", cleaned)
            if ("pointer-aliasing" in raw_markers) or bool(null_call):
                add(family)
        elif family == "high-risk-neighbor" and any(name in call_set for name in risk_context.high_risk_neighbors):
            add(family)
        elif family == "lifecycle-stress":
            has_pair = bool(set(execution_context.init_path) & call_set) and bool(set(execution_context.cleanup_path) & call_set)
            if has_pair or _has_repeated_calls(call_seq, execution_context.init_path) or _has_repeated_calls(call_seq, execution_context.cleanup_path):
                add(family)
    return aligned


def should_enforce_risk_hit(spec: ApiSpec, risk_context: RiskContext, policy: str) -> bool:
    if policy == "off":
        return False
    if not risk_context.boundary_hints:
        return False
    if policy == "strict":
        return True
    text = f"{spec.api_name} {spec.api_signature}".lower()
    parser_like = any(re.search(rf"\b{kw}\w*", text) for kw in ("parse", "decode", "deserialize"))
    boundary_sensitive = any(re.search(rf"\b{kw}\w*", text) for kw in ("length", "size", "buffer"))
    risk_tag_trigger = ("input-structure" in set(risk_context.risk_tags)) and boundary_sensitive
    return parser_like or risk_tag_trigger


def should_retry_generation_for_risk(val: SeedValidation) -> bool:
    return (not val.risk_hit) and val.compile_success and val.run_success and val.target_hit


def risk_retry_score(val: SeedValidation) -> Tuple[int, int, int, int, int]:
    healthy = int(val.compile_success and val.run_success and val.target_hit)
    return (
        healthy,
        int(val.risk_hit),
        len(val.aligned_risk_families),
        int(val.valid),
        int(val.init_hit and val.cleanup_hit),
    )
