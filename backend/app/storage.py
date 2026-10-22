"""Storage abstraction: uploaded files, profiles, specs and job records.

Two implementations share one tiny interface so that switching from a local
directory (single replica / development) to Azure Blob Storage (multiple
replicas) is purely a configuration change (``STORAGE_MODE``).

Keys look like ``datasets/<id>/source.xlsx`` or ``jobs/<id>.json``.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("dashboard.storage")

_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-./]*$")


class StorageError(Exception):
    pass


def validate_key(key: str) -> str:
    if not key or not _KEY_RE.match(key) or ".." in key or key.endswith("/"):
        raise StorageError(f"Invalid storage key: {key!r}")
    return key


class Storage:
    """Interface. All methods are synchronous; the API runs them in worker threads."""

    mode = "abstract"

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        raise NotImplementedError

    def get(self, key: str) -> bytes:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def list_keys(self, prefix: str) -> list[str]:
        raise NotImplementedError

    def modified_at(self, key: str) -> datetime | None:
        raise NotImplementedError

    # convenience ---------------------------------------------------------
    def put_json(self, key: str, obj: Any) -> None:
        self.put(key, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"), "application/json")

    def get_json(self, key: str) -> Any:
        return json.loads(self.get(key).decode("utf-8"))

    def delete_prefix(self, prefix: str) -> int:
        n = 0
        for k in self.list_keys(prefix):
            self.delete(k)
            n += 1
        return n

    def cleanup(self, older_than_hours: int, prefixes: tuple[str, ...] = ("datasets/", "jobs/")) -> int:
        """Delete objects older than the retention window. Returns the number of objects removed."""
        if older_than_hours <= 0:
            return 0
        cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)
        removed = 0
        for prefix in prefixes:
            for key in self.list_keys(prefix):
                try:
                    ts = self.modified_at(key)
                    if ts is not None and ts < cutoff:
                        self.delete(key)
                        removed += 1
                except Exception as exc:  # noqa: BLE001 - cleanup must never crash the app
                    log.warning("cleanup failed for %s: %s", key, exc)
        if removed:
            log.info("storage cleanup removed %d objects older than %dh", removed, older_than_hours)
        return removed


# --------------------------------------------------------------------------- #
class LocalStorage(Storage):
    mode = "local"

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / validate_key(key)).resolve()
        if self.root not in p.parents:
            raise StorageError(f"Key escapes storage root: {key!r}")
        return p

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + f".tmp{time.time_ns()}")
        tmp.write_bytes(data)
        tmp.replace(p)  # atomic on POSIX and Windows

    def get(self, key: str) -> bytes:
        p = self._path(key)
        if not p.exists():
            raise KeyError(key)
        return p.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()

    def list_keys(self, prefix: str) -> list[str]:
        base = self._path(prefix.rstrip("/")) if prefix else self.root
        if not base.exists():
            return []
        if base.is_file():
            return [prefix]
        return sorted(str(p.relative_to(self.root)).replace("\\", "/") for p in base.rglob("*") if p.is_file())

    def modified_at(self, key: str) -> datetime | None:
        p = self._path(key)
        if not p.exists():
            return None
        return datetime.fromtimestamp(p.stat().st_mtime, tz=UTC)

    def delete_prefix(self, prefix: str) -> int:
        n = len(self.list_keys(prefix))
        p = self._path(prefix.rstrip("/"))
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()
        return n


# --------------------------------------------------------------------------- #
class BlobStorage(Storage):
    """Azure Blob Storage. Authenticates with a managed identity (DefaultAzureCredential)
    unless a connection string is supplied for local development."""

    mode = "blob"

    def __init__(self, account_url: str = "", container: str = "dashboard-builder", connection_string: str = ""):
        from azure.storage.blob import BlobServiceClient  # imported lazily: optional dependency at runtime

        if connection_string:
            service = BlobServiceClient.from_connection_string(connection_string)
        elif account_url:
            from azure.identity import DefaultAzureCredential

            service = BlobServiceClient(account_url=account_url, credential=DefaultAzureCredential())
        else:
            raise StorageError("STORAGE_MODE=blob requires BLOB_ACCOUNT_URL (or BLOB_CONNECTION_STRING).")
        self.container = service.get_container_client(container)
        try:
            self.container.create_container()
        except Exception:  # noqa: BLE001 - already exists, or no permission to create (fine if it exists)
            pass

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        from azure.storage.blob import ContentSettings

        self.container.upload_blob(validate_key(key), data, overwrite=True,
                                   content_settings=ContentSettings(content_type=content_type))

    def get(self, key: str) -> bytes:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self.container.download_blob(validate_key(key)).readall()
        except ResourceNotFoundError as exc:
            raise KeyError(key) from exc

    def exists(self, key: str) -> bool:
        return self.container.get_blob_client(validate_key(key)).exists()

    def delete(self, key: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            self.container.delete_blob(validate_key(key))
        except ResourceNotFoundError:
            pass

    def list_keys(self, prefix: str) -> list[str]:
        return [b.name for b in self.container.list_blobs(name_starts_with=prefix)]

    def modified_at(self, key: str) -> datetime | None:
        props = self.container.get_blob_client(validate_key(key)).get_blob_properties()
        return props.last_modified


# --------------------------------------------------------------------------- #
def build_storage(settings) -> Storage:
    mode = (settings.storage_mode or "local").lower()
    if mode == "blob":
        return BlobStorage(settings.blob_account_url, settings.blob_container, settings.blob_connection_string)
    if mode == "local":
        return LocalStorage(settings.storage_local_dir)
    raise StorageError(f"Unknown STORAGE_MODE: {settings.storage_mode!r} (expected 'local' or 'blob')")
