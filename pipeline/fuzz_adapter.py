from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_MAX_FUZZ_INPUT = 1 << 20


@dataclass(frozen=True)
class FuzzProfile:
    api_name: str
    kind: str = "direct_api"
    includes: tuple[str, ...] = ()
    extra_system_includes: tuple[str, ...] = ("stdint.h", "stdlib.h", "string.h")
    cleanup_call: str | None = None
    skip_empty_input: bool = False


@dataclass(frozen=True)
class ApiParameter:
    type_name: str
    name: str


@dataclass(frozen=True)
class ApiSignature:
    api_name: str
    return_type: str
    params: tuple[ApiParameter, ...]
    header: str


@dataclass(frozen=True)
class CleanupHint:
    function: str
    style: str
    suffix_args: str = ""


BUILTIN_SIGNATURES: dict[str, ApiSignature] = {
    "cJSON_Parse": ApiSignature(
        api_name="cJSON_Parse",
        return_type="cJSON *",
        params=(ApiParameter("const char *", "value"),),
        header="cJSON.h",
    ),
}


def load_fuzz_profile(api_name: str, api_dir: Path) -> FuzzProfile:
    profile_path = api_dir / "ralfuzz.fuzz.json"
    if profile_path.is_file():
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
        entry = payload.get(api_name) or payload.get("default")
        if isinstance(entry, dict):
            return FuzzProfile(
                api_name=api_name,
                kind=str(entry.get("kind", "direct_api")),
                includes=tuple(entry.get("includes") or []),
                extra_system_includes=tuple(entry.get("extra_system_includes") or ("stdint.h", "stdlib.h", "string.h")),
                cleanup_call=entry.get("cleanup_call"),
                skip_empty_input=bool(entry.get("skip_empty_input", False)),
            )
    return FuzzProfile(api_name=api_name)


def load_api_signature(api_name: str, api_dir: Path) -> ApiSignature:
    for spec_path in _candidate_api_spec_files(api_dir):
        if not spec_path.is_file():
            continue
        for raw_line in spec_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [part.strip() for part in line.split("\t")]
            if len(parts) >= 3 and parts[0] == api_name:
                parsed = parse_c_signature(api_name, parts[1], parts[2])
                if parsed:
                    return parsed
    if api_name in BUILTIN_SIGNATURES:
        return BUILTIN_SIGNATURES[api_name]
    raise KeyError(f"no API signature for api={api_name!r}; add apis.txt or BUILTIN_SIGNATURES")


def _candidate_api_spec_files(api_dir: Path) -> list[Path]:
    manifest_path = api_dir / "ralfuzz.target.json"
    paths: list[Path] = []
    if manifest_path.is_file():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        spec_file = payload.get("api_specs_file")
        if isinstance(spec_file, str):
            paths.append(api_dir / spec_file)
    paths.append(api_dir / "apis.txt")
    return paths


def parse_c_signature(api_name: str, signature: str, header: str) -> ApiSignature | None:
    text = " ".join(signature.strip().rstrip(";").split())
    match = re.match(rf"(?P<ret>.+?)\s+\*?\s*{re.escape(api_name)}\s*\((?P<params>.*)\)$", text)
    if not match:
        return None
    prefix = text[: text.index(api_name)].strip()
    return_type = prefix.rstrip().strip()
    params_text = match.group("params").strip()
    params: list[ApiParameter] = []
    if params_text and params_text != "void":
        for index, piece in enumerate(_split_params(params_text), start=1):
            params.append(_parse_param(piece, index))
    return ApiSignature(api_name=api_name, return_type=return_type, params=tuple(params), header=header)


def _split_params(text: str) -> list[str]:
    params: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            params.append(text[start:index].strip())
            start = index + 1
    params.append(text[start:].strip())
    return [param for param in params if param]


def _parse_param(text: str, index: int) -> ApiParameter:
    cleaned = text.strip()
    if "(*" in cleaned:
        name_match = re.search(r"\(\s*\*\s*(\w+)\s*\)", cleaned)
        name = name_match.group(1) if name_match else f"arg{index}"
        return ApiParameter(cleaned, name)
    match = re.match(r"(?P<type>.+?)(?P<name>[A-Za-z_]\w*)(?:\[[^\]]*\])?$", cleaned)
    if not match:
        return ApiParameter(cleaned, f"arg{index}")
    type_name = match.group("type").strip()
    name = match.group("name").strip()
    return ApiParameter(type_name, name)


def list_source_harnesses(valid_dir: Path, api_name: str) -> list[Path]:
    if not valid_dir.is_dir():
        return []
    return sorted(path for path in valid_dir.glob(f"{api_name}_*.c") if path.is_file())


def _include_block(profile: FuzzProfile, signature: ApiSignature) -> str:
    lines = [f'#include <{name}>' for name in profile.extra_system_includes]
    includes = profile.includes or (signature.header,)
    lines.extend(f'#include "{name}"' for name in includes)
    return "\n".join(lines)


def _libfuzzer_body(profile: FuzzProfile, signature: ApiSignature, source_code: str) -> str:
    cleanup = _cleanup_hint(profile, signature, source_code)
    setup_lines, arg_exprs, cleanup_lines = _synthesize_arguments(signature)
    call_lines = _call_lines(signature, arg_exprs, cleanup)
    body_lines = [
        "extern int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {",
    ]
    if profile.skip_empty_input:
        body_lines.extend(
            [
                "    if (size == 0) {",
                "        return 0;",
                "    }",
            ]
        )
    body_lines.extend(
        [
        f"    if (size > {DEFAULT_MAX_FUZZ_INPUT}) {{",
        "        return 0;",
        "    }",
        "    uint32_t ralfuzz_u32 = 0;",
        "    size_t ralfuzz_int_bytes = size < sizeof(ralfuzz_u32) ? size : sizeof(ralfuzz_u32);",
        "    if (ralfuzz_int_bytes > 0) {",
        "        memcpy(&ralfuzz_u32, data, ralfuzz_int_bytes);",
        "    }",
        "    int ralfuzz_i32 = (int)ralfuzz_u32;",
        "    size_t ralfuzz_size = size;",
        "    char *ralfuzz_cstr = (char *)malloc(size + 1);",
        "    if (!ralfuzz_cstr) {",
        "        return 0;",
        "    }",
        "    if (size > 0) {",
        "        memcpy(ralfuzz_cstr, data, size);",
        "    }",
        "    ralfuzz_cstr[size] = '\\0';",
        "    unsigned char *ralfuzz_bytes = (unsigned char *)malloc(size ? size : 1);",
        "    if (!ralfuzz_bytes) {",
        "        free(ralfuzz_cstr);",
        "        return 0;",
        "    }",
        "    if (size > 0) {",
        "        memcpy(ralfuzz_bytes, data, size);",
        "    }",
        *setup_lines,
        *call_lines,
        *cleanup_lines,
        "    free(ralfuzz_bytes);",
        "    free(ralfuzz_cstr);",
        "    return 0;",
        "}",
        ]
    )
    return "\n".join(body_lines)


def _synthesize_arguments(signature: ApiSignature) -> tuple[list[str], list[str], list[str]]:
    setup: list[str] = []
    args: list[str] = []
    cleanup: list[str] = []
    for index, param in enumerate(signature.params, start=1):
        expr, setup_lines, cleanup_lines = _arg_expr(param, index)
        setup.extend(setup_lines)
        cleanup.extend(cleanup_lines)
        args.append(expr)
    return setup, args, cleanup


def _arg_expr(param: ApiParameter, index: int) -> tuple[str, list[str], list[str]]:
    type_name = _normalize_type(param.type_name)
    name = param.name.lower()
    cast_type = _cast_type(param.type_name)
    if "(*" in param.type_name:
        return "NULL", [], []
    if _is_pointer(type_name):
        if "ucl_parser" in type_name and "parser" in name:
            var = "ralfuzz_ucl_parser"
            setup = [
                f"    struct ucl_parser *{var} = ucl_parser_new(0);",
                f"    if (!{var}) {{",
                "        free(ralfuzz_bytes);",
                "        free(ralfuzz_cstr);",
                "        return 0;",
                "    }",
            ]
            cleanup = [f"    ucl_parser_free({var});"]
            return var, setup, cleanup
        if type_name.strip() in {"pool *", "pool*"} or ("pool" in type_name and name in {"p", "pool"}):
            var = "ralfuzz_pool"
            setup = [
                "    init_json();",
                f"    pool *{var} = make_sub_pool(NULL);",
                f"    if (!{var}) {{",
                "        free(ralfuzz_bytes);",
                "        free(ralfuzz_cstr);",
                "        return 0;",
                "    }",
            ]
            cleanup = ["    finish_json();", f"    destroy_pool({var});"]
            return var, setup, cleanup
        if "pj_pool_t" in type_name and "pool" in name:
            var = "ralfuzz_pj_pool"
            setup = [
                "    pj_caching_pool ralfuzz_caching_pool;",
                "    pj_init();",
                "    pj_caching_pool_init(&ralfuzz_caching_pool, &pj_pool_factory_default_policy, 0);",
                f"    pj_pool_t *{var} = pj_pool_create(&ralfuzz_caching_pool.factory, \"ralfuzz\", 256, 256, NULL);",
                f"    if (!{var}) {{",
                "        pj_caching_pool_destroy(&ralfuzz_caching_pool);",
                "        free(ralfuzz_bytes);",
                "        free(ralfuzz_cstr);",
                "        return 0;",
                "    }",
            ]
            cleanup = [
                f"    pj_pool_release({var});",
                "    pj_caching_pool_destroy(&ralfuzz_caching_pool);",
            ]
            return var, setup, cleanup
        if "pj_json_err_info" in type_name:
            var = "ralfuzz_json_err"
            return f"&{var}", [f"    pj_json_err_info {var};"], []
        if type_name.strip() in {"lua_state *", "lua_state*"}:
            var = "ralfuzz_lua_state"
            setup = [
                f"    lua_State *{var} = luaL_newstate();",
                f"    if (!{var}) {{",
                "        free(ralfuzz_bytes);",
                "        free(ralfuzz_cstr);",
                "        return 0;",
                "    }",
            ]
            cleanup = [f"    lua_close({var});"]
            return var, setup, cleanup
        if _is_string_pointer(type_name):
            if name == "mode":
                return "NULL", [], []
            if name in {"name", "chunkname", "chunk_name"}:
                return '"ralfuzz"', [], []
            return f"({cast_type})ralfuzz_cstr", [], []
        if _is_length_pointer(type_name, name):
            var = f"ralfuzz_len_{index}"
            return f"&{var}", [f"    {param.type_name.rstrip('*').strip()} {var} = ({param.type_name.rstrip('*').strip()})(size ? size : 1);"], []
        if _looks_output_pointer(type_name, name):
            var = f"ralfuzz_out_{index}"
            len_var = f"ralfuzz_out_len_{index}"
            setup = [
                f"    size_t {len_var} = size < 1024 ? 1024 : size * 2 + 1024;",
                f"    unsigned char *{var} = (unsigned char *)malloc({len_var});",
                f"    if (!{var}) {{",
                "        free(ralfuzz_bytes);",
                "        free(ralfuzz_cstr);",
                "        return 0;",
                "    }",
            ]
            return f"({cast_type}){var}", setup, [f"    free({var});"]
        if _is_byte_pointer(type_name) or "void" in type_name:
            return f"({cast_type})ralfuzz_bytes", [], []
        return "NULL", [], []
    if _looks_length_scalar(type_name, name):
        return f"({param.type_name})ralfuzz_size", [], []
    if _looks_integer_scalar(type_name):
        return f"({param.type_name})ralfuzz_i32", [], []
    if _looks_float_scalar(type_name):
        return f"({param.type_name})(ralfuzz_i32 / 1024.0)", [], []
    return f"({param.type_name})0", [], []


def _call_lines(signature: ApiSignature, arg_exprs: list[str], cleanup: CleanupHint | None) -> list[str]:
    args = ", ".join(arg_exprs)
    return_type = _normalize_type(signature.return_type)
    if return_type == "void":
        return [f"    {signature.api_name}({args});"]
    lines = [f"    {signature.return_type} ralfuzz_ret = {signature.api_name}({args});"]
    if cleanup:
        has_truthy_return = _can_use_as_condition(return_type)
        if cleanup.style == "address":
            if has_truthy_return:
                lines.extend(
                    [
                        "    if (ralfuzz_ret) {",
                        f"        {signature.return_type} ralfuzz_ret_cleanup = ralfuzz_ret;",
                        f"        {cleanup.function}(&ralfuzz_ret_cleanup{cleanup.suffix_args});",
                        "    }",
                    ]
                )
            else:
                lines.extend(
                    [
                        f"    {signature.return_type} ralfuzz_ret_cleanup = ralfuzz_ret;",
                        f"    {cleanup.function}(&ralfuzz_ret_cleanup{cleanup.suffix_args});",
                    ]
                )
        else:
            if has_truthy_return:
                lines.extend(
                    [
                        "    if (ralfuzz_ret) {",
                        f"        {cleanup.function}(ralfuzz_ret{cleanup.suffix_args});",
                        "    }",
                    ]
                )
            else:
                lines.append(f"    {cleanup.function}(ralfuzz_ret{cleanup.suffix_args});")
    else:
        lines.append("    (void)ralfuzz_ret;")
    return lines


def _cleanup_hint(profile: FuzzProfile, signature: ApiSignature, source_code: str) -> CleanupHint | None:
    if profile.cleanup_call:
        return CleanupHint(profile.cleanup_call, "value")
    assigned = re.search(
        rf"(?P<var>[A-Za-z_]\w*)\s*=\s*{re.escape(signature.api_name)}\s*\(",
        source_code,
    )
    if not assigned:
        return None
    var = assigned.group("var")
    for match in re.finditer(rf"(?P<fn>[A-Za-z_]\w*)\s*\(\s*(?P<addr>&)?\s*{re.escape(var)}\s*(?P<suffix>,[^;]*)?\)\s*;", source_code):
        fn = match.group("fn")
        if fn == signature.api_name:
            continue
        suffix = match.group("suffix") or ""
        style = "address" if match.group("addr") else "value"
        return CleanupHint(fn, style, suffix)
    return None


def _normalize_type(type_name: str) -> str:
    return " ".join(type_name.replace("const", " const ").replace("*", " * ").split()).lower()


def _cast_type(type_name: str) -> str:
    return " ".join(type_name.split())


def _is_pointer(type_name: str) -> bool:
    return "*" in type_name


def _can_use_as_condition(type_name: str) -> bool:
    return _is_pointer(type_name) or _looks_integer_scalar(type_name) or _looks_float_scalar(type_name)


def _is_string_pointer(type_name: str) -> bool:
    return "char" in type_name and "*" in type_name


def _is_byte_pointer(type_name: str) -> bool:
    byte_words = ("bytef", "uint8_t", "unsigned char", "char", "png_byte", "uint8")
    return any(word in type_name for word in byte_words) and "*" in type_name


def _is_length_pointer(type_name: str, name: str) -> bool:
    if "*" not in type_name:
        return False
    if not any(word in type_name for word in ("size_t", "ulong", "uLong".lower(), "long", "int")):
        return False
    return any(token in name for token in ("len", "size", "nbytes", "count", "capacity"))


def _looks_output_pointer(type_name: str, name: str) -> bool:
    if "*" not in type_name:
        return False
    if "const" in type_name:
        return False
    return any(token in name for token in ("dest", "dst", "out", "output", "buf", "buffer", "result"))


def _looks_length_scalar(type_name: str, name: str) -> bool:
    if "*" in type_name:
        return False
    if not any(token in name for token in ("len", "size", "sz", "nbytes", "count", "capacity")):
        return False
    return _looks_integer_scalar(type_name)


def _looks_integer_scalar(type_name: str) -> bool:
    integer_words = (
        "int",
        "long",
        "short",
        "char",
        "size_t",
        "uLong".lower(),
        "uint",
        "int32",
        "int64",
        "enum",
    )
    return "*" not in type_name and any(word in type_name for word in integer_words)


def _looks_float_scalar(type_name: str) -> bool:
    return "*" not in type_name and any(word in type_name for word in ("float", "double"))


def _afl_body(profile: FuzzProfile, signature: ApiSignature, source_code: str) -> str:
    libfuzzer = _libfuzzer_body(profile, signature, source_code)
    libfuzzer = libfuzzer.replace("extern int LLVMFuzzerTestOneInput", "static int ralfuzz_fuzz_input")
    return f"""
#ifdef __AFL_HAVE_MANUAL_CONTROL
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#else
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#endif

{libfuzzer}

int main(void) {{
#ifdef __AFL_HAVE_MANUAL_CONTROL
    __AFL_FUZZ_INIT();
    unsigned char *data = __AFL_FUZZ_TESTCASE_BUF;
    while (__AFL_LOOP(10000)) {{
        size_t size = __AFL_FUZZ_TESTCASE_LEN;
        (void)ralfuzz_fuzz_input(data, size);
    }}
    return 0;
#else
    static unsigned char data[{DEFAULT_MAX_FUZZ_INPUT}];
    size_t size = fread(data, 1, sizeof(data), stdin);
    return ralfuzz_fuzz_input(data, size);
#endif
}}
""".strip()


def render_fuzz_harness(profile: FuzzProfile, signature: ApiSignature, backend: str, source_code: str) -> str:
    backend = backend.lower()
    header = _include_block(profile, signature)
    if backend == "libfuzzer":
        body = _libfuzzer_body(profile, signature, source_code)
    elif backend == "afl":
        body = _afl_body(profile, signature, source_code)
    else:
        raise ValueError(f"unsupported fuzz backend: {backend}")
    return f"{header}\n\n{body}\n"


def quote_windows(path: Path | str) -> str:
    text = str(path).replace('"', '\\"')
    return f'"{text}"'


def build_compile_command(
    harness_path: Path,
    output_bin: Path,
    compiler: str,
    include_dirs: Iterable[Path],
    source_files: Iterable[Path],
    backend: str,
    enable_sanitizer: bool,
    extra_cflags: str = "",
    extra_ldflags: str = "",
) -> str:
    tokens = [compiler, "-g", "-O1"]
    if extra_cflags.strip():
        tokens.extend(extra_cflags.split())
    if backend == "libfuzzer":
        tokens.append("-fsanitize=fuzzer")
    if enable_sanitizer:
        tokens.extend(["-fsanitize=address", "-fsanitize=undefined"])
    for include_dir in include_dirs:
        tokens.append(f"-I{quote_windows(include_dir)}")
    tokens.append(quote_windows(harness_path))
    for source in source_files:
        tokens.append(quote_windows(source))
    if extra_ldflags.strip():
        tokens.extend(extra_ldflags.split())
    tokens.extend(["-o", quote_windows(output_bin)])
    return " ".join(tokens)


def emit_fuzz_targets(
    *,
    mutation_dir: Path,
    api_dir: Path,
    api_name: str,
    backends: Iterable[str],
    compiler: str = "clang",
    include_dirs: Iterable[Path] | None = None,
    source_files: Iterable[Path] | None = None,
    enable_sanitizer: bool = True,
    per_harness: bool = False,
    extra_cflags: str = "",
    extra_ldflags: str = "",
) -> dict:
    profile = load_fuzz_profile(api_name, api_dir)
    signature = load_api_signature(api_name, api_dir)
    valid_dir = mutation_dir / "valid"
    source_harnesses = list_source_harnesses(valid_dir, api_name)
    if not source_harnesses:
        raise FileNotFoundError(f"no accepted harnesses found under {valid_dir}")

    resolved_include_dirs: list[Path] = []
    target_json = api_dir / "ralfuzz.target.json"
    link_sources: list[Path] = []
    if target_json.is_file():
        payload = json.loads(target_json.read_text(encoding="utf-8"))
        for rel in payload.get("sources", []):
            link_sources.append((api_dir / rel).resolve())
        for rel in payload.get("include_dirs", []):
            resolved_include_dirs.append((api_dir / rel).resolve())
        for header in payload.get("public_headers", []):
            resolved_include_dirs.append((api_dir / header).parent.resolve())
    if not link_sources:
        link_sources = list(source_files or [])
    if not link_sources:
        link_sources = sorted(path.resolve() for path in api_dir.rglob("*.c") if path.is_file())
    if not resolved_include_dirs:
        resolved_include_dirs = list(include_dirs or [api_dir.resolve()])
    resolved_include_dirs = list(dict.fromkeys(path.resolve() for path in resolved_include_dirs))

    emitted: list[dict] = []
    manifest: dict = {
        "api": api_name,
        "profile_kind": profile.kind,
        "api_signature": {
            "return_type": signature.return_type,
            "params": [{"type": param.type_name, "name": param.name} for param in signature.params],
            "header": signature.header,
        },
        "source_harness_count": len(source_harnesses),
        "source_harnesses": [str(path.relative_to(mutation_dir)).replace("\\", "/") for path in source_harnesses],
        "backends": {},
    }

    for backend in backends:
        backend = backend.lower()
        backend_dir = mutation_dir / "fuzz" / backend
        backend_dir.mkdir(parents=True, exist_ok=True)
        backend_entries: list[dict] = []

        targets = source_harnesses if per_harness else [source_harnesses[0]]
        for index, source_path in enumerate(targets):
            suffix = re.sub(r"\.c$", "", source_path.name) if per_harness else api_name
            harness_path = backend_dir / f"{suffix}_fuzzer.c"
            bin_path = backend_dir / f"{suffix}_fuzzer"
            source_code = source_path.read_text(encoding="utf-8", errors="replace")
            harness_path.write_text(render_fuzz_harness(profile, signature, backend, source_code), encoding="utf-8")
            compile_cmd = build_compile_command(
                harness_path,
                bin_path,
                compiler,
                resolved_include_dirs,
                link_sources,
                backend,
                enable_sanitizer,
                extra_cflags=extra_cflags,
                extra_ldflags=extra_ldflags,
            )
            entry = {
                "harness": str(harness_path.relative_to(mutation_dir)).replace("\\", "/"),
                "binary": str(bin_path.relative_to(mutation_dir)).replace("\\", "/"),
                "source_harness": str(source_path.relative_to(mutation_dir)).replace("\\", "/"),
                "compile_cmd": compile_cmd,
            }
            backend_entries.append(entry)
            emitted.append(entry)

        script_name_ps1 = "build_fuzzers.ps1" if backend == "libfuzzer" else f"build_{backend}.ps1"
        script_name_sh = "build_fuzzers.sh" if backend == "libfuzzer" else f"build_{backend}.sh"
        script_path_ps1 = backend_dir / script_name_ps1
        script_path_sh = backend_dir / script_name_sh
        lines_ps1 = ["$ErrorActionPreference = 'Stop'"]
        lines_sh = ["#!/usr/bin/env bash", "set -euo pipefail"]
        for entry in backend_entries:
            lines_ps1.append(entry["compile_cmd"])
            lines_sh.append(entry["compile_cmd"])
        script_path_ps1.write_text("\n".join(lines_ps1) + "\n", encoding="utf-8")
        script_path_sh.write_text("\n".join(lines_sh) + "\n", encoding="utf-8")
        build_script = script_name_sh if os.name != "nt" else script_name_ps1
        manifest["backends"][backend] = {
            "harnesses": backend_entries,
            "build_script": str((backend_dir / build_script).relative_to(mutation_dir)).replace("\\", "/"),
        }

    manifest_path = mutation_dir / "fuzz" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
