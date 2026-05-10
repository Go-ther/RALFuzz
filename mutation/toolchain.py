from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from mutation.util.util import ExecutionStatus, make_temp_build_dir, normalize_code, run_cmd


@dataclass
class BuildResult:
    status: ExecutionStatus
    message: str
    build_dir: Path
    source_file: Path
    binary_path: Path | None


def compile_testcase(
    code: str,
    target_adapter,
    target_root: Path,
    compiler: str,
    build_root: str | Path,
    timeout: int = 20,
    enable_coverage: bool = False,
    enable_sanitizer: bool = False,
) -> BuildResult:
    target_root = Path(target_root).resolve()
    build_dir = make_temp_build_dir(build_root)
    source_file = build_dir / "generated_test.c"
    source_file.write_text(normalize_code(code), encoding="utf-8")

    cflags = target_adapter.get_common_cflags(
        enable_coverage=enable_coverage,
        enable_sanitizer=enable_sanitizer,
    )
    include_dirs = target_adapter.get_include_dirs(target_root)
    object_files: list[Path] = []
    compile_sources = [source_file] + target_adapter.get_target_sources(target_root)
    for index, source_path in enumerate(compile_sources):
        source_path = Path(source_path).resolve()
        object_file = build_dir / "obj_{}.o".format(index)
        cmd = [compiler] + cflags
        for include_dir in include_dirs:
            cmd.extend(["-I", str(include_dir)])
        cmd.extend(["-c", str(source_path), "-o", str(object_file)])
        status, message = run_cmd(cmd, timeout=timeout, cwd=build_dir)
        if status != ExecutionStatus.SUCCESS:
            return BuildResult(status, message, build_dir, source_file, None)
        object_files.append(object_file)

    binary_name = "generated_test.exe" if os.name == "nt" else "generated_test"
    binary_path = build_dir / binary_name
    link_cmd = [compiler] + [str(path) for path in object_files]
    link_cmd += target_adapter.get_link_flags(
        enable_coverage=enable_coverage,
        enable_sanitizer=enable_sanitizer,
    )
    link_cmd += ["-o", str(binary_path)]
    status, message = run_cmd(link_cmd, timeout=timeout, cwd=build_dir)
    if status != ExecutionStatus.SUCCESS:
        return BuildResult(status, message, build_dir, source_file, None)
    return BuildResult(ExecutionStatus.SUCCESS, message, build_dir, source_file, binary_path)


def run_compiled_binary(build_result: BuildResult, timeout: int = 10) -> tuple[ExecutionStatus, str]:
    if build_result.binary_path is None:
        return ExecutionStatus.EXCEPTION, "binary is missing"
    return run_cmd([str(build_result.binary_path)], timeout=timeout, cwd=build_result.build_dir)
