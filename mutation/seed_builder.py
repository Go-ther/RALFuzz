from __future__ import annotations

import shutil
from pathlib import Path

from ctitanfuzz.metadata import ApiSpec, LibraryMetadata, _base_type


STANDARD_HEADERS = [
    "#include <stddef.h>",
    "#include <stdint.h>",
    "#include <stdlib.h>",
    "#include <string.h>",
]


def _sanitize_name(name: str) -> str:
    return "".join(char if char.isalnum() or char == "_" else "_" for char in name) or "value"


def _looks_like_string(arg_type: str) -> bool:
    return "char" in arg_type and "*" in arg_type


def _looks_like_string_out(arg_type: str) -> bool:
    return "char" in arg_type and "**" in arg_type


def _looks_like_object_pointer(arg_type: str) -> bool:
    return "*" in arg_type and not _looks_like_string(arg_type)


def _looks_like_bool(arg_type: str, arg_name: str) -> bool:
    lowered = "{} {}".format(arg_type, arg_name).lower()
    return any(token in lowered for token in ("bool", "flag", "enabled", "require", "strict"))


def _looks_like_float(arg_type: str) -> bool:
    lowered = arg_type.lower()
    return "float" in lowered or "double" in lowered


def _looks_like_numeric(arg_type: str) -> bool:
    lowered = arg_type.lower()
    return any(token in lowered for token in ("int", "size_t", "long", "short", "ssize_t", "ptrdiff_t"))


def _guess_input_literal(metadata: LibraryMetadata, spec: ApiSpec) -> str:
    lowered = "{} {}".format(metadata.library_name, spec.api).lower()
    if "json" in lowered or "parse" in lowered:
        return '{"key":1,"name":"seed"}'
    if "xml" in lowered:
        return "<root><item>seed</item></root>"
    if "path" in lowered:
        return "/tmp/ctitanfuzz-seed"
    return "seed-input"


def _find_cleanup_api(metadata: LibraryMetadata, base_type: str) -> str | None:
    exact_any: str | None = None
    fallback_single: str | None = None
    fallback_any: str | None = None
    for api_name, spec in metadata.api_specs.items():
        lowered = api_name.lower()
        if not any(token in lowered for token in ("free", "delete", "destroy", "close", "release", "cleanup")):
            continue
        pointer_args = [arg_type for arg_type in spec.arg_types if "*" in arg_type]
        if not pointer_args:
            continue
        for arg_type in spec.arg_types:
            arg_base = _base_type(arg_type)
            if arg_base == base_type and "*" in arg_type:
                if len(pointer_args) == 1 and len(spec.arg_types) == 1:
                    return api_name
                if len(spec.arg_types) == 1:
                    exact_any = exact_any or api_name
            if arg_base == "void" and "*" in arg_type:
                if len(pointer_args) == 1 and len(spec.arg_types) == 1:
                    fallback_single = fallback_single or api_name
                if len(spec.arg_types) == 1:
                    fallback_any = fallback_any or api_name
    return exact_any or fallback_single or fallback_any


def _find_builder_api(metadata: LibraryMetadata, base_type: str, exclude_api: str) -> str | None:
    candidates: list[str] = []
    for api_name, spec in metadata.api_specs.items():
        if api_name == exclude_api:
            continue
        if _base_type(spec.ret) != base_type or "*" not in spec.ret:
            continue
        lowered = api_name.lower()
        if any(token in lowered for token in ("parse", "create", "new", "init", "open", "load", "make", "build")):
            candidates.append(api_name)
    if candidates:
        candidates.sort(key=lambda name: ("parse" not in name.lower(), len(metadata.api_specs[name].args)))
        return candidates[0]
    return None


def _emit_arg_value(
    metadata: LibraryMetadata,
    target_api: str,
    arg_name: str,
    arg_type: str,
    setup_lines: list[str],
    cleanup_items: list[tuple[str, str]],
    declarations: set[str],
    allow_builders: bool = True,
) -> str:
    safe_name = _sanitize_name(arg_name or "arg")
    lowered_name = safe_name.lower()
    base_type = _base_type(arg_type)

    if _looks_like_string_out(arg_type):
        pointee_type = "const char *" if "const" in arg_type else "char *"
        decl = "{}{}_out = NULL;".format(pointee_type + " ", safe_name)
        if decl not in declarations:
            setup_lines.append("    " + decl)
            declarations.add(decl)
        return "&{}_out".format(safe_name)

    if _looks_like_string(arg_type):
        if "const" in arg_type:
            decl = 'const char *input_text = "{}";'.format(_guess_input_literal(metadata, metadata.api_specs[target_api]).replace('"', '\\"'))
            if decl not in declarations:
                setup_lines.append("    " + decl)
                declarations.add(decl)
            if any(token in lowered_name for token in ("buf", "buffer", "data")) and "input_bytes" not in declarations:
                setup_lines.append("    const unsigned char *input_bytes = (const unsigned char *)input_text;")
                declarations.add("input_bytes")
            return "input_text"
        decl = 'char {}_buf[256] = "{}";'.format(safe_name, _guess_input_literal(metadata, metadata.api_specs[target_api]).replace('"', '\\"'))
        if decl not in declarations:
            setup_lines.append("    " + decl)
            declarations.add(decl)
        return "{}_buf".format(safe_name)

    if _looks_like_bool(arg_type, safe_name):
        return "0"

    if _looks_like_float(arg_type):
        return "1.0"

    if _looks_like_numeric(arg_type):
        if any(token in lowered_name for token in ("len", "length", "size", "count")):
            if "input_length" not in declarations:
                if "const char *input_text = " not in "".join(setup_lines):
                    setup_lines.append('    const char *input_text = "{}";'.format(_guess_input_literal(metadata, metadata.api_specs[target_api]).replace('"', '\\"')))
                setup_lines.append("    size_t input_length = strlen(input_text);")
                declarations.add("input_length")
            if "int" in arg_type.lower() and "size_t" not in arg_type.lower():
                return "(int)input_length"
            return "input_length"
        if "idx" in lowered_name or "index" in lowered_name or "offset" in lowered_name:
            return "0"
        return "1"

    if _looks_like_object_pointer(arg_type):
        if allow_builders:
            builder_api = _find_builder_api(metadata, base_type, target_api)
            if builder_api:
                builder_var = "{}_obj".format(safe_name)
                builder_lines, builder_cleanup = _emit_builder_call(metadata, builder_api, builder_var, declarations)
                for line in builder_lines:
                    if line not in setup_lines:
                        setup_lines.append(line)
                cleanup_items.extend(builder_cleanup)
                return builder_var
        return "NULL"

    if "*" in arg_type:
        if "void" in arg_type.lower():
            if "char mutable_buffer[64] = \"seed\";" not in declarations:
                setup_lines.append('    char mutable_buffer[64] = "seed";')
                declarations.add('char mutable_buffer[64] = "seed";')
            return "mutable_buffer"
        return "NULL"

    return "0"


def _emit_builder_call(
    metadata: LibraryMetadata,
    builder_api: str,
    variable_name: str,
    declarations: set[str],
) -> tuple[list[str], list[tuple[str, str]]]:
    spec = metadata.api_specs[builder_api]
    lines: list[str] = []
    cleanup_items: list[tuple[str, str]] = []
    local_declarations = set(declarations)
    args: list[str] = []
    for arg_name, arg_type in zip(spec.arg_names, spec.arg_types):
        expr = _emit_arg_value(
            metadata,
            builder_api,
            arg_name,
            arg_type,
            lines,
            cleanup_items,
            local_declarations,
            allow_builders=False,
        )
        args.append(expr)
    call_expr = "{} {} = {}({});".format(spec.ret, variable_name, builder_api, ", ".join(args)).strip()
    lines.append("    " + call_expr)
    if "*" in spec.ret:
        lines.append("    if ({} == NULL) {{ return 0; }}".format(variable_name))
        cleanup_api = _find_cleanup_api(metadata, _base_type(spec.ret))
        if cleanup_api is not None:
            cleanup_items.append((cleanup_api, variable_name))
    declarations.update(local_declarations)
    return lines, cleanup_items


def _render_seed_for_api(metadata: LibraryMetadata, api_name: str) -> str:
    spec = metadata.api_specs[api_name]
    includes = ['#include "{}"'.format(spec.header)] + STANDARD_HEADERS
    seen_includes: list[str] = []
    for header in includes:
        if header not in seen_includes:
            seen_includes.append(header)

    setup_lines: list[str] = []
    cleanup_items: list[tuple[str, str]] = []
    declarations: set[str] = set()
    args: list[str] = []
    for arg_name, arg_type in zip(spec.arg_names, spec.arg_types):
        expr = _emit_arg_value(
            metadata,
            api_name,
            arg_name,
            arg_type,
            setup_lines,
            cleanup_items,
            declarations,
        )
        args.append(expr)

    body_lines = ["int main(void) {"] + setup_lines
    call_args = ", ".join(args)
    if spec.ret != "void":
        body_lines.append("    {} result = {}({});".format(spec.ret, api_name, call_args).strip())
        if "*" in spec.ret:
            body_lines.append("    if (result == NULL) {")
            body_lines.append("        return 0;")
            body_lines.append("    }")
            cleanup_api = _find_cleanup_api(metadata, _base_type(spec.ret))
            if cleanup_api is not None:
                cleanup_items.append((cleanup_api, "result"))
    else:
        body_lines.append("    {}({});".format(api_name, call_args))

    seen_cleanup: set[tuple[str, str]] = set()
    for cleanup_api, variable_name in reversed(cleanup_items):
        key = (cleanup_api, variable_name)
        if key in seen_cleanup:
            continue
        seen_cleanup.add(key)
        body_lines.append("    {}({});".format(cleanup_api, variable_name))

    body_lines.append("    return 0;")
    body_lines.append("}")
    return "\n".join(seen_includes + ["", ""] + body_lines) + "\n"


def build_auto_seed_corpus(metadata: LibraryMetadata) -> Path:
    seed_root = metadata.cache_dir / "seeds"
    seed_root.mkdir(parents=True, exist_ok=True)
    for child in list(seed_root.iterdir()):
        if child.is_dir() and child.name not in metadata.api_specs:
            shutil.rmtree(child, ignore_errors=True)
    for api_name in sorted(metadata.api_specs):
        api_dir = seed_root / api_name
        api_dir.mkdir(parents=True, exist_ok=True)
        seed_path = api_dir / "seed1.c"
        seed_path.write_text(_render_seed_for_api(metadata, api_name), encoding="utf-8")
    return seed_root
