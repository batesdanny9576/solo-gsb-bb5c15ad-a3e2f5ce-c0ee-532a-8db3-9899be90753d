"""端到端测试：归并规则、冲突保留、审计链、时区处理。"""
from __future__ import annotations

import os
import tempfile

import pytest

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["GRID_SIZE_DEG"] = "0.001"
os.environ["MERGE_GAP_SECONDS"] = "300"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    _tmp.close()
    os.unlink(_tmp.name)


LAT, LON = 31.2304, 121.4737  # 约上海
LAT2, LON2 = 39.9042, 116.4074  # 约北京，另一个网格


def post(client, **kw):
    body = {
        "device_id": kw.get("device_id", "dev-x"),
        "start_time": kw["start_time"],
        "end_time": kw["end_time"],
        "latitude": kw.get("latitude", LAT),
        "longitude": kw.get("longitude", LON),
        "confidence": kw.get("confidence", 0.5),
    }
    r = client.post("/events", json=body)
    return r


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_first_event_creates_cluster(client):
    r = post(
        client,
        device_id="dev-A",
        start_time="2026-09-22T10:00:00+08:00",
        end_time="2026-09-22T10:05:00+08:00",
        confidence=0.7,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["event"]["status"] == "representative"
    assert data["event"]["grid_cell"]
    assert data["cluster_id"] == 1
    # UTC 归一化：10:00+08:00 -> 02:00Z
    assert data["event"]["start_time"].startswith("2026-09-22T02:00:00")
    assert data["event"]["start_time"].endswith(("Z", "+00:00"))
    assert data["event"]["source_timezone"] == "+08:00"


def test_overlap_conflict_keeps_highest_confidence(client):
    # 同一时间窗、不同设备、更低置信度 -> 合并到簇 1，代表不变
    r = post(
        client,
        device_id="dev-B",
        start_time="2026-09-22T10:01:00+08:00",
        end_time="2026-09-22T10:06:00+08:00",
        confidence=0.4,
    )
    assert r.json()["cluster_id"] == 1

    r = client.get("/clusters/1")
    cluster = r.json()
    assert cluster["member_count"] == 2
    assert cluster["representative_device_id"] == "dev-A"
    assert cluster["max_confidence"] == 0.7
    statuses = {m["device_id"]: m["status"] for m in cluster["members"]}
    assert statuses == {"dev-A": "representative", "dev-B": "merged"}

    # 更高置信度事件重叠 -> 代表切换，旧记录保留为 merged
    r = post(
        client,
        device_id="dev-C",
        start_time="2026-09-22T10:02:00+08:00",
        end_time="2026-09-22T10:04:00+08:00",
        confidence=0.95,
    )
    assert r.json()["cluster_id"] == 1
    cluster = client.get("/clusters/1").json()
    assert cluster["representative_device_id"] == "dev-C"
    assert cluster["max_confidence"] == 0.95
    statuses = {m["device_id"]: m["status"] for m in cluster["members"]}
    assert statuses["dev-C"] == "representative"
    assert statuses["dev-A"] == "merged"
    assert statuses["dev-B"] == "merged"


def test_adjacent_within_gap_merges(client):
    # 10:10 结束于 02:05Z，下一条 02:09Z 开始，间隙 4 分钟 <= 5 分钟阈值 -> 同簇
    r = post(
        client,
        device_id="dev-D",
        start_time="2026-09-22T10:09:00+08:00",
        end_time="2026-09-22T10:12:00+08:00",
        confidence=0.6,
    )
    assert r.json()["cluster_id"] == 1


def test_gap_too_large_creates_new_cluster(client):
    # 上一簇结束 10:12，此条 10:30 开始，间隙 18 分钟 > 5 分钟 -> 新簇
    r = post(
        client,
        device_id="dev-A",
        start_time="2026-09-22T10:30:00+08:00",
        end_time="2026-09-22T10:33:00+08:00",
        confidence=0.8,
    )
    assert r.json()["cluster_id"] == 2
    assert r.json()["event"]["status"] == "representative"


def test_different_grid_separates(client):
    r = post(
        client,
        device_id="dev-A",
        start_time="2026-09-22T10:00:00+08:00",
        end_time="2026-09-22T10:05:00+08:00",
        confidence=0.9,
        latitude=LAT2,
        longitude=LON2,
    )
    assert r.json()["cluster_id"] == 3


def test_chain_merge_via_bridge_event(client):
    # 两条相距较远的事件各成簇，第三条时间上同时与两者相邻 -> 三簇级联合并
    post(
        client,
        device_id="bridge-1",
        start_time="2026-09-22T14:00:00+08:00",
        end_time="2026-09-22T14:02:00+08:00",
        confidence=0.3,
    )
    post(
        client,
        device_id="bridge-2",
        start_time="2026-09-22T14:10:00+08:00",
        end_time="2026-09-22T14:12:00+08:00",
        confidence=0.3,
    )
    # 14:06-14:07：距前簇 4 分钟、距后簇 3 分钟，均在 gap 内
    r = post(
        client,
        device_id="bridge-mid",
        start_time="2026-09-22T14:06:00+08:00",
        end_time="2026-09-22T14:07:00+08:00",
        confidence=0.5,
    )
    body = r.json()
    assert body["merged_cluster_ids"]  # 至少吸收了一个既有簇
    survivor = body["cluster_id"]
    detail = client.get(f"/clusters/{survivor}").json()
    assert detail["member_count"] == 3

    listing = client.get("/clusters").json()
    active_ids = {c["id"] for c in listing["items"]}
    assert survivor in active_ids


def test_timezone_normalization(client):
    # 同一绝对时刻，+00:00 与 +08:00 表达不同，应按 UTC 比较后重叠归并
    r = post(
        client,
        device_id="tz-utc",
        start_time="2026-09-23T00:00:00+00:00",
        end_time="2026-09-23T00:05:00+00:00",
        confidence=0.6,
    )
    cid1 = r.json()["cluster_id"]
    r = post(
        client,
        device_id="tz-cst",
        start_time="2026-09-23T08:01:00+08:00",
        end_time="2026-09-23T08:04:00+08:00",
        confidence=0.8,
    )
    assert r.json()["cluster_id"] == cid1
    assert r.json()["event"]["source_timezone"] == "+08:00"


def test_audit_chain_content(client):
    entries = client.get("/clusters/1/audit").json()["entries"]
    actions = [e["action"] for e in entries]
    assert actions[0] == "cluster_created"
    assert "event_attached" in actions
    assert "representative_changed" in actions

    # 冲突事件的审计明细应记录对手与解决规则
    attached = [e for e in entries if e["action"] == "event_attached"]
    conflict_entries = [e for e in attached if '"conflict": true' in (e["detail"] or "")]
    assert conflict_entries
    for e in conflict_entries:
        assert "highest_confidence_wins" in e["detail"]


def test_validation_errors(client):
    # 缺时区
    r = post(
        client,
        start_time="2026-09-22T10:00:00",
        end_time="2026-09-22T10:05:00",
    )
    assert r.status_code == 422

    # end <= start
    r = post(
        client,
        start_time="2026-09-22T10:05:00+08:00",
        end_time="2026-09-22T10:00:00+08:00",
    )
    assert r.status_code == 422

    # 越界坐标 / 置信度
    r = post(
        client,
        start_time="2026-09-22T10:00:00+08:00",
        end_time="2026-09-22T10:05:00+08:00",
        latitude=91.0,
    )
    assert r.status_code == 422


def test_batch_and_filters(client):
    body = [
        {
            "device_id": "batch-1",
            "start_time": "2026-09-24T09:00:00+08:00",
            "end_time": "2026-09-24T09:02:00+08:00",
            "latitude": LAT,
            "longitude": LON,
            "confidence": 0.5,
        },
        {
            "device_id": "batch-bad",
            "start_time": "2026-09-24T10:00:00+08:00",
            "end_time": "2026-09-24T09:00:00+08:00",
            "latitude": LAT,
            "longitude": LON,
            "confidence": 0.5,
        },
    ]
    r = client.post("/events/batch", json=body)
    assert r.status_code == 200
    assert len(r.json()["accepted"]) == 1
    assert len(r.json()["failed"]) == 1

    # 过滤
    r = client.get("/events", params={"device_id": "dev-A"})
    assert all(e["device_id"] == "dev-A" for e in r.json()["items"])

    r = client.get("/clusters", params={"grid_cell": "39904:116407"})
    items = r.json()["items"]
    assert items and all(c["grid_cell"] == "39904:116407" for c in items)


def test_merged_cluster_redirects_in_listing(client):
    # 被级联合并吸收的簇：直接 GET 仍可见，且带 merged_into
    r = client.get("/clusters", params={"include_merged": "true"})
    merged = [c for c in r.json()["items"] if c["status"] == "merged"]
    assert merged
    for c in merged:
        assert c["merged_into"] is not None
        detail = client.get(f"/clusters/{c['id']}").json()
        assert detail["status"] == "merged"
