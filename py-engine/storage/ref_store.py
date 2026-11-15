"""参考代码范例库存储（T16.1 B1，表 ref_scripts 由 biz Flyway V21 建表，契约=ADR-015）。

一行 = 一个范例的一个内容版本（UNIQUE(ref_id, version)）；内容实体在 MinIO 桶 ref-scripts，
此处只存登记元数据。状态机 pending|active|disabled：检索只读 active；同一 ref_id 至多一个
active（activate_row 单活互斥，应用侧约束，对齐 meta_prompt_templates 先例）；不提供删除。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app_config import DATABASE_URL

logger = logging.getLogger("sonarcad.refstore")

STATUSES = ("pending", "active", "disabled")
SOURCE_TYPES = ("catalog_import", "manual_upload")  # B3 沉淀预留 deposit（ADR-015 §5）
REF_ID_RE = r"^[a-z0-9][a-z0-9_-]{0,126}$"

# PATCH 可写字段白名单（id/版本/哈希/对象引用/来源不可改——内容变更走新版本）
META_FIELDS = {"title", "description", "domain", "domain_rules", "sonar_types", "keywords"}


def _connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL.replace("+psycopg", ""), row_factory=dict_row)


def _json_list(value: Any) -> Any:
    """jsonb 列入参：list/dict 序列化为 JSON 文本（registry_store 同款），标量拒绝。"""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _normalize(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    row = dict(row)
    row["id"] = str(row["id"])  # uuid 出口序列化（T6.7 教训）
    row["updated_at"] = row["updated_at"].isoformat() if row.get("updated_at") else None
    row["created_at"] = row["created_at"].isoformat() if row.get("created_at") else None
    return row


def list_rows(ref_id: str | None = None) -> list[dict[str, Any]]:
    """全版本登记列表（含 pending/disabled——管理视图要看全量），ref_id 过滤可选。"""
    sql = "SELECT * FROM ref_scripts"
    args: tuple = ()
    if ref_id:
        sql += " WHERE ref_id = %s"
        args = (ref_id,)
    sql += " ORDER BY ref_id, version DESC"
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return [_normalize(r) for r in cur.fetchall()]


def get_row(row_id: str) -> dict[str, Any] | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM ref_scripts WHERE id = %s", (row_id,))
        return _normalize(cur.fetchone())


def get_active_rows() -> list[dict[str, Any]]:
    """检索源装载入口（reference.py db 模式）：只读 active 行，按 ref_id 稳定排序。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM ref_scripts WHERE status = 'active' ORDER BY ref_id")
        return [_normalize(r) for r in cur.fetchall()]


def next_version(ref_id: str) -> int:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(version), 0) + 1 AS v FROM ref_scripts WHERE ref_id = %s", (ref_id,))
        return int(cur.fetchone()["v"])


def find_by_content(ref_id: str, content_sha256: str) -> dict[str, Any] | None:
    """导入幂等判定：同 ref_id 下内容哈希已在库（任意版本/状态）→ 不重复登记。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM ref_scripts WHERE ref_id = %s AND content_sha256 = %s "
            "ORDER BY version DESC LIMIT 1",
            (ref_id, content_sha256),
        )
        return _normalize(cur.fetchone())


def insert_row(
    *,
    ref_id: str,
    version: int,
    file_name: str,
    title: str | None,
    description: str | None,
    domain: str | None,
    domain_rules: str | None,
    sonar_types: list,
    keywords: list,
    bucket: str,
    object_key: str,
    content_sha256: str,
    status: str = "pending",
    source_type: str,
    source_note: str | None,
    created_by: str | None,
) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"非法状态: {status}（只允许 {'|'.join(STATUSES)}）")
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"非法来源类型: {source_type}（只允许 {'|'.join(SOURCE_TYPES)}）")
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ref_scripts (ref_id, version, file_name, title, description, domain, "
            "domain_rules, sonar_types, keywords, bucket, object_key, content_sha256, status, "
            "source_type, source_note, created_by) "
            "VALUES (%(ref_id)s, %(version)s, %(file_name)s, %(title)s, %(description)s, %(domain)s, "
            "%(domain_rules)s, %(sonar_types)s, %(keywords)s, %(bucket)s, %(object_key)s, "
            "%(content_sha256)s, %(status)s, %(source_type)s, %(source_note)s, %(created_by)s) "
            "RETURNING *",
            {
                "ref_id": ref_id, "version": version, "file_name": file_name, "title": title,
                "description": description, "domain": domain, "domain_rules": domain_rules,
                "sonar_types": _json_list(sonar_types), "keywords": _json_list(keywords),
                "bucket": bucket, "object_key": object_key, "content_sha256": content_sha256,
                "status": status, "source_type": source_type, "source_note": source_note,
                "created_by": created_by,
            },
        )
        return _normalize(cur.fetchone())


def update_meta(row_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    """元数据编辑（白名单字段）+ 启停。

    status 语义：pending→active 禁止（激活走 confirm_row）；active→disabled 停用；
    disabled→active 重新启用（单活互斥：同 ref_id 其余 active 先停用）。
    """
    patch = {k: v for k, v in fields.items() if k in META_FIELDS}
    status = fields.get("status")
    if status is not None:
        if status not in ("active", "disabled"):
            raise ValueError("status 只允许 active|disabled（pending 激活走确认端点）")
        patch["status"] = status
    if not patch:
        raise ValueError("无可写字段")
    if "sonar_types" in patch:
        patch["sonar_types"] = _json_list(patch["sonar_types"])
    if "keywords" in patch:
        patch["keywords"] = _json_list(patch["keywords"])
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM ref_scripts WHERE id = %s FOR UPDATE", (row_id,))
        current = cur.fetchone()
        if current is None:
            raise KeyError(f"范例登记不存在: {row_id}")
        if patch.get("status") == "active" and current["status"] == "pending":
            raise ValueError("pending 版本须经确认端点激活（confirm_row）")
        if patch.get("status") == "active":
            cur.execute(
                "UPDATE ref_scripts SET status = 'disabled', updated_at = now() "
                "WHERE ref_id = %s AND status = 'active' AND id <> %s",
                (current["ref_id"], row_id),
            )
        cols = ", ".join(f"{k} = %({k})s" for k in patch)
        cur.execute(f"UPDATE ref_scripts SET {cols}, updated_at = now() WHERE id = %(row_id)s RETURNING *",
                    {**patch, "row_id": row_id})
        return _normalize(cur.fetchone())


def _require_row(cur: psycopg.Cursor, row_id: str, expected_status: str | tuple, action: str) -> dict:
    cur.execute("SELECT * FROM ref_scripts WHERE id = %s FOR UPDATE", (row_id,))
    row = cur.fetchone()
    if row is None:
        raise KeyError(f"范例登记不存在: {row_id}")
    allowed = expected_status if isinstance(expected_status, tuple) else (expected_status,)
    if row["status"] not in allowed:
        raise ValueError(f"仅 {('|'.join(allowed))} 状态可{action}（当前 {row['status']}）")
    return row


def activate_row(row_id: str) -> dict[str, Any]:
    """激活（确认 pending / 重新启用 disabled）：同 ref_id 其余 active 行先转 disabled（单活互斥）。"""
    with _connect() as conn, conn.cursor() as cur:
        row = _require_row(cur, row_id, ("pending", "disabled"), "激活")
        cur.execute(
            "UPDATE ref_scripts SET status = 'disabled', updated_at = now() "
            "WHERE ref_id = %s AND status = 'active' AND id <> %s",
            (row["ref_id"], row_id),
        )
        cur.execute("UPDATE ref_scripts SET status = 'active', updated_at = now() WHERE id = %s RETURNING *",
                    (row_id,))
        return _normalize(cur.fetchone())


def confirm_row(row_id: str) -> dict[str, Any]:
    """人工确认入库（A5「只产 pending、绝不直写」同构语义）：pending → active + 单活互斥。"""
    with _connect() as conn, conn.cursor() as cur:
        _require_row(cur, row_id, "pending", "确认")
        cur.execute(
            "UPDATE ref_scripts SET status = 'disabled', updated_at = now() "
            "WHERE ref_id = (SELECT ref_id FROM ref_scripts WHERE id = %s) "
            "AND status = 'active' AND id <> %s",
            (row_id, row_id),
        )
        cur.execute("UPDATE ref_scripts SET status = 'active', updated_at = now() WHERE id = %s RETURNING *",
                    (row_id,))
        return _normalize(cur.fetchone())


def snapshot_fingerprint() -> list[tuple]:
    """DB 快照指纹（reference.py db 模式热更新比对，对齐 A0-1 目录指纹语义）。

    含全状态行——启停/确认/新版本任一变化都会触发整体重载。异常由调用方捕获降级。
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT ref_id, version, status, updated_at FROM ref_scripts ORDER BY ref_id, version")
        return [(r["ref_id"], r["version"], r["status"],
                 r["updated_at"].isoformat() if r["updated_at"] else "") for r in cur.fetchall()]
