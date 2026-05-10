from __future__ import annotations

from pathlib import Path

from mutation.c_mutators import SnippetInfill
from mutation.util.util import load_apis, normalize_code


def strip_markdown_fences(code: str) -> str:
    text = code.strip()
    if not text.startswith("```"):
        return code
    lines = text.splitlines()
    if len(lines) >= 2 and lines[-1].startswith("```"):
        return "\n".join(lines[1:-1])
    return code


def clean_code(code: str, target_adapter) -> str:
    text = strip_markdown_fences(code)
    text = target_adapter.strip_generation_context(text)
    return normalize_code(text)


def get_initial_programs(
    directory: str | Path,
    mask_identifier: str,
    target_adapter,
    replace_type: str,
    target_api: str = "all",
) -> dict[str, list[dict[str, str]]]:
    ret: dict[str, list[dict[str, str]]] = {}
    api_list = load_apis(target_adapter.get_api_list_path())
    base = Path(directory)
    api_dirs = [base / target_api] if target_api != "all" else [path for path in base.iterdir() if path.is_dir()]
    if target_api == "all":
        api_dirs = [base / api for api in api_list if (base / api).exists()]
    for api_dir in api_dirs:
        api_name = api_dir.name
        ret[api_name] = []
        for program in api_dir.glob("*" + target_adapter.file_extension):
            original = clean_code(program.read_text(encoding="utf-8"), target_adapter)
            infill = SnippetInfill(
                mask_identifier=mask_identifier,
                api_call=api_name,
                full_api_list=api_list,
                replace_type=replace_type,
            )
            num_replaced, infill_code, original_code = infill.add_infill(original)
            if num_replaced >= 1:
                ret[api_name].append({"original": original_code, "infill": infill_code})
        if len(ret[api_name]) == 0:
            del ret[api_name]
    return ret
