# 声景巡检事件归并 API

基于 **FastAPI + SQLite** 的噪声事件归并服务。接收不同设备上报的带时区时间段、位置与置信度的噪声事件，
按**位置网格 + 时间相邻关系**实时归并为事件簇；时间段重叠冲突时**保留置信度最高的记录**作为簇代表，
其余记录与来源设备完整保留，并通过审计链回放归并全过程。

## 归并规则

1. **位置网格**：经纬度按 `GRID_SIZE_DEG`（默认 `0.001°` ≈ 111 m）做 floor 划分，网格编号形如 `31230:121470`，只有同网格事件才参与归并。
2. **时间相邻**：同网格内，两个时间段**重叠**，或空隙 ≤ `MERGE_GAP_SECONDS`（默认 300 秒）即归入同一簇。时间一律换算为 UTC 比较，跨时区上报（`+08:00`、`Z` 等）正确归一。
3. **冲突保留**：同簇成员时间重叠时，置信度最高的成员成为 `representative`（保留记录），其余成员置为 `merged` 但不删除；并列时取开始时间更早、再取 id 更小者，保证结果确定。
4. **级联合并**：新事件可能同时桥接多个现有簇，这些簇会合并为一个；被吸收的簇标记 `status=merged` 并记录 `merged_into`，其审计条目迁入存续簇，链路不丢失。

每次归并向 `audit_log` 写入：`cluster_created`、`event_attached`（含冲突对手与来源设备）、`representative_changed`、`clusters_merged`。

## 快速启动（Docker）

```bash
docker compose up --build
```

启动即自动建库建表（幂等），SQLite 数据持久化在命名卷 `soundscape-data`（容器内 `/data/soundscape.db`）。

- API 地址：<http://localhost:8000>
- 交互式文档（Swagger UI）：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/health>

## 本地运行

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

配置（环境变量）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./soundscape.db`（本地）/ `sqlite:////data/soundscape.db`（容器） | SQLAlchemy 数据库地址 |
| `GRID_SIZE_DEG` | `0.001` | 位置网格边长（度） |
| `MERGE_GAP_SECONDS` | `300` | 时间相邻阈值（秒） |
| `PORT` | `8000` | 监听端口 |

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/events` | 上报单条事件，立即归并 |
| `POST` | `/events/batch` | 批量上报，逐条校验（失败项在 `failed` 中返回） |
| `GET` | `/events` | 事件列表，可按 `device_id` / `cluster_id` / `grid_cell` 过滤 |
| `GET` | `/events/{id}` | 事件详情 |
| `GET` | `/clusters` | 簇列表，可按 `grid_cell`、时间窗（`start_from`/`start_to`）过滤 |
| `GET` | `/clusters/{id}` | 簇详情 + 全部成员（含被合并/取代记录） |
| `GET` | `/clusters/{id}/audit` | 簇及完整审计链 |
| `GET` | `/health` | 健康检查 |

## 使用示例

上报两条**重叠**事件（不同设备、不同置信度）：

```bash
curl -s -X POST localhost:8000/events -H 'Content-Type: application/json' -d '{
  "device_id": "device-A01",
  "start_time": "2026-09-22T10:00:00+08:00",
  "end_time":   "2026-09-22T10:05:00+08:00",
  "latitude": 31.2304, "longitude": 121.4737,
  "confidence": 0.6
}'

curl -s -X POST localhost:8000/events -H 'Content-Type: application/json' -d '{
  "device_id": "device-B02",
  "start_time": "2026-09-22T02:02:00Z",
  "end_time":   "2026-09-22T02:07:00Z",
  "latitude": 31.2305, "longitude": 121.4739,
  "confidence": 0.91
}'
```

两条事件虽分别使用 `+08:00` 与 `Z` 表达，仍按 UTC 判定为重叠、同网格 → 归入同一簇；
`device-B02`（0.91）成为代表记录。

查询簇与审计链：

```bash
curl -s localhost:8000/clusters/1 | python -m json.tool
curl -s localhost:8000/clusters/1/audit | python -m json.tool
```

`event_attached` 审计明细示例（记录了冲突对手、来源与解决规则）：

```json
{
  "action": "event_attached",
  "device_id": "device-B02",
  "detail": "{\"conflict\": true, \"conflicting_members\": [{\"event_id\": 1, \"device_id\": \"device-A01\", \"confidence\": 0.6}], \"resolution\": \"highest_confidence_wins\"}"
}
```

时间上**相邻但不重叠**（间隔 ≤ 300 秒）的事件同样归并；间隔超过阈值或落在不同网格则新建簇。
一条“桥接”事件可在单次上报中把多个既有簇级联合并，返回的 `merged_cluster_ids` 给出被吸收簇。

## 数据模型

- `events`：设备事件原始记录（UTC 时间、原始时区标签、网格、置信度、状态、所属簇）
- `clusters`：归并簇（时间跨度、代表记录 id、最大置信度、成员计数、合并去向）
- `audit_log`：簇级审计链（动作、关联事件/设备、JSON 明细、时间戳）

## 测试

```bash
python -m pytest tests/ -v
```

覆盖：建簇、重叠冲突置信度取胜、相邻归并、超阈值分裂、网格隔离、时区归一、级联合并、审计链内容与入参校验（共 12 项）。

## 项目结构

```
app/
├── main.py                # FastAPI 应用与启动建表（lifespan）
├── config.py              # 环境变量配置
├── database.py            # SQLite 引擎 / 会话 / 建表
├── models.py              # Event / Cluster / AuditLog
├── schemas.py             # Pydantic 输入输出与时区校验
├── serializers.py         # UTC 序列化
├── services/clustering.py # 网格划分、相邻归并、冲突保留、级联合并
└── routers/
    ├── events.py          # 事件上报 / 批量 / 查询
    └── clusters.py        # 簇列表 / 详情 / 成员 / 审计链
tests/test_api.py
Dockerfile
docker-compose.yml
```
