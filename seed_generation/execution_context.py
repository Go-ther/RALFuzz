from __future__ import annotations

import os
import pathlib
import re
import shlex
import subprocess
from collections import defaultdict
from typing import Dict, Iterable, List, Sequence, Set, Tuple

from seed_types import ApiSpec, ExecutionContext

CONTROL_KEYWORDS: Set[str] = {"if", "for", "while", "switch", "return", "sizeof", "do", "case"}
INIT_HINTS: Tuple[str, ...] = ("init", "create", "open", "alloc", "new", "setup", "begin", "parse", "build", "make", "start")
CLEANUP_HINTS: Tuple[str, ...] = ("cleanup", "free", "destroy", "close", "release", "delete", "deinit", "fini", "end", "dispose")
LINE_MARKER_RE = re.compile(r'^\s*#\s*(?:line\s+)?\d+\s+"([^"]+)"')


def strip_c_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = re.sub(r"//.*?$", " ", text, flags=re.MULTILINE)
    return text


def _parse_known_preprocessor_condition(expr: str) -> bool | None:
    cleaned = re.sub(r"\s+", "", expr or "")
    while cleaned.startswith("(") and cleaned.endswith(")") and len(cleaned) > 2:
        cleaned = cleaned[1:-1].strip()
    if cleaned == "0":
        return False
    if cleaned == "1":
        return True
    return None


def strip_preprocessor_noise(text: str) -> str:
    lines = (text or "").splitlines()
    out: List[str] = []
    branch_states: List[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        if stripped.startswith("#"):
            directive = stripped[1:].strip()
            parts = directive.split(None, 1)
            keyword = parts[0].lower() if parts else ""
            rest = parts[1].strip() if len(parts) > 1 else ""

            if keyword == "if":
                known = _parse_known_preprocessor_condition(rest)
                if known is True:
                    branch_states.append("keep")
                elif known is False:
                    branch_states.append("skip")
                else:
                    branch_states.append("unknown")
            elif keyword in {"ifdef", "ifndef"}:
                branch_states.append("unknown")
            elif keyword == "elif" and branch_states:
                current = branch_states[-1]
                known = _parse_known_preprocessor_condition(rest)
                if current == "keep":
                    branch_states[-1] = "skip"
                elif known is True:
                    branch_states[-1] = "keep"
                elif known is False:
                    branch_states[-1] = "skip"
                else:
                    branch_states[-1] = "unknown"
            elif keyword == "else" and branch_states:
                current = branch_states[-1]
                if current == "keep":
                    branch_states[-1] = "skip"
                elif current == "skip":
                    branch_states[-1] = "keep"
                else:
                    branch_states[-1] = "unknown"
            elif keyword == "endif" and branch_states:
                branch_states.pop()

            i += 1
            while line.rstrip().endswith("\\") and i < len(lines):
                line = lines[i]
                i += 1
            continue

        if "skip" not in branch_states:
            out.append(line)
        i += 1

    return "\n".join(out)


def split_flags(flags: str) -> List[str]:
    if not flags:
        return []
    if os.name == "nt":
        return shlex.split(flags, posix=False)
    return shlex.split(flags)


def normalize_source_path(path_text: str, base_dir: pathlib.Path | None = None) -> str:
    raw = (path_text or "").strip().strip('"')
    if not raw or (raw.startswith("<") and raw.endswith(">")):
        return ""
    path = pathlib.Path(raw)
    if not path.is_absolute():
        path = (base_dir or pathlib.Path.cwd()) / path
    try:
        path = path.resolve()
    except Exception:
        path = path.absolute()
    return str(path).replace("\\", "/").lower()


def normalize_source_roots(source_roots: Sequence[str]) -> List[str]:
    roots: List[str] = []
    for raw in source_roots:
        path = pathlib.Path(raw)
        if path.is_file():
            path = path.parent
        roots.append(normalize_source_path(str(path)))
    return unique([root for root in roots if root])


def path_is_in_roots(path_text: str, normalized_roots: Sequence[str], base_dir: pathlib.Path | None = None) -> bool:
    normalized = normalize_source_path(path_text, base_dir=base_dir)
    if not normalized:
        return False
    for root in normalized_roots:
        if normalized == root or normalized.startswith(root + "/"):
            return True
    return False


def preprocess_source_with_clang(
    source_file: pathlib.Path,
    source_roots: Sequence[str],
    cflags: str,
    timeout_sec: int,
) -> str:
    resolved_source = source_file.resolve()
    normalized_roots = normalize_source_roots(source_roots) or [normalize_source_path(str(resolved_source.parent))]
    cmd = ["clang", "-E", "-x", "c", "-std=c11"] + split_flags(cflags) + [str(resolved_source)]
    try:
        out = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"clang preprocessing timed out for {source_file}") from exc

    stdout_text = (out.stdout or b"").decode("utf-8", errors="replace")
    stderr_text = (out.stderr or b"").decode("utf-8", errors="replace")

    if out.returncode != 0:
        err = (stderr_text or stdout_text).strip()
        raise RuntimeError(f"clang preprocessing failed for {source_file}: {err}")

    kept_lines: List[str] = []
    keep_current = False
    for line in stdout_text.splitlines():
        marker = LINE_MARKER_RE.match(line)
        if marker:
            keep_current = path_is_in_roots(marker.group(1), normalized_roots, base_dir=resolved_source.parent)
            continue
        if keep_current:
            kept_lines.append(line)
    filtered = "\n".join(kept_lines)
    if not filtered.strip():
        raise RuntimeError(f"clang preprocessing produced no project-local content for {source_file}")
    return filtered


def unique(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def looks_init(name: str) -> bool:
    lower = name.lower()
    return any(k in lower for k in INIT_HINTS)


def looks_cleanup(name: str) -> bool:
    lower = name.lower()
    return any(k in lower for k in CLEANUP_HINTS)


def lcp_len(a: str, b: str) -> int:
    i, n = 0, min(len(a), len(b))
    while i < n and a[i] == b[i]:
        i += 1
    return i


def find_matching_brace(text: str, open_idx: int) -> int:
    depth, i = 0, open_idx
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def extract_call_sequence(code: str, dedup: bool = False) -> List[str]:
    cleaned = strip_c_comments(strip_preprocessor_noise(code))
    calls: List[str] = []
    for m in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", cleaned):
        fn = m.group(1)
        if fn not in CONTROL_KEYWORDS:
            calls.append(fn)
    return unique(calls) if dedup else calls


def collect_source_files(source_dirs: Sequence[str], file_patterns: Sequence[str], max_source_files: int) -> List[pathlib.Path]:
    found: List[pathlib.Path] = []
    seen: Set[pathlib.Path] = set()
    pats = list(file_patterns) if file_patterns else ["*.c"]
    for root_raw in source_dirs:
        root = pathlib.Path(root_raw)
        if root.is_file():
            if root.suffix.lower() in {".c", ".h"} and root not in seen:
                seen.add(root)
                found.append(root)
            continue
        if not root.is_dir():
            continue
        for pat in pats:
            for p in sorted(root.rglob(pat)):
                if not p.is_file() or p.suffix.lower() not in {".c", ".h"} or p in seen:
                    continue
                seen.add(p)
                found.append(p)
                if max_source_files > 0 and len(found) >= max_source_files:
                    return found
    return found


def build_call_graph(
    source_files: Sequence[pathlib.Path],
    source_roots: Sequence[str],
    cflags: str,
    preprocess_timeout: int,
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    def calls_from_source(text: str) -> Dict[str, List[str]]:
        cleaned = strip_c_comments(text)
        pattern = re.compile(r"(?m)^[ \t]*(?:[A-Za-z_][\w\s\*\(\),\[\]]*?)\s+([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{")
        out: Dict[str, List[str]] = {}
        for m in pattern.finditer(cleaned):
            name = m.group(1)
            if name in CONTROL_KEYWORDS:
                continue
            open_idx = cleaned.find("{", m.start())
            if open_idx < 0:
                continue
            close_idx = find_matching_brace(cleaned, open_idx)
            if close_idx < 0:
                continue
            body = cleaned[open_idx + 1 : close_idx]
            out[name] = extract_call_sequence(body, dedup=True)
        return out

    calls: Dict[str, List[str]] = {}
    callers: Dict[str, List[str]] = defaultdict(list)
    skipped_files: List[str] = []
    for p in source_files:
        try:
            text = preprocess_source_with_clang(
                p,
                source_roots=source_roots,
                cflags=cflags,
                timeout_sec=preprocess_timeout,
            )
        except Exception as exc:
            reason = str(exc).splitlines()[0]
            fatal_match = re.search(r"fatal error:\s*(.+)", reason)
            if fatal_match:
                reason = fatal_match.group(1).strip()
            skipped_files.append(f"{p} ({reason})")
            continue
        local = calls_from_source(text)
        for func, seq in local.items():
            calls[func] = unique(calls.get(func, []) + seq)
    for caller, callees in calls.items():
        for callee in callees:
            callers[callee].append(caller)
    callers = {k: unique(v) for k, v in callers.items()}
    if skipped_files:
        print(f"Skipped {len(skipped_files)} support file(s) that were not needed for API context discovery.")
    if not calls and source_files:
        raise RuntimeError("clang preprocessing did not yield any analyzable project source files")
    return calls, callers


def infer_neighbors_from_catalog(target: str, api_catalog: Sequence[str], max_neighbors: int) -> List[str]:
    prefix = target.split("_", 1)[0] if "_" in target else target[:4]
    scored: List[Tuple[int, int, str]] = []
    for name in api_catalog:
        if name == target:
            continue
        score = lcp_len(target, name)
        if "_" in target and "_" in name and name.split("_", 1)[0] == prefix:
            score += 6
        elif name.startswith(prefix):
            score += 4
        if looks_init(name) or looks_cleanup(name):
            score += 2
        scored.append((-score, len(name), name))
    scored.sort()
    return [x[2] for x in scored[:max_neighbors]]


def infer_execution_context(
    spec: ApiSpec,
    api_catalog: Sequence[str],
    calls: Dict[str, List[str]],
    callers: Dict[str, List[str]],
    source_files_scanned: int,
    max_neighbors: int,
    max_init: int,
    max_cleanup: int,
    max_chain_len: int,
) -> ExecutionContext:
    target, catalog_set = spec.api_name, set(api_catalog)
    n1 = [n for n in calls.get(target, []) if n in catalog_set and n != target]
    n2 = [n for n in callers.get(target, []) if n in catalog_set and n != target]
    neighbors = unique(n1 + n2 + infer_neighbors_from_catalog(target, api_catalog, max_neighbors))[:max_neighbors]
    init_path = unique([n for n in neighbors + list(api_catalog) if n != target and looks_init(n)])[:max_init]
    cleanup_path = unique([n for n in neighbors + list(api_catalog) if n != target and looks_cleanup(n)])[:max_cleanup]
    chain: List[str] = []
    if init_path:
        chain.append(init_path[0])
    for n in neighbors:
        if n != target and n not in chain and not looks_cleanup(n):
            chain.append(n)
            break
    chain.append(target)
    if cleanup_path and cleanup_path[0] not in chain:
        chain.append(cleanup_path[0])
    chain = unique(chain)[:max_chain_len] if max_chain_len > 0 else unique(chain)
    return ExecutionContext(
        init_path=init_path,
        cleanup_path=cleanup_path,
        neighbor_apis=neighbors,
        short_call_chain_template=chain,
        source_files_scanned=source_files_scanned,
    )
