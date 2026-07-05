from __future__ import annotations

from pathlib import Path

from mutation.targets.generic import GenericCTargetAdapter


def create_target_adapter(name: str, package_root: str | Path, **kwargs):
    if name in {"generic", "auto"}:
        return GenericCTargetAdapter(package_root, **kwargs)
    raise ValueError("Unsupported target: {}".format(name))
