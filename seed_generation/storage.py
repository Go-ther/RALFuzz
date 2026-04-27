from __future__ import annotations

import hashlib
import json
import pathlib
import re
from typing import Dict, List, Sequence

from execution_context import extract_call_sequence, strip_c_comments
from risk_logic import risk_marker_profile_from_code
from seed_types import ApiSpec, ExecutionContext, RiskContext


def normalized_text_hash(code: str) -> str:
    normalized = re.sub(r"\s+", " ", strip_c_comments(code)).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def boundary_profile_from_code(code: str) -> str:
    checks = [
        ("zero", r"(?<![A-Za-z0-9_])0(?![A-Za-z0-9_])"),
        ("one", r"(?<![A-Za-z0-9_])1(?![A-Za-z0-9_])"),
        ("minus_one", r"(?<![A-Za-z0-9_])-1(?![A-Za-z0-9_])"),
        ("null", r"\bNULL\b"),
        ("empty_str", r"\"\""),
        ("int_max", r"\bINT_MAX\b"),
        ("int_min", r"\bINT_MIN\b"),
        ("size_max", r"\bSIZE_MAX\b"),
    ]
    markers = [label for label, pat in checks if re.search(pat, code)]
    return ",".join(markers) if markers else "none"


def first_match(calls: Sequence[str], candidates: Sequence[str]) -> str:
    cset = set(candidates)
    for c in calls:
        if c in cset:
            return c
    return "none"


def build_seed_skeleton_key(code: str, spec: ApiSpec, execution_context: ExecutionContext, risk_context: RiskContext) -> str:
    seq = extract_call_sequence(code, dedup=True)
    init_family = first_match(seq, execution_context.init_path)
    cleanup_family = first_match(seq, execution_context.cleanup_path)
    neigh_set = set(execution_context.neighbor_apis)
    neigh_seq = [c for c in seq if c in neigh_set][:3]
    neigh_profile = ",".join(neigh_seq) if neigh_seq else "none"
    boundary_profile = boundary_profile_from_code(code)
    if boundary_profile == "none" and risk_context.boundary_hints:
        boundary_profile = "hinted"
    risk_profile = risk_marker_profile_from_code(code)
    return "|".join([spec.api_name, init_family, neigh_profile, cleanup_family, boundary_profile, risk_profile])


def write_raw_seed_file(path: pathlib.Path, prompt: str, completion: str) -> None:
    parts = [
        "===== PROMPT =====",
        (prompt or "").rstrip(),
        "",
        "===== RAW COMPLETION =====",
        (completion or "").rstrip(),
        "",
    ]
    path.write_text("\n".join(parts), encoding="utf-8")


def save_json(path: pathlib.Path, obj: Dict) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def load_json_or_default(path: pathlib.Path, expected_type: type, default):
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return data if isinstance(data, expected_type) else default


def list_numeric_stems(dir_path: pathlib.Path) -> List[int]:
    if not dir_path.exists() or not dir_path.is_dir():
        return []
    out: List[int] = []
    for p in dir_path.glob("*.c"):
        if p.stem.isdigit():
            out.append(int(p.stem))
    return sorted(out)
