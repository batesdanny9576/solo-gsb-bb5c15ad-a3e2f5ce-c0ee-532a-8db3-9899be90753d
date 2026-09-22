"""事件簇查询与审计链路由。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AuditLog, Cluster, Event
from ..schemas import AuditChain, AuditEntry, ClusterDetail
from ..serializers import serialize_audit, serialize_cluster, serialize_event

router = APIRouter(prefix="/clusters", tags=["clusters"])


def _parse_dt(name: str, value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise HTTPException(status_code=422, detail=f"{name} 必须携带时区信息")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _get_cluster_or_404(db: Session, cluster_id: int) -> Cluster:
    cluster = db.get(Cluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=404, detail="事件簇不存在")
    return cluster


@router.get("")
def list_clusters(
    grid_cell: str | None = None,
    include_merged: bool = False,
    start_from: datetime | None = Query(default=None),
    start_to: datetime | None = Query(default=None),
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> dict:
    """列出事件簇，可按网格 / 时间窗（与簇时间跨度相交）过滤。"""
    t_from = _parse_dt("start_from", start_from)
    t_to = _parse_dt("start_to", start_to)

    conditions = []
    if grid_cell:
        conditions.append(Cluster.grid_cell == grid_cell)
    if not include_merged:
        conditions.append(Cluster.status == "active")
    if t_from is not None:
        conditions.append(Cluster.end_time >= t_from)
    if t_to is not None:
        conditions.append(Cluster.start_time <= t_to)

    stmt = select(Cluster)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = stmt.order_by(Cluster.start_time, Cluster.id).limit(
        min(limit, 500)
    ).offset(offset)

    rows = db.execute(stmt).scalars().all()
    return {
        "items": [serialize_cluster(db, c) for c in rows],
        "limit": limit,
        "offset": offset,
        "count": len(rows),
    }


@router.get("/{cluster_id}", response_model=ClusterDetail)
def get_cluster(cluster_id: int, db: Session = Depends(get_db)) -> ClusterDetail:
    """簇详情，含全部成员（含被合并/取代的记录）。"""
    cluster = _get_cluster_or_404(db, cluster_id)
    members = (
        db.execute(
            select(Event)
            .where(Event.cluster_id == cluster_id)
            .order_by(Event.start_time, Event.id)
        )
        .scalars()
        .all()
    )
    data = serialize_cluster(db, cluster)
    data["members"] = [serialize_event(m) for m in members]
    return ClusterDetail(**data)


@router.get("/{cluster_id}/audit", response_model=AuditChain)
def get_cluster_audit(cluster_id: int, db: Session = Depends(get_db)) -> AuditChain:
    """返回簇及其完整审计链（创建/附着/冲突/代表变更/簇合并）。"""
    cluster = _get_cluster_or_404(db, cluster_id)
    entries = (
        db.execute(
            select(AuditLog)
            .where(AuditLog.cluster_id == cluster_id)
            .order_by(AuditLog.id)
        )
        .scalars()
        .all()
    )
    return AuditChain(
        cluster=serialize_cluster(db, cluster),
        entries=[AuditEntry(**serialize_audit(e)) for e in entries],
    )
