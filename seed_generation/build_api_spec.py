#!/usr/bin/env python3
"""
Build API spec file for generate_c_seeds.py from C header declarations.

Output format (tab-separated):
api_name<TAB>api_signature<TAB>header
"""

from __future__ import annotations

import argparse
import pathlib
import re
from typing import Iterable, List, Set, Tuple


def collapse_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def join_multiline_statements(text: str) -> List[str]:
    lines = text.splitlines()
    stmts: List[str] = []
    buf: List[str] = []
    for raw in lines:
        line = re.sub(r"/\*.*?\*/", " ", raw)
        line = re.sub(r"//.*$", " ", line)
        line = line.strip()
        if not line:
            continue
        buf.append(line)
        if line.endswith(";"):
            stmts.append(" ".join(buf))
            buf = []
    return stmts


def parse_cjson_public(stmt: str) -> Tuple[str, str] | None:
    m = re.match(
        r"^CJSON_PUBLIC\(([^)]*)\)\s*([A-Za-z_]\w*)\s*\((.*)\)\s*;\s*$",
        stmt,
    )
    if not m:
        return None
    ret_type, name, args = m.groups()
    signature = f"{collapse_spaces(ret_type)} {name}({collapse_spaces(args)});"
    return name, signature


def parse_generic_prototype(stmt: str) -> Tuple[str, str] | None:
    if stmt.startswith("#"):
        return None
    if stmt.startswith("typedef "):
        return None
    if stmt.startswith("struct ") or stmt.startswith("enum "):
        return None
    if "(*" in stmt:
        return None
    if "=" in stmt and "(" not in stmt.split("=", 1)[0]:
        return None

    m = re.match(
        r"^([A-Za-z_][\w\s\*\d]*?)\s+([A-Za-z_]\w*)\s*\(([^;{}]*)\)\s*;\s*$",
        stmt,
    )
    if not m:
        return None
    ret_type, name, args = m.groups()
    if name.upper() == name:
        return None
    signature = f"{collapse_spaces(ret_type)} {name}({collapse_spaces(args)});"
    return name, signature


def iter_prototypes(text: str, style: str) -> Iterable[Tuple[str, str]]:
    for stmt in join_multiline_statements(text):
        result: Tuple[str, str] | None = None
        if style in ("cjson", "auto"):
            result = parse_cjson_public(stmt)
        if result is None and style in ("generic", "auto"):
            result = parse_generic_prototype(stmt)
        if result is not None:
            yield result


def collect_headers(
    headers: List[str],
    api_dirs: List[str],
    header_globs: List[str],
    exclude_header_globs: List[str],
    recursive: bool,
) -> List[pathlib.Path]:
    found: List[pathlib.Path] = []
    seen: Set[pathlib.Path] = set()

    for h in headers:
        hp = pathlib.Path(h)
        if hp.is_file() and hp not in seen:
            seen.add(hp)
            found.append(hp)

    globs = header_globs if header_globs else ["*.h"]
    excludes = exclude_header_globs if exclude_header_globs else []

    for root_raw in api_dirs:
        root = pathlib.Path(root_raw)
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix.lower() == ".h" and root not in seen:
                seen.add(root)
                found.append(root)
            continue
        if not root.is_dir():
            continue

        iterator_fn = root.rglob if recursive else root.glob
        for pat in globs:
            for p in sorted(iterator_fn(pat)):
                if not p.is_file():
                    continue
                if p.suffix.lower() != ".h":
                    continue
                rel = str(p.relative_to(root)).replace("\\", "/")
                if any(pathlib.PurePosixPath(rel).match(x) for x in excludes):
                    continue
                if p in seen:
                    continue
                seen.add(p)
                found.append(p)

    return found


def main() -> None:
    parser = argparse.ArgumentParser(description="Build API spec from C header(s).")
    parser.add_argument("--headers", nargs="+", default=[], help="Header file paths.")
    parser.add_argument(
        "--api-dir",
        action="append",
        default=[],
        help="Auto-discover headers from API source directory (repeatable).",
    )
    parser.add_argument(
        "--header-glob",
        action="append",
        default=[],
        help="Header glob pattern under --api-dir (default: *.h). Repeatable.",
    )
    parser.add_argument(
        "--exclude-header-glob",
        action="append",
        default=[],
        help="Exclude glob under --api-dir (e.g. tests/*). Repeatable.",
    )
    parser.add_argument(
        "--non-recursive",
        action="store_true",
        default=False,
        help="Do not recursively scan --api-dir.",
    )
    parser.add_argument(
        "--api-name-regex",
        default=None,
        help="Optional regex filter for extracted API names.",
    )
    parser.add_argument(
        "--style",
        choices=["auto", "cjson", "generic"],
        default="auto",
        help="Prototype style parser.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output API spec txt path.",
    )
    parser.add_argument(
        "--header-name-mode",
        choices=["basename", "relative", "fullpath"],
        default="basename",
        help="How to write the 3rd column (header).",
    )
    parser.add_argument(
        "--sort",
        action="store_true",
        default=False,
        help="Sort by api name.",
    )
    args = parser.parse_args()

    if not args.headers and not args.api_dir:
        raise SystemExit("Need one of: --headers or --api-dir")

    header_paths = collect_headers(
        headers=args.headers,
        api_dirs=args.api_dir,
        header_globs=args.header_glob,
        exclude_header_globs=args.exclude_header_glob,
        recursive=not args.non_recursive,
    )
    if not header_paths:
        raise SystemExit("No header files found.")

    api_name_re = re.compile(args.api_name_regex) if args.api_name_regex else None
    rows: List[Tuple[str, str, str]] = []
    seen: Set[Tuple[str, str, str]] = set()

    for hp in header_paths:
        text = hp.read_text(encoding="utf-8", errors="ignore")
        if args.header_name_mode == "basename":
            header_col = hp.name
        elif args.header_name_mode == "relative":
            header_col = str(hp).replace("\\", "/")
        else:
            header_col = str(hp.resolve()).replace("\\", "/")

        for name, sig in iter_prototypes(text, style=args.style):
            if api_name_re and not api_name_re.search(name):
                continue
            key = (name, sig, header_col)
            if key in seen:
                continue
            seen.add(key)
            rows.append(key)

    if args.sort:
        rows.sort(key=lambda x: x[0].lower())

    out = pathlib.Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as f:
        for name, sig, header in rows:
            f.write(f"{name}\t{sig}\t{header}\n")

    print(f"scanned headers={len(header_paths)}")
    print(f"wrote apis={len(rows)} -> {out}")


if __name__ == "__main__":
    main()

