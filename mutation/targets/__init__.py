from __future__ import annotations

from pathlib import Path

from ctitanfuzz.targets.generic import GenericCTargetAdapter


def create_target_adapter(name: str, package_root: str | Path):
    if name in {"generic", "auto"}:
        return GenericCTargetAdapter(package_root)
    raise ValueError("Unsupported target: {}".format(name))
