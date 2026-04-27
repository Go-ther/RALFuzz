from __future__ import annotations

from pathlib import Path

from ctitanfuzz.toolchain import compile_testcase, run_compiled_binary
from ctitanfuzz.util.util import ExecutionStatus, cleanup_dir, run_cmd


class CoverageTracker:
    def __init__(
        self,
        target_adapter,
        target_root: str | Path,
        compiler: str = "gcc",
        gcov: str = "gcov",
        build_root: str | Path = "ctitanfuzz/.build",
        compile_timeout: int = 20,
        test_timeout: int = 10,
    ) -> None:
        self.target_adapter = target_adapter
        self.target_root = Path(target_root).resolve()
        self.compiler = compiler
        self.gcov = gcov
        self.build_root = build_root
        self.compile_timeout = compile_timeout
        self.test_timeout = test_timeout
        self.prev_coverage = 0

    def _parse_gcov_file(self, gcov_file: Path) -> int:
        executed = 0
        if not gcov_file.exists():
            return executed
        for line in gcov_file.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            count = parts[0].strip()
            if count in {"-", "#####", "====="}:
                continue
            try:
                if int(count) > 0:
                    executed += 1
            except ValueError:
                continue
        return executed

    def _collect_coverage(self, build_dir: Path) -> int:
        total = 0
        for source_path in self.target_adapter.get_focus_files(self.target_root):
            status, _ = run_cmd(
                [self.gcov, "-b", "-o", str(build_dir), str(source_path)],
                timeout=self.compile_timeout,
                cwd=build_dir,
            )
            if status != ExecutionStatus.SUCCESS:
                continue
            gcov_file = build_dir / (source_path.name + ".gcov")
            if not gcov_file.exists():
                matches = list(build_dir.glob(source_path.name + "*.gcov"))
                if matches:
                    gcov_file = matches[0]
            total += self._parse_gcov_file(gcov_file)
        return total

    def run(self, code: str) -> tuple[ExecutionStatus, bool]:
        build_result = compile_testcase(
            code=code,
            target_adapter=self.target_adapter,
            target_root=self.target_root,
            compiler=self.compiler,
            build_root=self.build_root,
            timeout=self.compile_timeout,
            enable_coverage=True,
            enable_sanitizer=False,
        )
        try:
            if build_result.status != ExecutionStatus.SUCCESS:
                return build_result.status, False
            status, message = run_compiled_binary(build_result, timeout=self.test_timeout)
            reason, _ = self.target_adapter.classify_oracle_failure(message)
            if status != ExecutionStatus.SUCCESS or reason is not None:
                return ExecutionStatus.EXCEPTION if reason is not None else status, False
            total_coverage = self._collect_coverage(build_result.build_dir)
            new_coverage = total_coverage > self.prev_coverage
            self.prev_coverage = max(self.prev_coverage, total_coverage)
            return ExecutionStatus.SUCCESS, new_coverage
        finally:
            cleanup_dir(build_result.build_dir)
