"""事件上报与查询路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Event
from ..schemas import BatchIngestResult, EventCreate, IngestResult
from ..serializers import serialize_event
from ..services import clustering

router = APIRouter(prefix="/events", tags=["events"])


def _to_result(result) -> IngestResult:
    event, cluster, merged_ids = result
    return IngestResult(
        event=serialize_event(event),
        cluster_id=cluster.id,
        cluster_status=cluster.status,
        merged_cluster_ids=merged_ids,
    )


@router.post("", response_model=IngestResult, status_code=201)
def create_event(payload: EventCreate, db: Session = Depends(get_db)) -> IngestResult:
    """上报单条噪声事件，立即执行网格 + 时间相邻归并。"""
    return _to_result(clustering.ingest_event(db, payload))


@router.post("/batch", response_model=BatchIngestResult, status_code=200)
async def create_events(
    request: Request, db: Session = Depends(get_db)
) -> BatchIngestResult:
    """批量上报；逐条校验，单条失败通过 failed 返回，不影响其余事件。"""
    try:
        raw = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="请求体必须为 JSON 数组")
    if not isinstance(raw, list):
        raise HTTPException(status_code=422, detail="请求体必须为 JSON 数组")

    payloads: list[EventCreate] = []
    failed: list[dict] = []
    for index, item in enumerate(raw):
        try:
            payloads.append(EventCreate.model_validate(item))
        except ValidationError as exc:
            failed.append(
                {
                    "index": index,
                    "errors": [
                        {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                        for e in exc.errors()
                    ],
                }
            )

    accepted, ingest_failed = clustering.ingest_batch(db, payloads)
    failed.extend(ingest_failed)
    return BatchIngestResult(
        accepted=[_to_result(r) for r in accepted], failed=failed
    )


@router.get("/{event_id}")
def get_event(event_id: int, db: Session = Depends(get_db)) -> dict:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    return serialize_event(event)


@router.get("")
def list_events(
    device_id: str | None = None,
    cluster_id: int | None = None,
    grid_cell: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(Event).order_by(Event.start_time, Event.id)
    if device_id:
        stmt = stmt.where(Event.device_id == device_id)
    if cluster_id is not None:
        stmt = stmt.where(Event.cluster_id == cluster_id)
    if grid_cell:
        stmt = stmt.where(Event.grid_cell == grid_cell)
    stmt = stmt.limit(min(limit, 500)).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return {
        "items": [serialize_event(e) for e in rows],
        "limit": limit,
        "offset": offset,
        "count": len(rows),
    }
