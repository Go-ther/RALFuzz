from __future__ import annotations

from pathlib import Path

from ctitanfuzz.targets import create_target_adapter


def _get_api_defs(api_def_fn):
    with open(api_def_fn, "r", encoding="utf-8") as handle:
        data = handle.read().splitlines()

    api_defs = {}
    for line in data:
        api, argstr = line.split("(", 1)
        args = []
        for piece in argstr.split(","):
            part = piece.strip()
            var = part if "=" not in part else part.split("=", 1)[0]
            if var.isidentifier():
                args.append(var)
        api_defs[api] = {"args": args, "def": line}
    return api_defs


def get_api_defs(lib, target_root: str | Path | None = None):
    package_root = Path(__file__).resolve().parents[1]
    if isinstance(lib, (str, Path)) and Path(str(lib)).exists():
        api_def_fn = Path(str(lib))
        return _get_api_defs(api_def_fn)
    if lib in {"generic", "auto"} and target_root is not None:
        adapter = create_target_adapter(str(lib), package_root)
        adapter.ensure_prepared(target_root)
        return _get_api_defs(adapter.get_api_defs_path())
    raise ValueError("Unsupported library for get_api_defs: {}".format(lib))
