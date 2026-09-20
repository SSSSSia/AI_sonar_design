"""知识库范围解析（PHASE-006 D6-2）：kb_ids → 允许检索的 doc_id 集合。

归属唯一事实源 = kb_base_documents（PG）；本模块是检索层唯一过滤入口：
- kb_ids 非空：并集这些库的归属文档；
- kb_ids 空 / None："全部库" = 有任一归属的文档（未归库排除）；
- 30s TTL 缓存（同动态语料模式）+ invalidate()（归属写操作后调用，本进程即时生效）。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from storage import kb_store

logger = logging.getLogger("sonarcad.kb.scope")

_TTL_SECONDS = 30.0
_scope_cache: dict[str, Any] = {"key": None, "doc_ids": None, "loaded_at": 0.0}


def resolve_doc_ids(kb_ids: Optional[list[str]] = None) -> list[str]:
    """解析允许检索的 doc_id 集合。排序稳定（缓存键 + Milvus/Neo4j 过滤表达式稳定）。"""
    key = ",".join(sorted(kb_ids)) if kb_ids else ""
    now = time.time()
    cached = _scope_cache.get("key") == key and now - _scope_cache["loaded_at"] < _TTL_SECONDS
    if cached:
        return list(_scope_cache["doc_ids"])
    try:
        if kb_ids:
            doc_ids: list[str] = []
            for base_id in kb_ids:
                doc_ids.extend(d["doc_id"] for d in kb_store.list_documents(limit=2000, base_id=base_id))
            doc_ids = sorted(set(doc_ids))
        else:
            # "全部库"：有任一归属的文档（未归库排除，D6-9）
            doc_ids = _all_assigned_doc_ids()
    except Exception as exc:
        logger.warning("kb_scope 解析失败（按空集合处理，检索将零命中如实）: %s", exc)
        return []
    _scope_cache.update(key=key, doc_ids=doc_ids, loaded_at=now)
    return list(doc_ids)


def _all_assigned_doc_ids() -> list[str]:
    """有任一归属的文档 id（"全部库"语义）。"""
    with kb_store._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT doc_id FROM kb_base_documents")
        return sorted(r["doc_id"] for r in cur.fetchall())


def invalidate() -> None:
    """归属写操作后调用（add/remove/delete base），本进程下个查询即取新范围。"""
    _scope_cache.update(key=None, doc_ids=None, loaded_at=0.0)
