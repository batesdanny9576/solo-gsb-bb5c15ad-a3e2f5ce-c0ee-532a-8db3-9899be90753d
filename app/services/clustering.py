"""核心归并服务。

规则：
1. 位置网格：经纬度按 GRID_SIZE_DEG 取 floor 划分网格，同格才参与归并。
2. 时间相邻：同格事件的时间区间重叠，或空隙 <= MERGE_GAP_SECONDS，即归并。
3. 冲突保留：同簇成员时间重叠时，置信度最高者成为 representative（保留记录），
   其余记录 status=merged 但完整保留，来源设备与全过程写入 audit_log。
4. 级联合并：新事件可能同时与多个现有簇相邻，这些簇会被合并为一个簇，
   被吸收的簇标记 status=merged 并记录 merged_into，审计链不丢失。
"""
from __future__ import annotations

import json
import math
import threading
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..models import AuditLog, Cluster, Event
from ..schemas import EventCreate, to_utc_naive, tz_label

# SQLite 写入需串行化（FastAPI 线程池 + SQLite），归并期间持锁。
_INGEST_LOCK = threading.RLock()


def grid_cell_for(lat: float, lon: float, size: float = config.GRID_SIZE_DEG) -> str:
    """经纬度 -> 网格编号字符串，如 "407500:1163900"。"""
    return f"{math.floor(lat / size)}:{math.floor(lon / size)}"


def _add_audit(
    db: Session,
    cluster_id: int,
    action: str,
    *,
    event_id: int | None = None,
    device_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        cluster_id=cluster_id,
        event_id=event_id,
        device_id=device_id,
        action=action,
        detail=json.dumps(detail, ensure_ascii=False, sort_keys=True, default=str)
        if detail is not None
        else None,
    )
    db.add(entry)
    return entry


def _intervals_overlap(a_start, a_end, b_start, b_end) -> bool:
    """严格区间相交（端点相接不算冲突，但相邻归并由 gap 控制）。"""
    return a_start < b_end and a_end > b_start


def _recompute_cluster(db: Session, cluster: Cluster) -> tuple[bool, Event | None, Event | None]:
    """依据成员重算簇时间跨度、计数与代表记录。

    返回 (代表是否变更, 旧代表, 新代表)。
    """
    members = (
        db.execute(select(Event).where(Event.cluster_id == cluster.id))
        .scalars()
        .all()
    )

    old_rep_id = cluster.representative_event_id
    old_rep = next((m for m in members if m.id == old_rep_id), None)

    # 置信度最高；并列时取开始时间更早，再并列取 id 更小（保证确定性）。
    new_rep = max(members, key=lambda m: (m.confidence, -m.start_time.timestamp(), -m.id))

    cluster.start_time = min(m.start_time for m in members)
    cluster.end_time = max(m.end_time for m in members)
    cluster.max_confidence = new_rep.confidence
    cluster.member_count = len(members)

    changed = new_rep.id != old_rep_id
    for m in members:
        m.status = "representative" if m.id == new_rep.id else "merged"
    if changed:
        cluster.representative_event_id = new_rep.id
    return changed, old_rep, new_rep


def _ingest_one(db: Session, payload: EventCreate) -> tuple[Event, Cluster, list[int]]:
    """在已持锁的会话中处理一条事件，返回 (事件, 所属簇, 被合并的旧簇ID)。"""
    start_utc = to_utc_naive(payload.start_time)
    end_utc = to_utc_naive(payload.end_time)
    cell = grid_cell_for(payload.latitude, payload.longitude)

    event = Event(
        device_id=payload.device_id,
        start_time=start_utc,
        end_time=end_utc,
        source_timezone=payload.timezone or tz_label(payload.start_time),
        latitude=payload.latitude,
        longitude=payload.longitude,
        grid_cell=cell,
        confidence=payload.confidence,
        status="merged",
    )
    db.add(event)
    db.flush()  # 取得 event.id

    gap = timedelta(seconds=config.MERGE_GAP_SECONDS)

    # 同网格、时间区间重叠或相邻（间隙 <= gap）的活跃簇。
    candidates = list(
        db.execute(
            select(Cluster)
            .where(
                Cluster.grid_cell == cell,
                Cluster.status == "active",
                Cluster.start_time <= end_utc + gap,
                Cluster.end_time >= start_utc - gap,
            )
            .order_by(Cluster.start_time, Cluster.id)
        )
        .scalars()
        .all()
    )

    if not candidates:
        # 新建簇，事件即代表记录。
        cluster = Cluster(
            grid_cell=cell,
            start_time=start_utc,
            end_time=end_utc,
            representative_event_id=event.id,
            max_confidence=event.confidence,
            member_count=1,
            status="active",
        )
        db.add(cluster)
        db.flush()
        event.cluster_id = cluster.id
        event.status = "representative"
        _add_audit(
            db,
            cluster.id,
            "cluster_created",
            event_id=event.id,
            device_id=event.device_id,
            detail={
                "confidence": event.confidence,
                "grid_cell": cell,
                "reason": "new_grid_or_gap",
            },
        )
        db.flush()
        return event, cluster, []

    # 多个相邻簇 -> 级联合并，保留开始时间最早（id 最小）的簇作为存续簇。
    survivor = candidates[0]
    absorbed = candidates[1:]
    merged_ids: list[int] = []
    for old in absorbed:
        old_members = (
            db.execute(select(Event).where(Event.cluster_id == old.id))
            .scalars()
            .all()
        )
        for m in old_members:
            m.cluster_id = survivor.id
        old.status = "merged"
        old.merged_into = survivor.id
        merged_ids.append(old.id)
        _add_audit(
            db,
            survivor.id,
            "clusters_merged",
            device_id=event.device_id,
            detail={
                "absorbed_cluster_id": old.id,
                "absorbed_member_count": len(old_members),
                "trigger_event_id": event.id,
            },
        )

    # 冲突判定：新事件与任一既有成员的时间段严格重叠。
    event.cluster_id = survivor.id
    db.flush()
    existing = list(
        db.execute(
            select(Event).where(Event.cluster_id == survivor.id, Event.id != event.id)
        )
        .scalars()
        .all()
    )
    conflicts = [
        {
            "event_id": m.id,
            "device_id": m.device_id,
            "confidence": m.confidence,
        }
        for m in existing
        if _intervals_overlap(start_utc, end_utc, m.start_time, m.end_time)
    ]

    before_rep_id = survivor.representative_event_id
    changed, old_rep, new_rep = _recompute_cluster(db, survivor)

    _add_audit(
        db,
        survivor.id,
        "event_attached",
        event_id=event.id,
        device_id=event.device_id,
        detail={
            "confidence": event.confidence,
            "conflict": bool(conflicts),
            "conflicting_members": conflicts,
            "merged_cluster_ids": merged_ids,
            "resolution": "highest_confidence_wins",
        },
    )

    if changed:
        _add_audit(
            db,
            survivor.id,
            "representative_changed",
            event_id=event.id if new_rep.id == event.id else None,
            device_id=new_rep.device_id,
            detail={
                "previous_representative_event_id": before_rep_id,
                "previous_representative_device_id": old_rep.device_id
                if old_rep
                else None,
                "previous_confidence": old_rep.confidence if old_rep else None,
                "new_representative_event_id": new_rep.id,
                "new_representative_device_id": new_rep.device_id,
                "new_confidence": new_rep.confidence,
            },
        )

    db.flush()
    return event, survivor, merged_ids


def ingest_event(db: Session, payload: EventCreate):
    with _INGEST_LOCK:
        result = _ingest_one(db, payload)
        db.commit()
    return result


def ingest_batch(db: Session, payloads: list[EventCreate]):
    accepted: list[tuple[Event, Cluster, list[int]]] = []
    failed: list[dict] = []
    with _INGEST_LOCK:
        for index, payload in enumerate(payloads):
            try:
                # SAVEPOINT：单条失败不影响整批。
                with db.begin_nested():
                    result = _ingest_one(db, payload)
                accepted.append(result)
            except Exception as exc:  # noqa: BLE001 - 逐条收集错误返回
                failed.append({"index": index, "error": str(exc)})
        db.commit()
    return accepted, failed
