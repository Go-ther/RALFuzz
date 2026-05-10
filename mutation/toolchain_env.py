from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


_CLANG_BASENAMES = {"clang", "clang.exe"}


def normalize_clang_compiler(compiler: str | None, *, source: str = "compiler") -> str:
    """Return a clang compiler argument, rejecting accidental gcc/msvc use."""
    text = (compiler or "").strip() or "clang"
    if Path(text).name.lower() not in _CLANG_BASENAMES:
        raise ValueError(f"{source} must be clang; got {compiler!r}")
    return text


def default_coverage_tool() -> str:
    return "llvm-cov gcov"


def configure_clang_environment(
    *,
    compiler: str | None = None,
    enable_sanitizer: bool = False,
    env: dict[str, str] | None = None,
    require_clang: bool = True,
) -> dict[str, str]:
    """Prepare PATH so clang and, on Windows, sanitizer DLLs are discoverable."""
    target = env if env is not None else os.environ
    clang_exe = resolve_clang_executable(compiler, env=target)
    if clang_exe is None:
        if require_clang:
            raise RuntimeError(
                "clang was not found. Install LLVM/clang and put its bin directory on PATH, "
                "or pass --compiler /path/to/clang."
            )
        return target

    path_entries = [str(clang_exe.parent)]
    if enable_sanitizer:
        runtime_dir = resolve_windows_sanitizer_runtime(clang_exe)
        if runtime_dir is not None:
            path_entries.append(str(runtime_dir))
        elif os.name == "nt":
            raise RuntimeError(
                "clang was found, but the Windows sanitizer runtime directory could not be resolved. "
                "Install LLVM's sanitizer runtime files or rerun without sanitizer instrumentation."
            )

    target["PATH"] = _prepend_path_entries(target.get("PATH", ""), path_entries)
    return target


def resolve_clang_executable(
    compiler: str | None = None,
    *,
    env: dict[str, str] | None = None,
) -> Path | None:
    target = env if env is not None else os.environ
    text = normalize_clang_compiler(compiler or target.get("RALFUZZ_CLANG"), source="clang executable")

    explicit = Path(text)
    if explicit.is_absolute() or explicit.parent != Path("."):
        return explicit.resolve() if explicit.exists() else None

    found = shutil.which(text, path=target.get("PATH"))
    if found:
        return Path(found).resolve()

    if os.name == "nt":
        default_install = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "LLVM" / "bin" / "clang.exe"
        if default_install.exists():
            return default_install.resolve()
    return None


def resolve_windows_sanitizer_runtime(clang_exe: Path) -> Path | None:
    if os.name != "nt":
        return None

    runtime_dir = _query_clang_runtime_dir(clang_exe)
    candidates: list[Path] = [clang_exe.parent]
    if runtime_dir is not None:
        candidates.extend(
            [
                runtime_dir,
                runtime_dir / "windows",
                runtime_dir.parent / "windows",
            ]
        )

    for candidate in candidates:
        if _contains_windows_sanitizer_runtime(candidate):
            return candidate.resolve()
    return None


def _query_clang_runtime_dir(clang_exe: Path) -> Path | None:
    try:
        result = subprocess.run(
            [str(clang_exe), "-print-runtime-dir"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    text = result.stdout.strip()
    return Path(text) if text else None


def _contains_windows_sanitizer_runtime(path: Path) -> bool:
    if not path.exists():
        return False
    return any(path.glob("clang_rt.*san*.dll")) or any(path.glob("clang_rt.*ubsan*.dll"))


def _prepend_path_entries(existing: str, new_entries: list[str]) -> str:
    current = [entry for entry in existing.split(os.pathsep) if entry]
    seen = {entry.casefold() for entry in current}
    merged: list[str] = []
    for entry in new_entries:
        key = entry.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(entry)
    merged.extend(current)
    return os.pathsep.join(merged)
