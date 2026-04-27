from __future__ import annotations

import argparse
from pathlib import Path

from ctitanfuzz.targets import create_target_adapter
from ctitanfuzz.util import util
from ctitanfuzz.util.util import ExecutionStatus
from ctitanfuzz.validate import validate_status

SEED = 420
OUTPUT_LIMIT = 2048
target_adapter = None


def _resolve_build_root() -> Path:
    override = os.environ.get("CTITANFUZZ_BUILD_ROOT", "").strip()
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parent / ".build"


def framework_single(args: argparse.Namespace, core_func) -> None:
    src = Path(args.input).read_text(encoding="utf-8")
    try:
        core_func(SEED, args, src)
    except Exception as exc:
        reason = "FrameworkCrashCatch"
        detail = str(exc)
        if len(exc.args) >= 2:
            reason = exc.args[0]
            detail = exc.args[1]
        print("\nFrameworkSingle", reason, SEED, detail)


def framework_src_batch(args: argparse.Namespace, core_func) -> None:
    tasks = util.read_all_tasks_from_dir(args.input, extension=target_adapter.file_extension)
    last_api = None
    for task_id in range(args.start, len(tasks)):
        api, label, src = util.parse_task(tasks[task_id])
        if args.singleapi:
            if last_api is not None and last_api != api:
                break
            last_api = api
        try:
            core_func(SEED, args, src)
        except Exception as exc:
            reason = "FrameworkCrashCatch"
            detail = str(exc)
            if len(exc.args) >= 2:
                reason = exc.args[0]
                detail = exc.args[1]
            if len(detail) > OUTPUT_LIMIT:
                detail = "Detail is too long"
            if reason == "FrameworkCrashCatch":
                print(detail)
                raise
            print("\nTitanFuzzTestcase", task_id, api, label, reason, SEED, detail)


def core_single(seed: int, args: argparse.Namespace, src: str) -> None:
    status, detail = validate_status(
        src,
        target_adapter=target_adapter,
        target_root=args.target_root,
        compiler=args.compiler,
        build_root=_resolve_build_root(),
        compile_timeout=args.compile_timeout,
        test_timeout=args.test_timeout,
        enable_sanitizer=args.enable_sanitizer,
    )
    if status == ExecutionStatus.SUCCESS:
        raise Exception("Success", "succeeded")
    if status == ExecutionStatus.TIMEOUT:
        raise Exception("TimeoutFail", detail)
    if status == ExecutionStatus.CRASH:
        raise Exception("CrashCatch", detail)
    reason, _ = target_adapter.classify_oracle_failure(detail)
    if reason is not None:
        raise Exception(reason, detail)
    raise Exception("ExecFail", detail)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=str, default="generic", choices=["generic", "auto"])
    parser.add_argument("--target_root", type=str, default=None)
    parser.add_argument("--mode", type=str, default="single", choices=["single", "batch", "race"])
    parser.add_argument("--input", type=str, default=None)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--singleapi", action="store_true", default=False)
    parser.add_argument("--compiler", type=str, default="gcc")
    parser.add_argument("--compile_timeout", type=int, default=20)
    parser.add_argument("--test_timeout", type=int, default=10)
    parser.add_argument("--enable_sanitizer", action="store_true", default=False)
    args = parser.parse_args()

    global target_adapter
    target_adapter = create_target_adapter(args.target, Path(__file__).resolve().parent)
    args.target_root = str(target_adapter.resolve_target_root(args.target_root))

    if args.mode == "single":
        framework_single(args, core_single)
    else:
        framework_src_batch(args, core_single)


if __name__ == "__main__":
    main()
    raise SystemExit(233)
