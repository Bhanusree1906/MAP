"""Storage backend for published skill packages and project memory files.

The database holds the metadata (which skill/version, which key). This
module holds the bytes. LocalBlobStorage is local-disk today, but the
interface is narrow (put/get/exists bytes by key) and the on-disk layout
already mirrors an S3 key layout:

    skills/<slug>/<version>/skill.tar.gz
    memories/<project_id>/<version>/MEMORY.md

so an S3BlobStorage later is a new class behind the same interface, not a
rewrite of callers. Keys are backend-relative - never persist an absolute
host path in the database.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_STORAGE_DIR = _REPO_ROOT / "storage"


class BlobStorage(Protocol):
    backend: str

    def put(self, storage_key: str, data: bytes) -> None: ...
    def get(self, storage_key: str) -> bytes: ...
    def exists(self, storage_key: str) -> bool: ...
    def delete(self, storage_key: str) -> None: ...


class LocalBlobStorage:
    backend = "local"

    def __init__(self, base_dir: str | Path | None = None):
        self._base_dir = Path(base_dir or os.getenv("PIPELINE_STORAGE_DIR", str(_DEFAULT_STORAGE_DIR))).resolve()
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, storage_key: str) -> Path:
        if not storage_key or storage_key.startswith("/") or ".." in Path(storage_key).parts:
            raise ValueError(f"illegal storage_key: {storage_key!r}")
        path = (self._base_dir / storage_key).resolve()
        if path != self._base_dir and self._base_dir not in path.parents:
            raise ValueError(f"storage_key escapes the storage root: {storage_key!r}")
        return path

    def put(self, storage_key: str, data: bytes) -> None:
        path = self._path_for(storage_key)
        if path.exists():
            raise FileExistsError(f"{storage_key!r} already exists - published versions are immutable")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, storage_key: str) -> bytes:
        return self._path_for(storage_key).read_bytes()

    def exists(self, storage_key: str) -> bool:
        return self._path_for(storage_key).exists()

    def delete(self, storage_key: str) -> None:
        path = self._path_for(storage_key)
        path.unlink(missing_ok=True)


# Back-compat alias used by older call sites / docs.
SkillStorage = BlobStorage
LocalSkillStorage = LocalBlobStorage


def default_storage() -> LocalBlobStorage:
    return LocalBlobStorage()
