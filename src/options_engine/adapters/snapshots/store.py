"""Immutable, content-addressed filesystem store for snapshots and fit artifacts.

Objects are written once into a temporary directory and atomically renamed;
an existing object id is never overwritten.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from options_engine.domain.errors import DomainError, ErrorCode

ID_PATTERN = re.compile(r"^(snap|fit|heston|svi)-[0-9a-f]{16}$")


def default_data_dir() -> Path:
    return Path(os.environ.get("OPTIONS_ENGINE_DATA_DIR", "data")).resolve()


class ObjectStore:
    def __init__(self, root: Path, kind: str) -> None:
        self.root = root / kind
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, object_id: str) -> Path:
        if not ID_PATTERN.match(object_id):
            raise DomainError(ErrorCode.INVALID_REQUEST, f"malformed id '{object_id}'")
        return self.root / object_id

    def exists(self, object_id: str) -> bool:
        return self._path(object_id).is_dir()

    def put(self, object_id: str, files: dict[str, bytes]) -> bool:
        """Write all files atomically. Returns False (and writes nothing) if it exists."""
        target = self._path(object_id)
        if target.exists():
            return False
        tmp = Path(tempfile.mkdtemp(prefix=f".{object_id}.", dir=self.root))
        for name, data in files.items():
            (tmp / name).write_bytes(data)
            os.chmod(tmp / name, 0o444)
        try:
            os.rename(tmp, target)
        except OSError:  # lost a race with an identical writer
            for f in tmp.iterdir():
                f.unlink()
            tmp.rmdir()
            return False
        return True

    def read_bytes(self, object_id: str, name: str) -> bytes:
        path = self._path(object_id) / name
        if not path.is_file():
            raise DomainError(ErrorCode.NOT_FOUND, f"{object_id}/{name} not found")
        return path.read_bytes()

    def read_json(self, object_id: str, name: str) -> Any:
        return json.loads(self.read_bytes(object_id, name))

    def ids(self) -> list[str]:
        return sorted(
            p.name for p in self.root.iterdir() if p.is_dir() and ID_PATTERN.match(p.name)
        )
