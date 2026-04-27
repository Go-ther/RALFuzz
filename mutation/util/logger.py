from __future__ import annotations

from pathlib import Path


class Logger:
    def __init__(self, folder: str | Path) -> None:
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.log_path = self.folder / "generation.log"

    def logo(self, message: str) -> None:
        print(message)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(message)
            handle.write("\n")
