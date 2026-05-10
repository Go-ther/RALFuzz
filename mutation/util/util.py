from __future__ import annotations

import glob
import os
import random
import shutil
import subprocess
import time
from enum import IntEnum, auto
from pathlib import Path
from typing import Iterable, Tuple


class ExecutionStatus(IntEnum):
    SUCCESS = auto()
    EXCEPTION = auto()
    CRASH = auto()
    TIMEOUT = auto()


CRASH_RETURN_CODES = {
    -11,
    -10,
    -8,
    -6,
    -5,
    132,
    133,
    134,
    136,
    137,
    138,
    139,
}


def set_seed(seed: int) -> None:
    random.seed(seed)


def normalize_code(code: str) -> str:
    return code.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"


def load_apis(path: str | Path) -> list[str]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return [line.strip() for line in handle.readlines() if line.strip()]


def load_api_defs(path: str | Path) -> dict[str, dict[str, list[str] | str]]:
    api_defs: dict[str, dict[str, list[str] | str]] = {}
    for line in load_apis(path):
        api, argstr = line.split("(", 1)
        args: list[str] = []
        for piece in argstr.rstrip(")").split(","):
            part = piece.strip()
            var = part if "=" not in part else part.split("=", 1)[0].strip()
            if var.isidentifier():
                args.append(var)
        api_defs[api] = {"args": args, "def": line}
    return api_defs


def parse_task(task: tuple[str, str, str]) -> Tuple[str, str, str]:
    return task[0], task[1], task[2]


def read_all_tasks_from_dir(
    directory: str | Path,
    extension: str = ".c",
    collect_mode: str = "all",
    target_api: str | None = None,
) -> list[tuple[str, str, str]]:
    tasks: list[tuple[str, str, str]] = []
    base = Path(directory)
    structured = (base / "valid").exists()
    if structured:
        if collect_mode == "seed":
            subdirs = ["seed"]
        elif collect_mode == "valid":
            subdirs = ["seed", "valid"]
        elif collect_mode == "exception":
            subdirs = ["exception"]
        else:
            subdirs = ["seed", "valid", "exception"]
        for subdir in subdirs:
            pattern = "*" + extension if target_api is None else target_api + "*" + extension
            for source in (base / subdir).glob(pattern):
                label = source.stem
                api = label.rsplit("_", 1)[0]
                tasks.append((api, label, source.read_text(encoding="utf-8")))
    else:
        files = list(base.glob("*" + extension))
        if files:
            for source in files:
                label = source.stem
                api = label.rsplit("_", 1)[0]
                tasks.append((api, label, source.read_text(encoding="utf-8")))
        else:
            for api_dir in base.iterdir():
                if not api_dir.is_dir():
                    continue
                for source in api_dir.glob("*" + extension):
                    api = api_dir.name
                    label = api + "_" + source.stem
                    tasks.append((api, label, source.read_text(encoding="utf-8")))
    tasks.sort()
    return tasks


def parse_result_summary(line: str):
    try:
        if not line.startswith("RALFuzzTestcase"):
            return None, None, None, None
        parts = line.split(" ")
        if len(parts) < 5:
            return None, None, None, None
        return int(parts[1]), parts[2], parts[3], parts[4]
    except Exception:
        return None, None, None, None


def make_temp_build_dir(root: str | Path, prefix: str = "mutation_") -> Path:
    root_path = Path(root).resolve()
    root_path.mkdir(parents=True, exist_ok=True)
    for _ in range(256):
        suffix = "{}_{}".format(int(time.time() * 1000), random.randint(1000, 9999))
        candidate = root_path / (prefix + suffix)
        try:
            candidate.mkdir(parents=False, exist_ok=False)
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError("Unable to create temporary build directory under {}".format(root_path))


def cleanup_dir(path: str | Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def run_cmd(
    cmd_args: Iterable[str],
    timeout: int = 10,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    verbose: bool = False,
) -> tuple[ExecutionStatus, str]:
    try:
        output = subprocess.run(
            list(cmd_args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            cwd=str(cwd) if cwd is not None else None,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return ExecutionStatus.TIMEOUT, ""

    stdout_msg = output.stdout.decode("utf-8", errors="replace")
    stderr_msg = output.stderr.decode("utf-8", errors="replace")
    merged = stdout_msg + stderr_msg
    if verbose:
        print("returncode>", output.returncode)
        print("stdout>", stdout_msg)
        print("stderr>", stderr_msg)

    if output.returncode == 0:
        return ExecutionStatus.SUCCESS, merged

    if output.returncode in CRASH_RETURN_CODES:
        return ExecutionStatus.CRASH, merged

    lower = merged.lower()
    if "addresssanitizer" in lower or "runtime error:" in lower or "heap-buffer-overflow" in lower:
        return ExecutionStatus.CRASH, merged
    if "segmentation fault" in lower or "stack smashing" in lower or "abort" in lower:
        return ExecutionStatus.CRASH, merged
    return ExecutionStatus.EXCEPTION, merged
