from __future__ import annotations

from ctitanfuzz.coverage import CoverageTracker
from ctitanfuzz.targets import create_target_adapter
from ctitanfuzz.util.util import ExecutionStatus

coverage_executor = None


def init_test_executor(args, cov=False):
    global coverage_executor
    if not cov:
        return
    package_root = args.package_root if hasattr(args, "package_root") else None
    if package_root is None:
        raise ValueError("package_root is required to initialize ctitanfuzz coverage executor")
    target_adapter = create_target_adapter(args.target, package_root)
    coverage_executor = CoverageTracker(
        target_adapter=target_adapter,
        target_root=args.target_root,
        compiler=args.compiler,
        gcov=args.gcov,
    )


def kill_executors():
    global coverage_executor
    coverage_executor = None


def coverate_run_status_mp(g_code, library, cov_executor=None, device="cpu"):
    executor = cov_executor or coverage_executor
    if executor is None:
        return ExecutionStatus.EXCEPTION, False
    return executor.run(g_code)
