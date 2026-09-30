"""知识资产库存储（T3.3，表 kb_formulas / kb_constraints / kb_standards，V7 建表）
+ LLM 供应商 CRUD（5.16，llm_providers 表 V3 建表）。

通用 CRUD 白名单字段校验；列表查询带 TTL 缓存（A4 校验热路径用，库变更后至多 30s 生效）。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app_config import DATABASE_URL

logger = logging.getLogger("sonarcad.registry")

TABLES = {
    "formulas": "kb_formulas",
    "constraints": "kb_constraints",
    "standards": "kb_standards",
}

# 各库可写字段白名单（id/时间戳不可写）
WRITABLE: dict[str, set[str]] = {
    "formulas": {"key", "name", "expr", "symbols", "unit", "source_ref", "enabled", "confidence"},
    "constraints": {"name", "expression", "applies_to", "severity_hint", "source_ref", "enabled", "confidence"},
    "standards": {"code", "name", "applies_to", "requirement", "expr", "enabled", "confidence"},
}


def _connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL.replace("+psycopg", ""), row_factory=dict_row)


def _validate_confidence(value: Any) -> str:
    if value not in ("high", "medium", "low"):
        raise ValueError(f"confidence 只允许 high|medium|low: {value}")
    return value


def create_row(kind: str, fields: dict[str, Any]) -> dict[str, Any]:
    table, allowed = TABLES[kind], WRITABLE[kind]
    patch = {k: v for k, v in fields.items() if k in allowed}
    if "confidence" in patch:
        patch["confidence"] = _validate_confidence(patch["confidence"])
    if not patch:
        raise ValueError("无可写字段")
    if "symbols" in patch and isinstance(patch["symbols"], (list, dict)):
        patch["symbols"] = json.dumps(patch["symbols"], ensure_ascii=False)
    cols = ", ".join(patch)
    marks = ", ".join(f"%({k})s" for k in patch)
    conflict_key = "key" if kind == "formulas" else "name" if kind == "constraints" else "code"
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({marks}) "
            f"ON CONFLICT ({conflict_key}) DO UPDATE SET "
            + ", ".join(f"{k} = EXCLUDED.{k}" for k in patch if k != conflict_key)
            + ", updated_at = now() RETURNING *",
            patch,
        )
        return cur.fetchone()


def update_row(kind: str, row_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
    table, allowed = TABLES[kind], WRITABLE[kind]
    patch = {k: v for k, v in fields.items() if k in allowed}
    if "confidence" in patch:
        patch["confidence"] = _validate_confidence(patch["confidence"])
    if not patch:
        raise ValueError("无可写字段")
    if "symbols" in patch and isinstance(patch["symbols"], (list, dict)):
        patch["symbols"] = json.dumps(patch["symbols"], ensure_ascii=False)
    sets = ", ".join(f"{k} = %s" for k in patch)
    values = list(patch.values()) + [row_id]
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {table} SET {sets}, updated_at = now() WHERE id = %s RETURNING *", values)
        return cur.fetchone()


def delete_row(kind: str, row_id: int) -> bool:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {TABLES[kind]} WHERE id = %s RETURNING id", (row_id,))
        return cur.fetchone() is not None


def list_rows(kind: str, enabled_only: bool = False) -> list[dict[str, Any]]:
    sql = f"SELECT * FROM {TABLES[kind]}"
    if enabled_only:
        sql += " WHERE enabled"
    sql += " ORDER BY id"
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    for r in rows:
        if isinstance(r.get("symbols"), str):
            r["symbols"] = json.loads(r["symbols"])
    return rows


_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CACHE_TTL_S = 30


def list_rows_cached(kind: str) -> list[dict[str, Any]]:
    """带 TTL 缓存的 enabled 列表（A4 热路径）。库不可达返回 []（如实降级）。"""
    cached = _cache.get(kind)
    now = time.time()
    if cached and now - cached[0] < _CACHE_TTL_S:
        return cached[1]
    try:
        rows = list_rows(kind, enabled_only=True)
    except Exception as exc:
        logger.warning("资产库 %s 读取失败（如实降级为空）: %s", kind, exc)
        rows = []
    _cache[kind] = (now, rows)
    return rows


# ----------------------------------------------------------------------
# LLM 供应商管理（5.16；api_key 只写不读——序列化一律脱敏）
# ----------------------------------------------------------------------
def list_providers() -> list[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT name, base_url, model, enabled, priority, timeout_s, created_at "
            "FROM llm_providers ORDER BY priority DESC, name"
        )
        return cur.fetchall()


def create_provider(name: str, base_url: str, api_key: str, model: str,
                    priority: int = 0, timeout_s: int = 300, enabled: bool = True) -> dict[str, Any]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO llm_providers (name, base_url, api_key, model, enabled, priority, timeout_s)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (name) DO UPDATE SET base_url = EXCLUDED.base_url,
                 api_key = EXCLUDED.api_key, model = EXCLUDED.model,
                 enabled = EXCLUDED.enabled, priority = EXCLUDED.priority,
                 timeout_s = EXCLUDED.timeout_s
               RETURNING name, base_url, model, enabled, priority, timeout_s""",
            (name, base_url, api_key, model, enabled, priority, timeout_s),
        )
        return cur.fetchone()


def update_provider(name: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {"base_url", "api_key", "model", "enabled", "priority", "timeout_s"}
    patch = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not patch:
        raise ValueError("无可写字段")
    sets = ", ".join(f"{k} = %s" for k in patch)
    values = list(patch.values()) + [name]
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE llm_providers SET {sets} WHERE name = %s "
            "RETURNING name, base_url, model, enabled, priority, timeout_s",
            values,
        )
        return cur.fetchone()


def delete_provider(name: str) -> bool:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM llm_providers WHERE name = %s RETURNING name", (name,))
        return cur.fetchone() is not None
