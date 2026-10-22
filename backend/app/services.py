"""Dataset service: upload -> parse -> profile -> persist, with a DataFrame cache."""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from .api.schemas import ColumnInfo, DatasetInfo
from .engine.loader import LoadedTable, file_kind, load_table
from .engine.profile import TableProfile, frame_to_records, profile_table
from .errors import UserFacingError
from .settings import Settings
from .spec.models import DashboardSpec, SemanticModel
from .storage import Storage


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class DatasetService:
    def __init__(self, storage: Storage, settings: Settings, cache_size: int = 6):
        self.storage = storage
        self.settings = settings
        self._cache: OrderedDict[str, LoadedTable] = OrderedDict()
        self._cache_size = cache_size
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ keys
    @staticmethod
    def _k(dataset_id: str, name: str) -> str:
        if not dataset_id or not all(ch.isalnum() for ch in dataset_id):
            raise UserFacingError("Invalid dataset id.", status_code=404)
        return f"datasets/{dataset_id}/{name}"

    # ------------------------------------------------------------------ create
    def create(self, content: bytes, file_name: str, created_by: str = "") -> DatasetInfo:
        file_kind(file_name)  # validates the extension early
        if len(content) > self.settings.max_upload_bytes:
            raise UserFacingError(f"The file is {len(content) / 1_048_576:.1f} MB; the limit is {self.settings.max_upload_mb} MB.",
                                  status_code=413)
        dataset_id = uuid.uuid4().hex
        ext = Path(file_name).suffix.lower()
        table = self._parse(content, file_name, None)
        self.storage.put(self._k(dataset_id, f"source{ext}"), content)
        meta = {"dataset_id": dataset_id, "file_name": Path(file_name).name, "ext": ext, "sheet": table.sheet,
                "sheet_names": table.sheet_names, "created_at": _now(), "created_by": created_by}
        self.storage.put_json(self._k(dataset_id, "meta.json"), meta)
        self._put_cache(dataset_id, table)
        return self._finish(dataset_id, meta, table)

    def select_sheet(self, dataset_id: str, sheet: str) -> DatasetInfo:
        meta = self._meta(dataset_id)
        if sheet not in meta.get("sheet_names", []):
            raise UserFacingError(f"Sheet '{sheet}' not found. Available: {', '.join(meta.get('sheet_names', []))}")
        content = self.storage.get(self._k(dataset_id, f"source{meta['ext']}"))
        table = self._parse(content, meta["file_name"], sheet)
        meta["sheet"] = sheet
        self.storage.put_json(self._k(dataset_id, "meta.json"), meta)
        for key in ("model.json", "spec.json"):
            self.storage.delete(self._k(dataset_id, key))
        self._put_cache(dataset_id, table)
        return self._finish(dataset_id, meta, table)

    def _parse(self, content: bytes, file_name: str, sheet: str | None) -> LoadedTable:
        return load_table(content, file_name, sheet=sheet, max_rows=self.settings.max_rows,
                          max_columns=self.settings.max_columns)

    def _finish(self, dataset_id: str, meta: dict, table: LoadedTable) -> DatasetInfo:
        profile = profile_table(table.df, table.types, meta["file_name"], table.sheet,
                                sample_rows=self.settings.llm_sample_rows,
                                max_distinct=self.settings.llm_max_distinct_values, warnings=table.warnings,
                                coercion_failures=table.coercion_failures)
        self.storage.put_json(self._k(dataset_id, "profile.json"), profile.model_dump(mode="json"))
        return self._info(meta, table, profile)

    def _info(self, meta: dict, table: LoadedTable, profile: TableProfile) -> DatasetInfo:
        return DatasetInfo(
            dataset_id=meta["dataset_id"], file_name=meta["file_name"], sheet_names=meta.get("sheet_names", []),
            sheet=meta.get("sheet", ""), row_count=profile.row_count, column_count=profile.column_count,
            columns=[ColumnInfo(name=c.name, data_type=c.data_type, nulls=c.nulls, distinct=c.distinct, samples=c.samples)
                     for c in profile.columns],
            preview=frame_to_records(table.df, 10), warnings=profile.warnings, created_at=meta.get("created_at", ""),
            created_by=meta.get("created_by", ""),
        )

    # ------------------------------------------------------------------ read
    def _meta(self, dataset_id: str) -> dict:
        key = self._k(dataset_id, "meta.json")
        if not self.storage.exists(key):
            raise UserFacingError("Dataset not found (uploads expire after a while). Please upload the file again.",
                                  status_code=404)
        return self.storage.get_json(key)

    def exists(self, dataset_id: str) -> bool:
        try:
            return self.storage.exists(self._k(dataset_id, "meta.json"))
        except UserFacingError:
            return False

    def info(self, dataset_id: str) -> DatasetInfo:
        meta = self._meta(dataset_id)
        return self._info(meta, self.table(dataset_id), self.profile(dataset_id))

    def table(self, dataset_id: str) -> LoadedTable:
        with self._lock:
            cached = self._cache.get(dataset_id)
            if cached is not None:
                self._cache.move_to_end(dataset_id)
                return cached
        meta = self._meta(dataset_id)
        content = self.storage.get(self._k(dataset_id, f"source{meta['ext']}"))
        table = self._parse(content, meta["file_name"], meta.get("sheet") or None)
        self._put_cache(dataset_id, table)
        return table

    def frame(self, dataset_id: str) -> pd.DataFrame:
        return self.table(dataset_id).df

    def profile(self, dataset_id: str) -> TableProfile:
        key = self._k(dataset_id, "profile.json")
        if self.storage.exists(key):
            return TableProfile.model_validate(self.storage.get_json(key))
        meta = self._meta(dataset_id)
        table = self.table(dataset_id)
        return profile_table(table.df, table.types, meta["file_name"], table.sheet,
                             sample_rows=self.settings.llm_sample_rows, max_distinct=self.settings.llm_max_distinct_values,
                             warnings=table.warnings, coercion_failures=table.coercion_failures)

    # ------------------------------------------------------------------ model / spec persistence
    def save_model(self, dataset_id: str, model: SemanticModel) -> None:
        self.storage.put_json(self._k(dataset_id, "model.json"), model.model_dump(mode="json"))

    def load_model(self, dataset_id: str) -> SemanticModel | None:
        key = self._k(dataset_id, "model.json")
        return SemanticModel.model_validate(self.storage.get_json(key)) if self.storage.exists(key) else None

    def save_spec(self, dataset_id: str, spec: DashboardSpec) -> None:
        self.storage.put_json(self._k(dataset_id, "spec.json"), spec.to_json_dict())

    def load_spec(self, dataset_id: str) -> DashboardSpec | None:
        key = self._k(dataset_id, "spec.json")
        return DashboardSpec.model_validate(self.storage.get_json(key)) if self.storage.exists(key) else None

    # ------------------------------------------------------------------ cache
    def _put_cache(self, dataset_id: str, table: LoadedTable) -> None:
        with self._lock:
            self._cache[dataset_id] = table
            self._cache.move_to_end(dataset_id)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
