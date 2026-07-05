from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from mutation.c_mutators import find_call_spans


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

CONTROL_KEYWORDS = {"if", "for", "while", "switch", "return", "sizeof"}
INIT_HINTS = ("init", "open", "create", "new", "setup", "load", "parse", "alloc", "make", "build")
CLEANUP_HINTS = ("free", "delete", "close", "destroy", "release", "cleanup", "dispose")
PARSER_HINTS = ("parse", "decode", "deserialize", "read", "load", "scan")
STRING_HINTS = ("string", "str", "text", "json", "buf", "buffer", "data", "raw", "input")
LENGTH_HINTS = ("len", "length", "size", "count", "idx", "index", "offset", "capacity")
INTEGER_TYPE_HINTS = ("int", "size_t", "long", "short", "ssize_t", "ptrdiff_t", "uint", "int32", "int64")
MEMORY_FUNCTION_HINTS = ("malloc", "calloc", "realloc", "free", "memcpy", "memmove", "strcpy", "strncpy")
NON_RESOURCE_POINTER_BASES = {"char", "void", "uint8_t", "int8_t", "byte"}


@dataclass
class ApiSpec:
    api: str
    ret: str
    args: list[str]
    arg_names: list[str]
    arg_types: list[str]
    header: str


@dataclass
class CallGraphEntry:
    api: str
    callers: list[str]
    callees: list[str]
    neighbors: list[str]
    init_paths: list[str]
    cleanup_paths: list[str]
    short_call_chains: list[list[str]]
    cg_priority: float
    distance_to_risky_region: float


@dataclass
class RiskProfile:
    api: str
    risk_level: float
    risk_tags: list[str]
    boundary_hints: list[str]
    high_risk_neighbors: list[str]
    history_summary: str


@dataclass
class SeedContext:
    library_name: str
    library_version: str
    api_signature: str
    header: str
    execution_init_path: list[str]
    execution_cleanup_path: list[str]
    execution_neighbor_apis: list[str]
    execution_short_call_chain: list[list[str]]
    risk_level: float
    risk_tags: list[str]
    risk_boundary_hints: list[str]
    risk_history_summary: str
    risk_high_risk_neighbors: list[str]


@dataclass
class LibraryMetadata:
    library_name: str
    library_version: str
    target_root: Path
    cache_dir: Path
    include_dirs: list[Path]
    public_headers: list[Path]
    source_files: list[Path]
    focus_files: list[Path]
    api_specs: dict[str, ApiSpec]
    call_graph: dict[str, CallGraphEntry]
    risk_profiles: dict[str, RiskProfile]
    function_to_source: dict[str, Path] = field(default_factory=dict)

    @property
    def api_list_path(self) -> Path:
        return self.cache_dir / "apis.txt"

    @property
    def api_defs_path(self) -> Path:
        return self.cache_dir / "api_defs.txt"

    @property
    def api_db_path(self) -> Path:
        return self.cache_dir / "api_db.jsonl"

    @property
    def cg_db_path(self) -> Path:
        return self.cache_dir / "cg_db.jsonl"

    @property
    def risk_db_path(self) -> Path:
        return self.cache_dir / "risk_db.jsonl"

    def get_api_spec(self, api: str) -> ApiSpec | None:
        return self.api_specs.get(api)

    def get_call_graph_entry(self, api: str) -> CallGraphEntry | None:
        return self.call_graph.get(api)

    def get_risk_profile(self, api: str) -> RiskProfile | None:
        return self.risk_profiles.get(api)

    def build_seed_context(self, api: str) -> SeedContext:
        spec = self.api_specs[api]
        cg = self.call_graph.get(api) or CallGraphEntry(
            api=api,
            callers=[],
            callees=[],
            neighbors=[],
            init_paths=[],
            cleanup_paths=[],
            short_call_chains=[],
            cg_priority=0.0,
            distance_to_risky_region=1.0,
        )
        risk = self.risk_profiles.get(api) or RiskProfile(
            api=api,
            risk_level=0.1,
            risk_tags=[],
            boundary_hints=[],
            high_risk_neighbors=[],
            history_summary="",
        )
        signature = "{} {}({})".format(spec.ret, spec.api, ", ".join(spec.args)).strip()
        return SeedContext(
            library_name=self.library_name,
            library_version=self.library_version,
            api_signature=signature,
            header=spec.header,
            execution_init_path=cg.init_paths[:2],
            execution_cleanup_path=cg.cleanup_paths[:2],
            execution_neighbor_apis=cg.neighbors[:5],
            execution_short_call_chain=cg.short_call_chains[:2],
            risk_level=risk.risk_level,
            risk_tags=risk.risk_tags[:5],
            risk_boundary_hints=risk.boundary_hints[:5],
            risk_history_summary=risk.history_summary,
            risk_high_risk_neighbors=risk.high_risk_neighbors[:5],
        )


def _path_has_excluded_part(path: Path) -> bool:
    return any(part.lower() in EXCLUDED_DIR_PARTS for part in path.parts)


def _file_is_excluded(path: Path) -> bool:
    stem = path.stem.lower()
    if any(pattern.search(stem) for pattern in EXCLUDED_FILE_PATTERNS):
        return True
    return _path_has_excluded_part(path.parent)


def _strip_comments_preserving_strings(text: str) -> str:
    output: list[str] = []
    idx = 0
    in_string: str | None = None
    line_comment = False
    block_comment = False
    escape = False
    while idx < len(text):
        char = text[idx]
        nxt = text[idx + 1] if idx + 1 < len(text) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
                output.append(char)
            idx += 1
            continue
        if block_comment:
            if char == "*" and nxt == "/":
                block_comment = False
                idx += 2
                continue
            idx += 1
            continue
        if in_string is not None:
            output.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == in_string:
                in_string = None
            idx += 1
            continue
        if char == "/" and nxt == "/":
            line_comment = True
            idx += 2
            continue
        if char == "/" and nxt == "*":
            block_comment = True
            idx += 2
            continue
        if char in ("'", '"'):
            in_string = char
        output.append(char)
        idx += 1
    return "".join(output)


def _collapse_ws(text: str) -> str:
    return " ".join(text.split())


def _split_top_level(text: str, delimiter: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth_paren = 0
    depth_bracket = 0
    depth_brace = 0
    in_string: str | None = None
    escape = False
    for char in text:
        current.append(char)
        if in_string is not None:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == in_string:
                in_string = None
            continue
        if char in ("'", '"'):
            in_string = char
            continue
        if char == "(":
            depth_paren += 1
        elif char == ")":
            depth_paren = max(0, depth_paren - 1)
        elif char == "[":
            depth_bracket += 1
        elif char == "]":
            depth_bracket = max(0, depth_bracket - 1)
        elif char == "{":
            depth_brace += 1
        elif char == "}":
            depth_brace = max(0, depth_brace - 1)
        elif (
            char == delimiter
            and depth_paren == 0
            and depth_bracket == 0
            and depth_brace == 0
        ):
            parts.append("".join(current[:-1]).strip())
            current = []
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _extract_arg_name(arg_decl: str) -> str:
    decl = arg_decl.strip()
    if not decl or decl == "void":
        return ""
    func_ptr = re.search(r"\(\s*\*\s*([A-Za-z_]\w*)\s*\)", decl)
    if func_ptr:
        return func_ptr.group(1)
    array_decl = re.search(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])+\s*$", decl)
    if array_decl:
        return array_decl.group(1)
    matches = re.findall(r"([A-Za-z_]\w*)", decl)
    if not matches:
        return ""
    reserved = {"const", "volatile", "restrict", "struct", "enum", "union", "unsigned", "signed", "long", "short"}
    for token in reversed(matches):
        if token not in reserved:
            return token
    return matches[-1]


def _extract_arg_type(arg_decl: str, arg_name: str) -> str:
    decl = _collapse_ws(arg_decl)
    if not decl or decl == "void":
        return decl
    if arg_name:
        decl = re.sub(r"\b{}\b".format(re.escape(arg_name)), "", decl, count=1).strip()
    return _collapse_ws(decl.replace(" *", "*").replace("* ", "* "))


def _normalize_decl_type(decl: str) -> str:
    text = decl.strip()
    while True:
        match = re.fullmatch(r"([A-Za-z_]\w*)\s*\((.*)\)", text)
        if match is None:
            break
        macro_name, inner = match.groups()
        if not macro_name.isupper():
            break
        text = inner.strip()
    text = re.sub(r"\b(__declspec|__attribute__)\s*\([^)]*\)", "", text)
    text = re.sub(r"\b[A-Z_][A-Z0-9_]*CALL\b", "", text)
    return _collapse_ws(text.replace(" *", "*").replace("* ", "* "))


def _parse_prototype(statement: str) -> tuple[str, str, str, list[str], list[str], list[str]] | None:
    candidate = _collapse_ws(statement)
    if not candidate or candidate.startswith("#"):
        return None
    lowered = candidate.lower()
    if lowered.startswith(("typedef ", "return ", "case ", "goto ")):
        return None
    if "{" in candidate or "}" in candidate:
        return None
    if "(" not in candidate or ")" not in candidate:
        return None
    match = re.match(r"(?P<ret>.+?)\b(?P<name>[A-Za-z_]\w*)\s*\((?P<args>.*)\)\s*$", candidate.rstrip(";"))
    if match is None:
        return None
    api_name = match.group("name")
    if api_name in CONTROL_KEYWORDS:
        return None
    ret = _normalize_decl_type(match.group("ret").strip())
    if any(token in ret for token in ("static ", "inline ")):
        return None
    args_text = match.group("args").strip()
    arg_specs: list[str] = []
    arg_names: list[str] = []
    arg_types: list[str] = []
    for piece in _split_top_level(args_text, ","):
        arg_decl = piece.strip()
        if not arg_decl or arg_decl == "void":
            continue
        arg_name = _extract_arg_name(arg_decl)
        arg_type = _extract_arg_type(arg_decl, arg_name)
        arg_specs.append(arg_decl)
        arg_names.append(arg_name)
        arg_types.append(arg_type)
    return api_name, ret, candidate.rstrip(";"), arg_specs, arg_names, arg_types


def _looks_like_macro_api(api_name: str) -> bool:
    if api_name in CONTROL_KEYWORDS:
        return True
    upper = api_name.upper()
    if api_name == upper and any(
        token in upper for token in ("EXPORT", "REMOVED", "EXTERN", "IMPORT", "CALL", "DEFINE")
    ):
        return True
    return False


def _filter_header_api_specs(specs: dict[str, ApiSpec]) -> dict[str, ApiSpec]:
    return {api_name: spec for api_name, spec in specs.items() if not _looks_like_macro_api(api_name)}


def _api_spec_from_signature(api_name: str, signature: str, header: str) -> ApiSpec | None:
    candidate = signature.strip()
    if not candidate:
        return None
    if not candidate.endswith(";"):
        candidate += ";"
    parsed = _parse_prototype(candidate)
    if parsed is None:
        return None
    parsed_name, ret, _, arg_specs, arg_names, arg_types = parsed
    resolved_name = api_name or parsed_name
    return ApiSpec(
        api=resolved_name,
        ret=ret,
        args=arg_specs,
        arg_names=arg_names,
        arg_types=arg_types,
        header=header,
    )


def _load_explicit_api_specs(target_root: Path, manifest: dict[str, object]) -> dict[str, ApiSpec]:
    spec_file = manifest.get("api_specs_file")
    if spec_file:
        path = target_root / str(spec_file)
    else:
        path = target_root / "apis.txt"
    if not path.exists():
        return {}

    default_header = "api.h"
    public_headers = manifest.get("public_headers")
    if isinstance(public_headers, list) and public_headers:
        default_header = str(public_headers[0])

    specs: dict[str, ApiSpec] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = [part.strip() for part in stripped.split("\t")]
        api_name: str | None = None
        signature: str | None = None
        header = default_header
        if len(parts) == 1:
            one = parts[0]
            if "(" in one:
                signature = one
                parsed = _parse_prototype(one if one.endswith(";") else one + ";")
                api_name = parsed[0] if parsed is not None else None
            else:
                api_name = one
        elif len(parts) == 2:
            left, right = parts
            if "(" in left and "(" not in right:
                signature = left
                parsed = _parse_prototype(left if left.endswith(";") else left + ";")
                api_name = parsed[0] if parsed is not None else None
                header = right or default_header
            else:
                api_name = left
                signature = right
        else:
            api_name = parts[0]
            signature = parts[1]
            header = parts[2] if parts[2] else default_header
        if not api_name:
            continue
        spec = _api_spec_from_signature(api_name, signature or "", header)
        if spec is not None:
            specs[api_name] = spec
    return specs


def _discover_public_headers(target_root: Path, manifest: dict[str, object]) -> list[Path]:
    headers_override = manifest.get("public_headers")
    if isinstance(headers_override, list) and headers_override:
        return [target_root / str(entry) for entry in headers_override]
    headers = [
        path
        for path in target_root.rglob("*.h")
        if path.is_file() and not _file_is_excluded(path.relative_to(target_root))
    ]
    headers.sort()
    return headers


def _discover_source_files(target_root: Path, manifest: dict[str, object]) -> list[Path]:
    sources_override = manifest.get("sources")
    if isinstance(sources_override, list) and sources_override:
        return [target_root / str(entry) for entry in sources_override]
    sources = [
        path
        for path in target_root.rglob("*.c")
        if path.is_file() and not _file_is_excluded(path.relative_to(target_root))
    ]
    sources.sort()
    return sources


def _discover_include_dirs(target_root: Path, headers: Iterable[Path], manifest: dict[str, object]) -> list[Path]:
    include_override = manifest.get("include_dirs")
    if isinstance(include_override, list) and include_override:
        paths = [target_root / str(entry) for entry in include_override]
    else:
        paths = [target_root] + [header.parent for header in headers]
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _load_manifest(target_root: Path) -> dict[str, object]:
    for name in ("ralfuzz.target.json", "ralfuzz_target.json"):
        path = target_root / name
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _collect_api_specs(target_root: Path, headers: list[Path]) -> dict[str, ApiSpec]:
    specs: dict[str, ApiSpec] = {}
    for header in headers:
        raw_text = _strip_comments_preserving_strings(header.read_text(encoding="utf-8", errors="replace"))
        lines: list[str] = []
        skip_macro_continuation = False
        for line in raw_text.splitlines():
            stripped = line.lstrip()
            if skip_macro_continuation:
                skip_macro_continuation = stripped.endswith("\\")
                continue
            if stripped.startswith("#"):
                skip_macro_continuation = stripped.endswith("\\")
                continue
            lines.append(line)
        text = "\n".join(lines)
        for statement in text.split(";"):
            parsed = _parse_prototype(statement)
            if parsed is None:
                continue
            api_name, ret, _, arg_specs, arg_names, arg_types = parsed
            relative_header = header.relative_to(target_root).as_posix()
            specs.setdefault(
                api_name,
                ApiSpec(
                    api=api_name,
                    ret=ret,
                    args=arg_specs,
                    arg_names=arg_names,
                    arg_types=arg_types,
                    header=relative_header,
                ),
            )
    return specs


def _find_matching_brace(text: str, open_idx: int) -> int:
    depth = 0
    in_string: str | None = None
    escape = False
    idx = open_idx
    while idx < len(text):
        char = text[idx]
        if in_string is not None:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == in_string:
                in_string = None
            idx += 1
            continue
        if char in ("'", '"'):
            in_string = char
            idx += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return idx
        idx += 1
    return -1


def _parse_function_definitions(source_path: Path) -> dict[str, str]:
    text = _strip_comments_preserving_strings(source_path.read_text(encoding="utf-8", errors="replace"))
    definitions: dict[str, str] = {}
    depth = 0
    last_separator = 0
    idx = 0
    in_string: str | None = None
    escape = False
    while idx < len(text):
        char = text[idx]
        if in_string is not None:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == in_string:
                in_string = None
            idx += 1
            continue
        if char in ("'", '"'):
            in_string = char
            idx += 1
            continue
        if char == "{":
            if depth == 0:
                signature = _collapse_ws(text[last_separator:idx])
                parsed = _parse_prototype(signature)
                if parsed is not None:
                    api_name, _, _, _, _, _ = parsed
                    body_end = _find_matching_brace(text, idx)
                    if body_end > idx:
                        definitions[api_name] = text[idx + 1 : body_end]
                        idx = body_end
                        last_separator = body_end + 1
                        depth = 0
                        idx += 1
                        continue
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
            if depth == 0:
                last_separator = idx + 1
        elif char == ";" and depth == 0:
            last_separator = idx + 1
        idx += 1
    return definitions


def _build_call_graph(
    api_specs: dict[str, ApiSpec],
    source_files: list[Path],
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, Path]]:
    definitions: dict[str, str] = {}
    function_to_source: dict[str, Path] = {}
    for source_file in source_files:
        for func_name, body in _parse_function_definitions(source_file).items():
            definitions[func_name] = body
            function_to_source[func_name] = source_file

    known_names = sorted(set(api_specs) | set(definitions), key=len, reverse=True)
    forward: dict[str, set[str]] = {name: set() for name in known_names}
    reverse: dict[str, set[str]] = {name: set() for name in known_names}
    for func_name, body in definitions.items():
        for span in find_call_spans(body, known_names):
            callee = span.api_name
            if callee == func_name:
                continue
            forward.setdefault(func_name, set()).add(callee)
            reverse.setdefault(callee, set()).add(func_name)
    return forward, reverse, function_to_source


def _choose_type_builders(api_specs: dict[str, ApiSpec]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    init_builders: dict[str, list[str]] = {}
    cleanup_builders: dict[str, list[str]] = {}
    for api_name, spec in api_specs.items():
        api_lower = api_name.lower()
        ret_base = _base_type(spec.ret)
        if ret_base and "*" in spec.ret and any(hint in api_lower for hint in INIT_HINTS):
            init_builders.setdefault(ret_base, []).append(api_name)
        if any(hint in api_lower for hint in CLEANUP_HINTS):
            if len(spec.arg_types) == 1 and "*" in spec.arg_types[0]:
                cleanup_builders.setdefault(_base_type(spec.arg_types[0]), []).append(api_name)
    return init_builders, cleanup_builders


def _base_type(type_decl: str) -> str:
    text = type_decl.replace("*", " ")
    tokens = [
        token
        for token in re.findall(r"[A-Za-z_]\w*", text)
        if token
        not in {
            "const",
            "volatile",
            "restrict",
            "struct",
            "enum",
            "union",
            "unsigned",
            "signed",
            "long",
            "short",
        }
    ]
    if not tokens:
        return ""
    return tokens[-1]


def _is_pointer_type(type_decl: str) -> bool:
    return "*" in type_decl or bool(re.search(r"\[[^\]]*\]", type_decl))


def _is_const_type(type_decl: str) -> bool:
    return bool(re.search(r"\bconst\b", type_decl))


def _looks_accessor_api(name: str) -> bool:
    lower = (name or "").lower()
    return lower.startswith(("get", "set", "has", "is")) or "_get" in lower or "_set" in lower or "errorptr" in lower


def _extract_version(target_root: Path) -> str:
    for filename in ("VERSION", "version.txt", "VERSION.txt"):
        path = target_root / filename
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace").strip()
    readme_candidates = [target_root / "README.md", target_root / "README", target_root / "readme.md"]
    version_re = re.compile(r"\b\d+\.\d+(?:\.\d+)?\b")
    for path in readme_candidates:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        match = version_re.search(text)
        if match:
            return match.group(0)
    return "unknown"


def _collect_short_call_chains(api_name: str, forward: dict[str, set[str]], max_depth: int = 3) -> list[list[str]]:
    chains: list[list[str]] = []

    def dfs(path: list[str], depth: int) -> None:
        current = path[-1]
        if depth >= max_depth:
            chains.append(path.copy())
            return
        next_nodes = sorted(forward.get(current, set()))
        if not next_nodes:
            chains.append(path.copy())
            return
        for next_node in next_nodes[:3]:
            if next_node in path:
                continue
            dfs(path + [next_node], depth + 1)
            if len(chains) >= 4:
                return

    dfs([api_name], 1)
    deduped: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for chain in chains:
        key = tuple(chain)
        if key not in seen and len(chain) > 1:
            seen.add(key)
            deduped.append(chain)
        if len(deduped) >= 2:
            break
    return deduped


def _infer_api_tags(spec: ApiSpec, body: str) -> tuple[list[str], list[str]]:
    tags: list[str] = []
    hints: list[str] = []
    joined = " ".join(
        [spec.api, spec.ret] + spec.args + [body.lower()]
    ).lower()

    if any(hint in joined for hint in PARSER_HINTS):
        tags.extend(["parser", "input-sensitive"])
        hints.extend(["empty", "malformed-input", "truncated"])
    if any(hint in joined for hint in LENGTH_HINTS):
        tags.extend(["length-sensitive", "integer-sensitive"])
        hints.extend(["oversized", "mismatch", "max", "min"])
    if "*" in spec.ret or any("*" in arg_type for arg_type in spec.arg_types):
        tags.append("pointer-sensitive")
        hints.append("null")
    if any(hint in joined for hint in CLEANUP_HINTS):
        tags.extend(["memory-management", "cleanup-sensitive"])
        hints.extend(["repeated free", "use-after-free"])
    if any(hint in joined for hint in MEMORY_FUNCTION_HINTS):
        tags.append("memory-management")
        hints.extend(["oversized", "null"])
    if any(hint in joined for hint in STRING_HINTS):
        hints.extend(["empty", "unterminated"])
    if any(type_hint in " ".join(spec.arg_types).lower() for type_hint in INTEGER_TYPE_HINTS):
        tags.append("integer-sensitive")
        hints.extend(["negative", "max", "min"])

    dedup_tags = list(dict.fromkeys(tag for tag in tags if tag))
    dedup_hints = list(dict.fromkeys(hint for hint in hints if hint))
    return dedup_tags[:6], dedup_hints[:6]


def _build_risk_profiles(
    api_specs: dict[str, ApiSpec],
    forward: dict[str, set[str]],
    cg_scores: dict[str, float],
    definitions: dict[str, str],
) -> dict[str, RiskProfile]:
    profiles: dict[str, RiskProfile] = {}
    raw_scores: dict[str, float] = {}
    api_tags: dict[str, list[str]] = {}
    api_hints: dict[str, list[str]] = {}
    for api_name, spec in api_specs.items():
        tags, hints = _infer_api_tags(spec, definitions.get(api_name, ""))
        api_tags[api_name] = tags
        api_hints[api_name] = hints
        score = 0.15
        if "parser" in tags:
            score += 0.18
        if "length-sensitive" in tags:
            score += 0.18
        if "memory-management" in tags:
            score += 0.15
        if "cleanup-sensitive" in tags:
            score += 0.1
        if "pointer-sensitive" in tags:
            score += 0.08
        if "integer-sensitive" in tags:
            score += 0.08
        score += 0.25 * cg_scores.get(api_name, 0.0)
        raw_scores[api_name] = min(0.99, score)

    for api_name in api_specs:
        neighbors = sorted(forward.get(api_name, set()))
        high_risk_neighbors = sorted(
            neighbors,
            key=lambda name: raw_scores.get(name, 0.0),
            reverse=True,
        )[:4]
        tags = api_tags.get(api_name, [])
        hints = api_hints.get(api_name, [])
        summary_parts = []
        if tags:
            summary_parts.append("Heuristics mark this API as {}.".format(", ".join(tags[:3])))
        if hints:
            summary_parts.append("Prioritize {} inputs.".format(", ".join(hints[:4])))
        profiles[api_name] = RiskProfile(
            api=api_name,
            risk_level=round(raw_scores.get(api_name, 0.1), 4),
            risk_tags=tags,
            boundary_hints=hints,
            high_risk_neighbors=high_risk_neighbors,
            history_summary=" ".join(summary_parts).strip(),
        )
    return profiles


def _bfs_distance(start: str, graph: dict[str, set[str]], targets: set[str]) -> int:
    if start in targets:
        return 0
    frontier = [(start, 0)]
    seen = {start}
    while frontier:
        current, dist = frontier.pop(0)
        for nxt in graph.get(current, set()):
            if nxt in seen:
                continue
            if nxt in targets:
                return dist + 1
            seen.add(nxt)
            frontier.append((nxt, dist + 1))
    return 3


def _build_call_graph_entries(
    api_specs: dict[str, ApiSpec],
    forward: dict[str, set[str]],
    reverse: dict[str, set[str]],
    cg_scores: dict[str, float],
    risk_profiles: dict[str, RiskProfile],
) -> dict[str, CallGraphEntry]:
    init_builders, cleanup_builders = _choose_type_builders(api_specs)
    risky_apis = {
        api_name
        for api_name, profile in risk_profiles.items()
        if profile.risk_level >= 0.65
    }
    undirected: dict[str, set[str]] = {}
    for node in set(forward) | set(reverse):
        undirected[node] = set(forward.get(node, set())) | set(reverse.get(node, set()))

    entries: dict[str, CallGraphEntry] = {}
    for api_name, spec in api_specs.items():
        callers = sorted(reverse.get(api_name, set()))
        callees = sorted(forward.get(api_name, set()))
        neighbors = list(dict.fromkeys(callers + callees))[:8]
        arg_base_types = [_base_type(arg_type) for arg_type in spec.arg_types]
        init_paths: list[str] = []
        for base_type in arg_base_types:
            for builder in init_builders.get(base_type, []):
                if builder != api_name:
                    init_paths.append("{}(...)".format(builder))
        cleanup_paths: list[str] = []
        if not any(hint in api_name.lower() for hint in CLEANUP_HINTS):
            ret_base = _base_type(spec.ret)
            if ret_base and _is_pointer_type(spec.ret) and not _looks_accessor_api(api_name):
                exact_ret_cleanup = cleanup_builders.get(ret_base, [])
                ret_cleanup = exact_ret_cleanup
                if (not ret_cleanup) and (not _is_const_type(spec.ret)):
                    ret_cleanup = cleanup_builders.get("void", [])
                for cleanup_api in ret_cleanup:
                    if cleanup_api != api_name:
                        cleanup_paths.append("{}(...)".format(cleanup_api))

            resource_arg_bases = [
                base_type
                for arg_type, base_type in zip(spec.arg_types, arg_base_types)
                if _is_pointer_type(arg_type)
                and base_type
                and base_type not in NON_RESOURCE_POINTER_BASES
                and init_builders.get(base_type)
            ]
            for base_type in resource_arg_bases:
                for cleanup_api in cleanup_builders.get(base_type, []):
                    if cleanup_api != api_name:
                        cleanup_paths.append("{}(...)".format(cleanup_api))
        entries[api_name] = CallGraphEntry(
            api=api_name,
            callers=callers,
            callees=callees,
            neighbors=neighbors,
            init_paths=list(dict.fromkeys(init_paths))[:2],
            cleanup_paths=list(dict.fromkeys(cleanup_paths))[:2],
            short_call_chains=_collect_short_call_chains(api_name, forward),
            cg_priority=round(cg_scores.get(api_name, 0.0), 4),
            distance_to_risky_region=float(_bfs_distance(api_name, undirected, risky_apis)) if risky_apis else 3.0,
        )
    return entries


def _compute_cg_scores(api_specs: dict[str, ApiSpec], forward: dict[str, set[str]], reverse: dict[str, set[str]]) -> dict[str, float]:
    raw_scores: dict[str, float] = {}
    max_score = 1.0
    for api_name in api_specs:
        degree = len(forward.get(api_name, set())) + len(reverse.get(api_name, set()))
        two_hop = set()
        for neighbor in forward.get(api_name, set()) | reverse.get(api_name, set()):
            two_hop |= forward.get(neighbor, set()) | reverse.get(neighbor, set())
        raw = degree + 0.5 * len(two_hop)
        raw_scores[api_name] = raw
        max_score = max(max_score, raw)
    return {api_name: round(score / max_score, 4) for api_name, score in raw_scores.items()}


def _write_jsonl(path: Path, rows: Iterable[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_compat_files(metadata: LibraryMetadata) -> None:
    metadata.cache_dir.mkdir(parents=True, exist_ok=True)
    with metadata.api_list_path.open("w", encoding="utf-8") as handle:
        for api_name in sorted(metadata.api_specs):
            handle.write(api_name + "\n")
    with metadata.api_defs_path.open("w", encoding="utf-8") as handle:
        for api_name in sorted(metadata.api_specs):
            spec = metadata.api_specs[api_name]
            args = ", ".join(name or "arg{}".format(idx) for idx, name in enumerate(spec.arg_names))
            handle.write("{}({})\n".format(api_name, args))
    _write_jsonl(metadata.api_db_path, (asdict(spec) for spec in metadata.api_specs.values()))
    _write_jsonl(metadata.cg_db_path, (asdict(entry) for entry in metadata.call_graph.values()))
    _write_jsonl(metadata.risk_db_path, (asdict(profile) for profile in metadata.risk_profiles.values()))


def build_library_metadata(
    package_root: str | Path,
    target_root: str | Path,
    library_name: str | None = None,
) -> LibraryMetadata:
    package_root = Path(package_root).resolve()
    target_root = Path(target_root).resolve()
    manifest = _load_manifest(target_root)
    public_headers = _discover_public_headers(target_root, manifest)
    source_files = _discover_source_files(target_root, manifest)
    include_dirs = _discover_include_dirs(target_root, public_headers, manifest)
    header_specs = _filter_header_api_specs(_collect_api_specs(target_root, public_headers))
    explicit_specs = _load_explicit_api_specs(target_root, manifest)
    api_specs = {**header_specs, **explicit_specs}
    forward, reverse, function_to_source = _build_call_graph(api_specs, source_files)
    cg_scores = _compute_cg_scores(api_specs, forward, reverse)

    definitions: dict[str, str] = {}
    for source_file in source_files:
        definitions.update(_parse_function_definitions(source_file))

    risk_profiles = _build_risk_profiles(api_specs, forward, cg_scores, definitions)
    call_graph = _build_call_graph_entries(api_specs, forward, reverse, cg_scores, risk_profiles)

    focus_override = manifest.get("focus_files")
    if isinstance(focus_override, list) and focus_override:
        focus_files = [target_root / str(entry) for entry in focus_override]
    else:
        api_sources = {
            function_to_source[api_name]
            for api_name in api_specs
            if api_name in function_to_source
        }
        focus_files = sorted(api_sources) if api_sources else source_files

    root_hash = hashlib.sha1(str(target_root).encode("utf-8")).hexdigest()[:10]
    cache_name = "{}_{}".format(re.sub(r"[^A-Za-z0-9_]+", "_", target_root.name) or "target", root_hash)
    cache_root_override = os.environ.get("RALFUZZ_CACHE_ROOT", "").strip()
    if cache_root_override:
        cache_dir = Path(cache_root_override).resolve() / "targets" / cache_name
    else:
        cache_dir = package_root / ".cache" / "targets" / cache_name
    metadata = LibraryMetadata(
        library_name=library_name or str(manifest.get("name") or target_root.name),
        library_version=str(manifest.get("version") or _extract_version(target_root)),
        target_root=target_root,
        cache_dir=cache_dir,
        include_dirs=include_dirs,
        public_headers=public_headers,
        source_files=source_files,
        focus_files=focus_files,
        api_specs=api_specs,
        call_graph=call_graph,
        risk_profiles=risk_profiles,
        function_to_source=function_to_source,
    )
    _write_compat_files(metadata)
    return metadata


def render_seed_context(
    seed_context: SeedContext,
    *,
    include_execution_context: bool = True,
    include_risk_context: bool = True,
    signature_only: bool = False,
) -> str:
    if signature_only:
        return "\n".join(
            [
                "Library: {} ({})".format(seed_context.library_name, seed_context.library_version),
                "Target signature: {}".format(seed_context.api_signature),
                'Primary header: #include "{}"'.format(seed_context.header),
            ]
        )
    lines = [
        "Library: {} ({})".format(seed_context.library_name, seed_context.library_version),
        "Target signature: {}".format(seed_context.api_signature),
        'Primary header: #include "{}"'.format(seed_context.header),
    ]
    if include_execution_context:
        if seed_context.execution_init_path:
            lines.append("Init candidates: {}".format(", ".join(seed_context.execution_init_path)))
        if seed_context.execution_cleanup_path:
            lines.append("Cleanup candidates: {}".format(", ".join(seed_context.execution_cleanup_path)))
        if seed_context.execution_neighbor_apis:
            lines.append("Neighbor APIs: {}".format(", ".join(seed_context.execution_neighbor_apis)))
        if seed_context.execution_short_call_chain:
            chain_str = [" -> ".join(chain) for chain in seed_context.execution_short_call_chain]
            lines.append("Short call chains: {}".format("; ".join(chain_str)))
    if include_risk_context:
        lines.append("Risk level: {:.2f}".format(seed_context.risk_level))
        if seed_context.risk_tags:
            lines.append("Risk tags: {}".format(", ".join(seed_context.risk_tags)))
        if seed_context.risk_boundary_hints:
            lines.append("Boundary hints: {}".format(", ".join(seed_context.risk_boundary_hints)))
        if seed_context.risk_high_risk_neighbors:
            lines.append("High-risk neighbors: {}".format(", ".join(seed_context.risk_high_risk_neighbors)))
        if seed_context.risk_history_summary:
            lines.append("Risk summary: {}".format(seed_context.risk_history_summary))
    return "\n".join(lines)


def score_distance(distance: float) -> float:
    return 1.0 / (1.0 + max(distance, 0.0))


def stable_softmax(values: list[float]) -> list[float]:
    if not values:
        return []
    max_value = max(values)
    exps = [math.exp(value - max_value) for value in values]
    total = sum(exps) or 1.0
    return [value / total for value in exps]
