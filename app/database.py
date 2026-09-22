"""SQLite 引擎与会话管理。"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from . import config


class Base(DeclarativeBase):
    pass


# check_same_thread=False 允许 FastAPI 线程池共用连接；
# 串行写入由 services.clustering._INGEST_LOCK 兜底。
_connect_args = (
    {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}
)
engine = create_engine(
    config.DATABASE_URL,
    connect_args=_connect_args,
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """建表（幂等）。应用启动时调用。"""
    # 触发模型注册，确保 metadata 中包含全部表。
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：按请求提供数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
