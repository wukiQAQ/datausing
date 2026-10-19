"""SQLite 存储层。

刻意不引 ORM/SQLAlchemy：单进程一体机 + 三个实体（任务、检测结果、审计日志），
用标准库 ``sqlite3`` 更透明，评审时也容易讲清楚每张表的作用。表结构见 ``SCHEMA``。
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id             TEXT PRIMARY KEY,
  created_at     TEXT NOT NULL,
  finished_at    TEXT,
  status         TEXT NOT NULL,           -- pending / running / done / failed
  source         TEXT NOT NULL,           -- 文件名（上传）或 text（粘贴）
  content_type   TEXT NOT NULL,           -- json / csv / sql / text / docx / pdf
  options        TEXT NOT NULL,           -- JSON：级别、算子开关、是否启用 L2/L3
  total_segments INTEGER NOT NULL DEFAULT 0,
  total_findings INTEGER NOT NULL DEFAULT 0,
  error          TEXT
);

CREATE TABLE IF NOT EXISTS findings (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id           TEXT NOT NULL,
  path              TEXT NOT NULL,        -- JSON Path / 列名+行号 / SQL 字面量位置
  level             TEXT NOT NULL,        -- L3 / L2 / L1 / SAFE
  score             REAL NOT NULL,
  types             TEXT NOT NULL,        -- 逗号分隔的英文类别键：id_card,phone
  hits              INTEGER NOT NULL,
  masked            TEXT NOT NULL,        -- 脱敏副本（永不存原文）
  original_hash     TEXT NOT NULL,        -- 原文 SHA-256，用于审计对位而不泄露内容
  model_chain       TEXT NOT NULL,        -- l1 / l1>l2 / l1>l2>l3
  confidence_vector TEXT,                 -- JSON：[{type, confidence, weight}]
  rule_id           TEXT,                 -- 命中的规则名（逗号分隔）
  degraded          INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_task  ON findings(task_id, id);
CREATE INDEX IF NOT EXISTS idx_findings_level ON findings(task_id, level);

CREATE TABLE IF NOT EXISTS audit_log (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  ts                TEXT NOT NULL,
  task_id           TEXT NOT NULL,
  input_hash        TEXT NOT NULL,
  output_hash       TEXT NOT NULL,
  model_chain       TEXT NOT NULL,
  confidence_vector TEXT,
  rule_id           TEXT,
  operator_id       TEXT NOT NULL DEFAULT 'system',
  user_id           TEXT NOT NULL DEFAULT 'local',
  prev_hash         TEXT NOT NULL,
  signature         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);

CREATE TABLE IF NOT EXISTS rules (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  type       TEXT NOT NULL,
  pattern    TEXT NOT NULL,
  weight     REAL NOT NULL,
  confidence REAL NOT NULL,
  enabled    INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);
"""


def utcnow_iso() -> str:
    """统一的时间口径：UTC、秒级 ISO8601（与 Go 侧 storage 的 ts 语义一致）。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Database:
    """轻量 SQLite 封装：每次操作一个连接，写操作串行化。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._write_lock = threading.Lock()

    def init(self) -> None:
        """建库建表；幂等，服务每次启动都调用。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """写事务：进程内用锁串行，避免 SQLite busy 抖动。"""
        with self._write_lock, self.connect() as conn:
            yield conn
            conn.commit()


_database: Database | None = None


def get_database() -> Database:
    global _database
    if _database is None:
        _database = Database(get_settings().db_path)
    return _database


def reset_database_for_tests() -> None:
    """测试辅助：让下一次 get_database() 重新读取配置。"""
    global _database
    _database = None
