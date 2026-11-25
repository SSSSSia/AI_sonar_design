# -*- coding: utf-8 -*-
"""范例库向量检索薄层（T17.4，PHASE-017；契约=ADR-016 §4）。

复用 kb/vector_store.ChunkVectorStore（collection 构造参数化，零 schema 新代码）与
kb/embeddings.embed_texts（bge-m3 既有链）：
- 写侧：范例 confirm 时 embed（三来源 manual_upload/import-catalog/deposit 汇合于唯一
  confirm 端点）——best-effort 失败 log+False 不阻塞入库（ADR 明文「失败如实降级为纯词法」）；
- 读侧：select_reference 词法候选之外补向量名次，RRF 合成排序（type_matched 权威优先不变，
  向量只扩召回不改判分；weak 语义/无匹配不注入锚全部不动）。

全局开关 app_config.REF_VECTOR_ENABLED（默认关，生产启用留 T16.2 B2 评审）——关闭=两路
零执行，检索行为与纯词法逐字节一致（回滚保证）。active-only 双保险：写侧只嵌 confirmed
行；读侧返回后由调用方对当前 entries 的 ref_id 集合二次过滤（向量残留不越 active 线）。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("sonarcad.refcode.vector")


def _store() -> Any:
    from kb.vector_store import ChunkVectorStore
    import app_config

    return ChunkVectorStore(collection=app_config.KB_REF_VECTOR_COLLECTION)


def _embedding_material(row: dict[str, Any], content: str) -> str:
    """embed 材料：title + description + 脚本头（bge-m3 上下文窗口内）。"""
    return " ".join(
        str(x or "").strip()
        for x in (row.get("title"), row.get("description"), content[:8000])
        if str(x or "").strip()
    )


def sync_ref_vector(row: dict[str, Any], content: str) -> bool:
    """embed 并 upsert 范例向量（doc_id=ref_id 先删后插，版本替换天然幂等）。

    失败 log+False 不抛（不阻塞入库）。材料为空不嵌（A-16 不造空向量）。"""
    try:
        material = _embedding_material(row, content)
        if not material:
            logger.info("范例向量材料为空，跳过 embedding: %s", row.get("ref_id"))
            return False
        from kb.embeddings import embed_texts

        vectors = embed_texts([material])
        if not vectors:
            logger.warning("embedding 供应商不可用，范例向量未写入（检索纯词法降级）: %s", row.get("ref_id"))
            return False
        vs = _store()
        written = vs.upsert_chunks(
            str(row["ref_id"]),
            [{"chunk_index": 0, "location": f"v{row.get('version')}", "text": content[:1000]}],
            [vectors[0]],
        )
        logger.info("范例向量已写入: %s v%s (%d 行)", row.get("ref_id"), row.get("version"), written)
        return True
    except Exception as exc:  # noqa: BLE001 - 写侧失败绝不阻塞入库（ADR-016 §4）
        logger.warning("范例向量写入失败（不阻塞入库，回填脚本可补）: %s: %s", row.get("ref_id"), exc)
        return False


def search_ref_vectors(query_text: str, top_k: int = 5) -> dict[str, tuple[int, float]]:
    """查询向量名次：{ref_id: (rank 从 1, 相似度分)}。任何失败/零命中返回空 dict（纯词法降级）。

    供 select_reference 融合调用；调用方须再按当前 entries 的 ref_id 集合过滤
    （active-only 兜底，见模块 docstring）。"""
    if not query_text.strip():
        return {}
    try:
        from kb.embeddings import embed_texts

        vectors = embed_texts([query_text])
        if not vectors:
            return {}
        vs = _store()
        hits = vs.search(vectors[0], top_k=top_k)
        out: dict[str, tuple[int, float]] = {}
        for rank, h in enumerate(hits, start=1):
            ref_id = str(h.get("doc_id") or "")
            if ref_id and ref_id not in out:
                out[ref_id] = (rank, float(h.get("score") or 0.0))
        return out
    except Exception as exc:  # noqa: BLE001 - 读侧失败降级纯词法（A-14 如实日志）
        logger.warning("范例向量检索失败（纯词法降级）: %s", exc)
        return {}


def delete_ref_vector(ref_id: str) -> bool:
    """停用/撤回时删除该 ref_id 的向量（active-only：向量集合只反映 active 行）。

    best-effort 失败不抛（读侧还有 entries 集合二次过滤兜底）。"""
    try:
        deleted = _store().delete_doc(str(ref_id))
        logger.info("范例向量已删除: %s (%d 行)", ref_id, deleted)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("范例向量删除失败（读侧 active 过滤兜底）: %s: %s", ref_id, exc)
        return False
