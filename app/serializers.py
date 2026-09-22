"""响应序列化辅助。"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import AuditLog, Cluster, Event


def utc_z(dt: datetime) -> datetime:
    """naive UTC -> aware UTC，保证 JSON 输出带 Z 后缀。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def serialize_event(event: Event) -> dict:
    return {
        "id": event.id,
        "device_id": event.device_id,
        "start_time": utc_z(event.start_time),
        "end_time": utc_z(event.end_time),
        "source_timezone": event.source_timezone,
        "latitude": event.latitude,
        "longitude": event.longitude,
        "grid_cell": event.grid_cell,
        "confidence": event.confidence,
        "status": event.status,
        "cluster_id": event.cluster_id,
        "created_at": utc_z(event.created_at),
    }


def serialize_cluster(db: Session, cluster: Cluster) -> dict:
    rep_device = None
    if cluster.representative_event_id is not None:
        rep = db.get(Event, cluster.representative_event_id)
        rep_device = rep.device_id if rep else None
    return {
        "id": cluster.id,
        "grid_cell": cluster.grid_cell,
        "start_time": utc_z(cluster.start_time),
        "end_time": utc_z(cluster.end_time),
        "representative_event_id": cluster.representative_event_id,
        "representative_device_id": rep_device,
        "max_confidence": cluster.max_confidence,
        "member_count": cluster.member_count,
        "status": cluster.status,
        "merged_into": cluster.merged_into,
        "created_at": utc_z(cluster.created_at),
        "updated_at": utc_z(cluster.updated_at),
    }


def serialize_audit(entry: AuditLog) -> dict:
    return {
        "id": entry.id,
        "cluster_id": entry.cluster_id,
        "event_id": entry.event_id,
        "device_id": entry.device_id,
        "action": entry.action,
        "detail": entry.detail,
        "created_at": utc_z(entry.created_at),
    }
