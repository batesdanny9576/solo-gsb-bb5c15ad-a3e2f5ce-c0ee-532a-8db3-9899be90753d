"""应用配置（全部可通过环境变量覆盖）。"""
from __future__ import annotations

import os


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


# SQLite 数据库地址。容器内默认持久化到 /data；本地开发默认当前目录。
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./soundscape.db")

# 位置网格边长（度）。约 0.001 度 ≈ 111 米。同一网格内的事件才参与归并。
GRID_SIZE_DEG = _env_float("GRID_SIZE_DEG", 0.001)

# 时间相邻阈值（秒）：两个事件时间段重叠，或时间空隙小于该值，即视为相邻。
MERGE_GAP_SECONDS = _env_int("MERGE_GAP_SECONDS", 300)

# API 服务监听配置。
HOST = os.getenv("HOST", "0.0.0.0")
PORT = _env_int("PORT", 8000)
