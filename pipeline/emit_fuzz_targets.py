#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.fuzz_adapter import emit_fuzz_targets
from pipeline.run_full_pipeline import discover_include_dirs, discover_link_sources


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit libFuzzer / AFL++ compatible fuzz harnesses from accepted RALFuzz mutation outputs."
    )
    parser.add_argument("--mutation-dir", required=True, help="Path to runtime_data/.../mutation")
    parser.add_argument("--api-dir", required=True, help="Target library input directory")
    parser.add_argument("--api", required=True, help="Target API name")
    parser.add_argument(
        "--backend",
        choices=["libfuzzer", "afl", "both"],
        default="both",
        help="Fuzz backend to emit",
    )
    parser.add_argument("--compiler", default="clang")
    parser.add_argument("--per-harness", action="store_true", default=False)
    parser.add_argument("--enable-sanitizer", action="store_true", default=True)
    parser.add_argument("--disable-sanitizer", action="store_true", default=False)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_dir = Path(args.api_dir).resolve()
    mutation_dir = Path(args.mutation_dir).resolve()
    backends = ["libfuzzer", "afl"] if args.backend == "both" else [args.backend]
    manifest = emit_fuzz_targets(
        mutation_dir=mutation_dir,
        api_dir=api_dir,
        api_name=args.api,
        backends=backends,
        compiler=args.compiler,
        include_dirs=discover_include_dirs(api_dir),
        source_files=discover_link_sources(api_dir),
        enable_sanitizer=not args.disable_sanitizer,
        per_harness=args.per_harness,
    )
    print(f"[fuzz-adapter] api={args.api} source_harnesses={manifest['source_harness_count']}")
    for backend, payload in manifest["backends"].items():
        print(
            f"[fuzz-adapter] backend={backend} emitted={len(payload['harnesses'])} "
            f"build_script={payload['build_script']}"
        )
    print(f"[fuzz-adapter] manifest={mutation_dir / 'fuzz' / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
