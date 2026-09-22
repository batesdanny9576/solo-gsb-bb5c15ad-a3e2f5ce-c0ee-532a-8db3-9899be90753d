"""ORM 模型：噪声事件、事件簇、审计链。"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Event(Base):
    """单台设备上报的一条噪声事件。

    时间统一归一化为 UTC（naive，SQLite 不保留 tzinfo），
    原始时区信息保存在 source_timezone 字段中供审计展示。
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    source_timezone: Mapped[str] = mapped_column(String(64), nullable=False)

    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    grid_cell: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    # representative：当前簇的保留记录（置信度最高）；
    # merged：归并/冲突中被合并的记录，仍保留以备审计。
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="merged")
    cluster_id: Mapped[int | None] = mapped_column(
        ForeignKey("clusters.id", ondelete="CASCADE"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("ix_events_grid_time", "grid_cell", "start_time", "end_time"),
    )


class Cluster(Base):
    """归并后的事件簇：同一网格、时间上重叠或相邻的事件集合。"""

    __tablename__ = "clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    grid_cell: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # 簇时间跨度 = 全部成员时间范围的并集。
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # 保留记录（置信度最高的成员）。不使用外键约束，避免与 Event.cluster_id 形成循环。
    representative_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    member_count: Mapped[int] = mapped_column(Integer, default=0)

    # active：活跃簇；merged：在级联合并中并入其他簇（记录 merged_into）。
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    merged_into: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class AuditLog(Base):
    """簇级审计链：创建、附着、冲突、代表记录变更、簇合并全程留痕。"""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster_id: Mapped[int] = mapped_column(
        ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    device_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # cluster_created / event_attached / representative_changed / clusters_merged
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 字符串

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
