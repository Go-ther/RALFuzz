#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CATEGORY_DIRS = ("valid", "exception", "crash", "notarget", "hangs")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def count_c_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.glob("*.c") if item.is_file())


def parse_generation_log(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    generated_line = None
    valid_line = None
    for line in text.splitlines():
        if " generated:" in line and " duplicated " in line:
            generated_line = line
        if " valid outputs using " in line:
            valid_line = line

    counters = {
        "generated": None,
        "exception": None,
        "duplicate": None,
        "crash": None,
        "timeout": None,
        "notarget": None,
        "valid_outputs": None,
        "generation_time_sec": None,
        "validation_time_sec": None,
    }
    if generated_line:
        match = re.search(
            r"(?P<generated>\d+)\s+generated:\s+"
            r"(?P<exception>\d+)\s+exceptions\s+"
            r"(?P<duplicate>\d+)\s+duplicated\s+"
            r"(?P<crash>\d+)\s+crashes\s+"
            r"(?P<timeout>\d+)\s+timeouts\s+"
            r"(?P<notarget>\d+)\s+notarget",
            generated_line,
        )
        if match:
            counters.update({key: int(value) for key, value in match.groupdict().items()})
    if valid_line:
        match = re.search(
            r"(?P<valid_outputs>\d+)\s+valid outputs using\s+"
            r"(?P<generation_time_sec>[0-9.]+)s generation,\s+"
            r"(?P<validation_time_sec>[0-9.]+)s validation",
            valid_line,
        )
        if match:
            parsed = match.groupdict()
            counters["valid_outputs"] = int(parsed["valid_outputs"])
            counters["generation_time_sec"] = float(parsed["generation_time_sec"])
            counters["validation_time_sec"] = float(parsed["validation_time_sec"])
    return {"path": str(path), "parsed": counters, "tail": text.splitlines()[-20:]}


def iter_tree_lines(root: Path, max_depth: int = 3) -> list[str]:
    if not root.exists():
        return [f"{root} <missing>"]
    root = root.resolve()
    lines = [root.name + "/"]

    def walk(path: Path, prefix: str, depth: int) -> None:
        if depth >= max_depth:
            return
        entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        for index, entry in enumerate(entries):
            connector = "`-- " if index == len(entries) - 1 else "|-- "
            lines.append(prefix + connector + entry.name + ("/" if entry.is_dir() else ""))
            if entry.is_dir():
                extension = "    " if index == len(entries) - 1 else "|   "
                walk(entry, prefix + extension, depth + 1)

    walk(root, "", 0)
    return lines


def stage1_snapshot(seed_dir: Path, api: str) -> dict[str, Any]:
    summary = load_json(seed_dir / "summary.json", {})
    outputs = load_json(seed_dir / "outputs.json", {})
    seed_bank = load_json(seed_dir / "seed_bank.json", {})
    api_out = outputs.get(api, {}) if isinstance(outputs, dict) else {}
    records = api_out.get("records", []) if isinstance(api_out, dict) else []
    reasons = Counter(str(record.get("reason", "<missing>")) for record in records if isinstance(record, dict))
    validation_reasons = Counter(
        str(record.get("validation_reason", "<missing>")) for record in records if isinstance(record, dict)
    )
    return {
        "api": api,
        "source_files": {
            "summary_json": str(seed_dir / "summary.json"),
            "outputs_json": str(seed_dir / "outputs.json"),
            "seed_bank_json": str(seed_dir / "seed_bank.json"),
        },
        "summary": summary,
        "api_record": {
            key: api_out.get(key)
            for key in (
                "api_name",
                "api_signature",
                "samples_requested",
                "samples_attempted",
                "samples_received",
                "target_valid_per_api",
                "validation_passed_count",
                "valid_count",
                "g_time_sec",
                "token_usage",
                "quality_metrics",
                "endpoint_used",
            )
            if isinstance(api_out, dict) and key in api_out
        },
        "record_reason_counts": dict(sorted(reasons.items())),
        "validation_reason_counts": dict(sorted(validation_reasons.items())),
        "seed_bank_entry": seed_bank.get(api, {}) if isinstance(seed_bank, dict) else {},
    }


def stage2_snapshot(mutation_dir: Path, api: str) -> dict[str, Any]:
    outputs = load_json(mutation_dir / "outputs.json", {})
    api_out = outputs.get(api, {}) if isinstance(outputs, dict) else {}
    info_code = api_out.get("outputs", {}) if isinstance(api_out, dict) else {}
    records = list(info_code.values()) if isinstance(info_code, dict) else []
    generated_records = [
        record
        for record in records
        if isinstance(record, dict) and not str(record.get("filename", "")).startswith(f"{api}_seed")
    ]
    signatures = [
        str(record.get("behavior_signature", ""))
        for record in generated_records
        if isinstance(record, dict) and str(record.get("behavior_signature", ""))
    ]
    category_counts = {name: count_c_files(mutation_dir / name) for name in CATEGORY_DIRS}
    log_info = parse_generation_log(mutation_dir / "generation.log")
    parsed = log_info["parsed"]
    generated_from_log = parsed.get("generated")
    duplicate_from_log = parsed.get("duplicate")
    candidate_count = generated_from_log
    if candidate_count is None:
        candidate_count = sum(category_counts.values())
    duplicate_count = duplicate_from_log
    if duplicate_count is None:
        duplicate_count = max(0, int(candidate_count) - sum(category_counts.values()))
    wall_clock_sec = None
    if isinstance(api_out, dict):
        tot_time = api_out.get("tot_time", [])
        if isinstance(tot_time, list):
            wall_clock_sec = sum(float(value) for value in tot_time if isinstance(value, (int, float)))
    g_time = api_out.get("g_time", []) if isinstance(api_out, dict) else []
    v_time = api_out.get("v_time", []) if isinstance(api_out, dict) else []
    signature_counts = Counter(signatures)
    token_usage = api_out.get("token_usage") if isinstance(api_out, dict) else None
    if not isinstance(token_usage, dict):
        token_usage = {
            "available": False,
            "estimate_method": "unavailable: mutation LLM client did not persist provider usage fields",
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }
    return {
        "api": api,
        "source_files": {
            "outputs_json": str(mutation_dir / "outputs.json"),
            "generation_log": str(mutation_dir / "generation.log"),
            "args_txt": str(mutation_dir / "args.txt"),
        },
        "candidate_count": int(candidate_count),
        "valid_count": category_counts["valid"],
        "exception_count": category_counts["exception"],
        "crash_count": category_counts["crash"],
        "notarget_count": category_counts["notarget"],
        "hang_count": category_counts["hangs"],
        "duplicate_count": int(duplicate_count),
        "unique_behavior_signature_count": len(signature_counts),
        "behavior_signature_counts": dict(sorted(signature_counts.items())),
        "accepted_generated_record_count": len(generated_records),
        "seed_count": count_c_files(mutation_dir / "seed"),
        "wall_clock_sec": wall_clock_sec,
        "generation_time_sec": sum(float(value) for value in g_time if isinstance(value, (int, float))),
        "validation_time_sec": sum(float(value) for value in v_time if isinstance(value, (int, float))),
        "log_summary": log_info,
        "token_usage": token_usage,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export fixed illustrative-example artifacts.")
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--api", default="cJSON_Parse")
    parser.add_argument("--output-dir", default="repro_artifacts/illustrative")
    args = parser.parse_args()

    runtime_root = Path(args.runtime_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    seed_dir = runtime_root / "seed_generation"
    mutation_dir = runtime_root / "mutation"
    output_dir.mkdir(parents=True, exist_ok=True)

    stage1 = stage1_snapshot(seed_dir, args.api)
    stage2 = stage2_snapshot(mutation_dir, args.api)
    manifest = {
        "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(runtime_root),
        "api": args.api,
        "artifacts": {
            "stage1_seed_summary": str(output_dir / "stage1_seed_summary.json"),
            "stage2_mutation_snapshot": str(output_dir / "stage2_mutation_snapshot.json"),
            "output_tree": str(output_dir / "output_tree.txt"),
            "manifest": str(output_dir / "manifest.md"),
        },
    }

    save_json(output_dir / "stage1_seed_summary.json", stage1)
    save_json(output_dir / "stage2_mutation_snapshot.json", stage2)
    seed_bank_path = seed_dir / "seed_bank.json"
    if seed_bank_path.exists():
        shutil.copyfile(seed_bank_path, output_dir / "seed_bank.json")
        manifest["artifacts"]["seed_bank"] = str(output_dir / "seed_bank.json")

    tree_text = "\n".join(iter_tree_lines(runtime_root, max_depth=3)) + "\n"
    (output_dir / "output_tree.txt").write_text(tree_text, encoding="utf-8")
    save_json(output_dir / "manifest.json", manifest)
    manifest_md = "\n".join(
        [
            "# Illustrative cJSON Parse Artifact Manifest",
            "",
            f"- Exported at UTC: `{manifest['exported_at_utc']}`",
            f"- Runtime root: `{runtime_root}`",
            f"- API: `{args.api}`",
            "",
            "## Key Stage 2 Counts",
            "",
            f"- candidates: `{stage2['candidate_count']}`",
            f"- valid: `{stage2['valid_count']}`",
            f"- duplicate: `{stage2['duplicate_count']}`",
            f"- exception: `{stage2['exception_count']}`",
            f"- crash: `{stage2['crash_count']}`",
            f"- notarget: `{stage2['notarget_count']}`",
            f"- hangs: `{stage2['hang_count']}`",
            f"- unique behavior signatures: `{stage2['unique_behavior_signature_count']}`",
            f"- wall-clock seconds: `{stage2['wall_clock_sec']}`",
            "",
            "## Files",
            "",
            "\n".join(f"- `{name}`: `{path}`" for name, path in manifest["artifacts"].items()),
            "",
        ]
    )
    (output_dir / "manifest.md").write_text(manifest_md, encoding="utf-8")
    print(f"[export] wrote artifacts to {output_dir}")
    print(json.dumps(stage2, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
