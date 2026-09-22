"""Pydantic 输入/输出模型。

输入时间必须为带时区的 ISO 8601（如 2026-09-22T10:00:00+08:00），
内部统一换算为 UTC 存储，输出一律使用 UTC（带 Z 后缀）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def to_utc_naive(dt: datetime) -> datetime:
    """带时区时间 -> UTC naive（写库用）。"""
    if dt.tzinfo is None:
        raise ValueError("时间必须携带时区信息，例如 2026-09-22T10:00:00+08:00")
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def tz_label(dt: datetime) -> str:
    """原始时区标签，例如 +08:00 / +05:30 / UTC。"""
    offset = dt.utcoffset()
    if offset is None:
        return "UTC"
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


class EventCreate(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=128, examples=["device-A01"])
    start_time: datetime = Field(..., examples=["2026-09-22T10:00:00+08:00"])
    end_time: datetime = Field(..., examples=["2026-09-22T10:05:00+08:00"])
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    # 可选：设备原始时区名（如 Asia/Shanghai）；缺省时由时间偏移推导。
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("start_time", "end_time")
    @classmethod
    def _check_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("时间必须携带时区偏移，例如 2026-09-22T10:00:00+08:00")
        return v

    @model_validator(mode="after")
    def _check_range(self) -> "EventCreate":
        if self.end_time <= self.start_time:
            raise ValueError("end_time 必须晚于 start_time")
        return self


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: str
    start_time: datetime
    end_time: datetime
    source_timezone: str
    latitude: float
    longitude: float
    grid_cell: str
    confidence: float
    status: str
    cluster_id: int | None
    created_at: datetime


class ClusterOut(BaseModel):
    id: int
    grid_cell: str
    start_time: datetime
    end_time: datetime
    representative_event_id: int | None
    representative_device_id: str | None = None
    max_confidence: float
    member_count: int
    status: str
    merged_into: int | None = None
    created_at: datetime
    updated_at: datetime


class ClusterDetail(ClusterOut):
    members: list[EventOut] = []


class AuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cluster_id: int
    event_id: int | None
    device_id: str | None
    action: str
    detail: str | None
    created_at: datetime


class AuditChain(BaseModel):
    cluster: ClusterOut
    entries: list[AuditEntry]


class IngestResult(BaseModel):
    event: EventOut
    cluster_id: int
    cluster_status: str
    merged_cluster_ids: list[int]


class BatchIngestResult(BaseModel):
    accepted: list[IngestResult]
    failed: list[dict]


class Message(BaseModel):
    detail: str
