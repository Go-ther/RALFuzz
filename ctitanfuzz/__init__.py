from __future__ import annotations

from pathlib import Path


_SOURCE_ROOT = Path(__file__).resolve().parent.parent / "mutation"
__path__ = [str(_SOURCE_ROOT)]
