"""声景巡检事件归并 API。

启动即初始化数据库（建表，幂等）并暴露 HTTP 端口。
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .database import init_db
from .routers import clusters, events


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 确保 SQLite 文件所在目录存在（容器内 /data 由卷挂载）。
    db_url = os.getenv("DATABASE_URL", "sqlite:///./soundscape.db")
    if db_url.startswith("sqlite:////"):
        db_dir = os.path.dirname(db_url.replace("sqlite:///", "", 1))
        os.makedirs(db_dir, exist_ok=True)
    init_db()
    yield


app = FastAPI(
    title="声景巡检事件归并 API",
    description=(
        "接收多设备上报的带时区噪声事件，按位置网格与时间相邻关系归并为事件簇；"
        "重叠冲突时保留置信度最高记录并记录来源，支持簇与成员审计链查询。"
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(events.router)
app.include_router(clusters.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "soundscape-event-clustering"}


@app.get("/", tags=["meta"])
def root() -> dict:
    return {
        "service": "声景巡检事件归并 API",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }
