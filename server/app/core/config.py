"""运行配置。

所有配置项都可以用 ``FLOWMASK_`` 前缀的环境变量覆盖，默认值面向本机演示：
数据写在 ``server/data``，不依赖任何外部服务；L2/L3 默认关闭（模型未接入时
识别链会自动降级为 L1-only，并在结果里标记 ``degraded``）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# server/app/core/config.py -> server/
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA_DIR = BASE_DIR / "data"


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(f"FLOWMASK_{name}")
    return default if value is None or value == "" else value


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(f"FLOWMASK_{name}")
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(f"FLOWMASK_{name}")
    if value is None or value == "":
        return default
    return float(value)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(f"FLOWMASK_{name}")
    if value is None or value == "":
        return default
    return int(value)


@dataclass(frozen=True)
class Settings:
    """一份不可变的运行配置快照。"""

    data_dir: Path
    db_path: Path
    # 留空表示不鉴权（本机演示默认）；部署时设置 Bearer Token
    api_token: str
    # 审计哈希链签名密钥（HMAC-SHA256）；生产环境应从密钥管理注入
    audit_secret: str
    enable_l2: bool
    enable_l3: bool
    l2_endpoint: str
    l3_endpoint: str
    l3_model: str
    l2_timeout_s: float
    l3_timeout_s: float
    max_upload_mb: int
    cors_origins: tuple[str, ...]

    @classmethod
    def load(cls) -> "Settings":
        data_dir = Path(_env_str("DATA_DIR", str(DEFAULT_DATA_DIR))).resolve()
        db_path = Path(_env_str("DB_PATH", str(data_dir / "flowmask.db"))).resolve()
        origins = _env_str(
            "CORS_ORIGINS",
            "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8080",
        )
        return cls(
            data_dir=data_dir,
            db_path=db_path,
            api_token=_env_str("API_TOKEN", ""),
            audit_secret=_env_str("AUDIT_SECRET", "flowmask-demo-secret"),
            enable_l2=_env_bool("ENABLE_L2", False),
            enable_l3=_env_bool("ENABLE_L3", False),
            l2_endpoint=_env_str("L2_ENDPOINT", "http://127.0.0.1:8091/v1/classify"),
            l3_endpoint=_env_str("L3_ENDPOINT", "http://127.0.0.1:11434/api/generate"),
            l3_model=_env_str("L3_MODEL", "qwen3guard-stream-0.6b"),
            l2_timeout_s=_env_float("L2_TIMEOUT_S", 0.5),
            l3_timeout_s=_env_float("L3_TIMEOUT_S", 5.0),
            max_upload_mb=_env_int("MAX_UPLOAD_MB", 20),
            cors_origins=tuple(x.strip() for x in origins.split(",") if x.strip()),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例（测试里用 ``get_settings.cache_clear()`` 重置）。"""
    return Settings.load()
