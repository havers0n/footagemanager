from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


MediaType = Literal["video", "image", "audio"]


class SourceCreate(BaseModel):
    path: str
    name: str | None = None

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        return str(Path(value).expanduser().resolve())


class SourceOut(BaseModel):
    id: int
    path: str
    name: str
    enabled: bool
    last_scan_at: str | None = None
    last_error: str | None = None
    asset_count: int = 0


class TagUpdate(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=50)


class ExportRequest(BaseModel):
    asset_id: int
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    mode: Literal["fast", "accurate"] = "fast"


class ScreenshotRequest(BaseModel):
    asset_id: int
    timestamp_ms: int = Field(ge=0)


class SearchResult(BaseModel):
    result_id: str
    asset_id: int
    scene_id: int | None = None
    media_type: MediaType
    filename: str
    source_name: str
    path: str
    start_ms: int
    end_ms: int
    duration_ms: int | None
    width: int | None
    height: int | None
    fps: float | None = None
    thumbnail_url: str | None
    preview_url: str
    score: float
    reason: str
    transcript: str | None = None
    tags: list[str] = Field(default_factory=list)


class SearchResponse(BaseModel):
    query: str
    total: int
    results: list[SearchResult]
