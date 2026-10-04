"""Persists every stage output under generated/ so runs are inspectable."""

import json
from pathlib import Path

from pydantic import BaseModel


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def save_text(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def save_json(self, relative: str, data: BaseModel | dict | list) -> Path:
        payload = data.model_dump_json(indent=2) if isinstance(data, BaseModel) else json.dumps(data, indent=2, default=str)
        return self.save_text(relative, payload + "\n")

    def reset_dir(self, relative: str) -> None:
        """Remove stale files (e.g. old .feature files) from an artifact subdirectory."""
        directory = self.root / relative
        if directory.is_dir():
            for child in directory.iterdir():
                if child.is_file():
                    child.unlink()
