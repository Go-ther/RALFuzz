from __future__ import annotations

import html
import json
import pathlib
import re
from typing import List, Optional, Sequence, Set, Tuple

from seed_types import DEFAULT_PROMPT_TEMPLATE, ApiSpec

try:
    from build_api_spec import iter_prototypes as iter_header_prototypes
except Exception:
    iter_header_prototypes = None


def extract_api_name_from_signature(signature: str) -> str:
    if "(" not in signature:
        raise ValueError(f"Cannot parse api name from signature: {signature}")
    head = signature.split("(", 1)[0]
    tokens = re.findall(r"[A-Za-z_]\w*", head)
    if not tokens:
        raise ValueError(f"Cannot parse api name from signature: {signature}")
    return tokens[-1]


def sanitize_api_folder_name(api_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", api_name)


def read_text_file(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def load_prompt_template(path: Optional[pathlib.Path]) -> str:
    if path is None or (not path.exists()):
        return DEFAULT_PROMPT_TEMPLATE
    return read_text_file(path)


def parse_api_specs_from_txt(path: pathlib.Path, default_header: str, doc_url_template: Optional[str]) -> List[ApiSpec]:
    specs: List[ApiSpec] = []
    for line in read_text_file(path).splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = [p.strip() for p in s.split("\t")]
        api_name: Optional[str] = None
        signature: Optional[str] = None
        header: Optional[str] = None
        if len(parts) == 1:
            one = parts[0]
            if "(" in one:
                signature = one
                api_name = extract_api_name_from_signature(signature)
            else:
                api_name = one
                signature = ""
            header = default_header
        elif len(parts) == 2:
            left, right = parts
            if "(" in left and "(" not in right:
                signature = left
                api_name = extract_api_name_from_signature(signature)
                header = right
            else:
                api_name = left
                signature = right
                header = default_header
        else:
            api_name = parts[0]
            signature = parts[1]
            header = parts[2] if parts[2] else default_header
        if not api_name:
            continue
        specs.append(
            ApiSpec(
                api_name=api_name,
                api_signature=signature or "",
                header=header or default_header,
                doc_url=doc_url_template.format(api=api_name) if doc_url_template else None,
            )
        )
    return specs


def parse_api_specs_from_json(path: pathlib.Path, default_header: str, doc_url_template: Optional[str]) -> List[ApiSpec]:
    raw = json.loads(read_text_file(path))
    if not isinstance(raw, list):
        raise ValueError("JSON api spec file must be a list.")
    specs: List[ApiSpec] = []
    for rec in raw:
        if not isinstance(rec, dict):
            continue
        api_name = str(rec.get("api_name", "")).strip()
        api_signature = str(rec.get("api_signature", "")).strip()
        header = str(rec.get("header", default_header)).strip() or default_header
        doc_url = rec.get("doc_url")
        if not doc_url and doc_url_template and api_name:
            doc_url = doc_url_template.format(api=api_name)
        if not api_name and api_signature:
            api_name = extract_api_name_from_signature(api_signature)
        if api_name:
            specs.append(ApiSpec(api_name=api_name, api_signature=api_signature, header=header, doc_url=doc_url))
    return specs


def load_api_specs(path: pathlib.Path, default_header: str, doc_url_template: Optional[str]) -> List[ApiSpec]:
    if path.suffix.lower() == ".json":
        return parse_api_specs_from_json(path, default_header, doc_url_template)
    return parse_api_specs_from_txt(path, default_header, doc_url_template)


def discover_header_files(
    api_dirs: Sequence[str],
    header_globs: Sequence[str],
    exclude_globs: Sequence[str],
    recursive: bool,
) -> List[pathlib.Path]:
    found: List[pathlib.Path] = []
    seen: Set[pathlib.Path] = set()
    globs = list(header_globs) if header_globs else ["*.h"]
    excludes = list(exclude_globs) if exclude_globs else []

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
                if not p.is_file() or p.suffix.lower() != ".h":
                    continue
                rel = str(p.relative_to(root)).replace("\\", "/")
                if any(pathlib.PurePosixPath(rel).match(x) for x in excludes):
                    continue
                if p in seen:
                    continue
                seen.add(p)
                found.append(p)
    return found


def auto_discover_api_specs(
    api_dirs: Sequence[str],
    style: str,
    api_name_regex: Optional[str],
    header_globs: Sequence[str],
    exclude_header_globs: Sequence[str],
    recursive: bool,
    sort_by_name: bool,
) -> List[ApiSpec]:
    if iter_header_prototypes is None:
        raise RuntimeError("Auto API discovery unavailable: build_api_spec.iter_prototypes not importable.")

    headers = discover_header_files(
        api_dirs=api_dirs,
        header_globs=header_globs,
        exclude_globs=exclude_header_globs,
        recursive=recursive,
    )
    if not headers:
        return []

    api_name_re = re.compile(api_name_regex) if api_name_regex else None
    specs: List[ApiSpec] = []
    seen: Set[Tuple[str, str, str]] = set()
    for hp in headers:
        text = hp.read_text(encoding="utf-8", errors="ignore")
        header_col = hp.name
        for name, sig in iter_header_prototypes(text, style=style):
            if api_name_re and not api_name_re.search(name):
                continue
            key = (name, sig, header_col)
            if key in seen:
                continue
            seen.add(key)
            specs.append(ApiSpec(api_name=name, api_signature=sig, header=header_col))

    if sort_by_name:
        specs.sort(key=lambda x: x.api_name.lower())
    return specs


def crawl_signature_from_doc(api_name: str, url: str, timeout_sec: int) -> Optional[str]:
    try:
        import requests

        resp = requests.get(url, timeout=timeout_sec)
        resp.raise_for_status()
    except Exception:
        return None
    text = resp.text
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    pats = [
        rf"[A-Za-z_][A-Za-z0-9_\s\*\(\),\[\]]*\b{re.escape(api_name)}\s*\([^;{{}}]*\)\s*;",
        rf"\b{re.escape(api_name)}\s*\([^;{{}}]*\)\s*;",
    ]
    for pat in pats:
        m = re.search(pat, text)
        if m:
            return re.sub(r"\s+", " ", m.group(0).strip())
    return None


def enrich_missing_signatures(specs: List[ApiSpec], crawl_timeout_sec: int) -> None:
    for spec in specs:
        if spec.api_signature or not spec.doc_url:
            continue
        sig = crawl_signature_from_doc(spec.api_name, spec.doc_url, crawl_timeout_sec)
        if sig:
            spec.api_signature = sig
