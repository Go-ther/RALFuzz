from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ctitanfuzz.toolchain import compile_testcase, run_compiled_binary
from ctitanfuzz.util.util import ExecutionStatus, cleanup_dir


@dataclass
class ValidationReport:
    status: ExecutionStatus
    message: str
    compile_ok: bool
    run_ok: bool
    timeout: bool
    crash: bool
    sanitizer_hits: list[str] = field(default_factory=list)


def validate_testcase(
    code: str,
    target_adapter,
    target_root: str | Path,
    compiler: str = "gcc",
    build_root: str | Path = "ctitanfuzz/.build",
    compile_timeout: int = 20,
    test_timeout: int = 10,
    enable_sanitizer: bool = False,
) -> ValidationReport:
    build_result = compile_testcase(
        code=code,
        target_adapter=target_adapter,
        target_root=Path(target_root).resolve(),
        compiler=compiler,
        build_root=build_root,
        timeout=compile_timeout,
        enable_coverage=False,
        enable_sanitizer=enable_sanitizer,
    )
    try:
        if build_result.status != ExecutionStatus.SUCCESS:
            message = "CompileError\n" + build_result.message
            return ValidationReport(
                status=build_result.status,
                message=message,
                compile_ok=False,
                run_ok=False,
                timeout=build_result.status == ExecutionStatus.TIMEOUT,
                crash=build_result.status == ExecutionStatus.CRASH,
                sanitizer_hits=_detect_sanitizers(message),
            )
        status, message = run_compiled_binary(build_result, timeout=test_timeout)
        reason, oracle_detail = target_adapter.classify_oracle_failure(message)
        final_status = ExecutionStatus.EXCEPTION if reason is not None else status
        final_message = oracle_detail if reason is not None else message
        run_ok = status == ExecutionStatus.SUCCESS and reason is None
        return ValidationReport(
            status=final_status,
            message=final_message,
            compile_ok=True,
            run_ok=run_ok,
            timeout=status == ExecutionStatus.TIMEOUT,
            crash=status == ExecutionStatus.CRASH,
            sanitizer_hits=_detect_sanitizers(final_message),
        )
    finally:
        cleanup_dir(build_result.build_dir)


def _detect_sanitizers(message: str) -> list[str]:
    lower = message.lower()
    hits: list[str] = []
    if "addresssanitizer" in lower or "heap-buffer-overflow" in lower:
        hits.append("asan")
    if "undefinedbehavior" in lower or "runtime error:" in lower:
        hits.append("ubsan")
    return hits


def validate_status(
    code: str,
    target_adapter,
    target_root: str | Path,
    compiler: str = "gcc",
    build_root: str | Path = "ctitanfuzz/.build",
    compile_timeout: int = 20,
    test_timeout: int = 10,
    enable_sanitizer: bool = False,
) -> tuple[ExecutionStatus, str]:
    report = validate_testcase(
        code=code,
        target_adapter=target_adapter,
        target_root=target_root,
        compiler=compiler,
        build_root=build_root,
        compile_timeout=compile_timeout,
        test_timeout=test_timeout,
        enable_sanitizer=enable_sanitizer,
    )
    if report.run_ok:
        return ExecutionStatus.SUCCESS, ""
    return report.status, report.message
