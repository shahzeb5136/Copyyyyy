"""Request/response models for the HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..engine.compat import CompatibilityReport
from ..engine.dashboard import DashboardResult
from ..spec.models import DashboardSpec, SemanticModel


class ColumnInfo(BaseModel):
    name: str
    data_type: str
    nulls: int = 0
    distinct: int = 0
    samples: list[str] = Field(default_factory=list)


class DatasetInfo(BaseModel):
    dataset_id: str
    file_name: str
    sheet_names: list[str] = Field(default_factory=list)
    sheet: str = ""
    row_count: int = 0
    column_count: int = 0
    columns: list[ColumnInfo] = Field(default_factory=list)
    preview: dict[str, Any] = Field(default_factory=dict)  # {columns, rows}
    warnings: list[str] = Field(default_factory=list)
    created_at: str = ""
    created_by: str = ""


class SheetRequest(BaseModel):
    sheet: str


class ChatTurn(BaseModel):
    role: str = "user"
    content: str = ""


class RefineModelRequest(BaseModel):
    model: SemanticModel
    message: str
    history: list[ChatTurn] = Field(default_factory=list)


class BuildRequest(BaseModel):
    model: SemanticModel
    generate_narratives: bool = True


class RenderRequest(BaseModel):
    spec: DashboardSpec


class RefineDashboardRequest(BaseModel):
    spec: DashboardSpec
    message: str
    history: list[ChatTurn] = Field(default_factory=list)
    generate_narratives: bool = True


class NarrativesRequest(BaseModel):
    spec: DashboardSpec
    widget_ids: list[str] | None = None
    force: bool = False


class AskHistoryItem(BaseModel):
    question: str = ""
    summary: str = ""


class AskRequest(BaseModel):
    question: str
    model: SemanticModel
    history: list[AskHistoryItem] = Field(default_factory=list)


class InferResult(BaseModel):
    """Result payload of the infer / refine-model jobs."""

    model: SemanticModel
    reply: str = ""


class JobSubmitted(BaseModel):
    job_id: str
    status: str = "queued"


class DashboardPayload(BaseModel):
    spec: dict[str, Any]
    dashboard: DashboardResult
    warnings: list[str] = Field(default_factory=list)
    reply: str = ""
    changes: dict[str, list[str]] = Field(default_factory=dict)


class ReplayResponse(BaseModel):
    dataset: DatasetInfo
    spec: dict[str, Any]
    dashboard: DashboardResult
    compatibility: CompatibilityReport
    warnings: list[str] = Field(default_factory=list)
