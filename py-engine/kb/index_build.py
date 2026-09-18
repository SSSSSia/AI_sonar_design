"""KB 索引构建编排（T3.2，§6 ⑤⑥，celery 任务 sonarcad.kb_build 的主体）。

三层构建逐层独立、逐层如实记录（A-16，不伪造成功）：
  ① 向量层：chunks → embedding(bge-m3) → Milvus upsert（按 doc_id 先删后插，增量）
  ② 图谱层：confirmed 抽取 → Neo4j MERGE（节点带 source_doc，删除文档时标 deprecated）
  ③ 语料层：indexed 文档 chunks → 检索文本级动态语料（GraphRAGService.refresh_dynamic_corpus）
某层不可用 → 该层跳过/失败如实写入 build_detail，其余层继续；全部层失败则 doc 状态如实回退。
"""

from __future__ import annotations

import logging
from typing import Any

from storage import kb_store

logger = logging.getLogger("sonarcad.kb.build")


# ----------------------------------------------------------------------
# 图谱层：confirmed 抽取 → Neo4j
# ----------------------------------------------------------------------
def write_extractions_to_graph(store: Any, extractions: list[dict[str, Any]], doc_id: str) -> dict[str, int]:
    """把 confirmed 抽取写入 Neo4j。返回 {"entities": n, "relations": m}。

    实体节点：label=本体类型（白名单外兜底 Entity），key=sha1(归一化名)，
    属性 name/source_doc/description；关系：两端实体 + 关系类型（净化）。
    """
    from storage.neo4j_store import _content_key, _normalize, _safe_label, _safe_rel_type

    entities = [e for e in extractions if e["kind"] == "entity"]
    relations = [e for e in extractions if e["kind"] == "relation"]
    name_to_key: dict[str, str] = {}
    with store._get_driver().session() as session:
        for e in entities:
            key = _content_key(e["name"])
            name_to_key[e["name"]] = key
            label = _safe_label(e["etype"])
            session.run(
                f"MERGE (n:`{label}` {{key: $key}}) "
                "ON CREATE SET n.name = $name, n.source_doc = $doc, "
                "n.description = $desc, n.created_at = datetime(), n.deprecated = false "
                "ON MATCH SET n.source_doc = coalesce(n.source_doc, $doc)",
                key=key, name=e["name"], doc=doc_id,
                desc=str((e.get("payload") or {}).get("description") or ""),
            )
        for r in relations:
            payload = r.get("payload") or {}
            src_key = name_to_key.get(str(payload.get("source") or ""))
            dst_key = name_to_key.get(str(payload.get("target") or ""))
            if not src_key or not dst_key:
                continue  # 两端实体不在本批 confirmed 集合内——悬挂关系不入图
            rel = _safe_rel_type(r["etype"])
            session.run(
                "MATCH (a {key: $src}), (b {key: $dst}) "
                f"MERGE (a)-[rel:`{rel}`]->(b) "
                "ON CREATE SET rel.source_doc = $doc, rel.description = $desc",
                src=src_key, dst=dst_key, doc=doc_id,
                desc=str(payload.get("description") or ""),
            )
    return {"entities": len(entities), "relations": len(relations)}


def _refresh_text_corpus() -> int:
    """语料层：indexed 文档 chunks 注册进检索文本级动态语料。"""
    from knowledge.service import get_knowledge_service

    chunks = kb_store.all_indexed_chunks()
    svc = get_knowledge_service()
    svc.refresh_dynamic_corpus(
        [(c["doc_id"], c["location"], c["text"]) for c in chunks]
    )
    return len(chunks)


# ----------------------------------------------------------------------
# 单文档构建
# ----------------------------------------------------------------------
def build_one(doc_id: str) -> dict[str, Any]:
    doc = kb_store.get_document(doc_id)
    if doc is None:
        return {"doc_id": doc_id, "error": "文档不存在"}
    detail: dict[str, Any] = {}
    ok_layers = 0

    chunks = kb_store.get_chunks(doc_id)
    if not chunks:
        return {"doc_id": doc_id, "error": "无 chunks（先调用分块接口）"}

    # ① 向量层
    try:
        from kb.embeddings import embed_texts
        from kb.vector_store import ChunkVectorStore

        vs = ChunkVectorStore()
        if not vs.available():
            detail["vector"] = "skipped: Milvus 不可用"
        else:
            vectors = embed_texts([c["text"] for c in chunks])
            if vectors is None:
                detail["vector"] = "skipped: embedding 不可用"
            else:
                written = vs.upsert_chunks(doc_id, chunks, vectors)
                kb_store.mark_embedded(doc_id, True)
                detail["vector"] = {"written": written}
                ok_layers += 1
    except Exception as exc:
        logger.exception("向量层构建失败 doc_id=%s", doc_id)
        detail["vector"] = f"failed: {exc}"

    # ② 图谱层（confirmed 抽取；无 confirmed 记 skipped）
    try:
        confirmed = kb_store.get_confirmed_extractions(doc_id)
        if not confirmed:
            detail["graph"] = "skipped: 无 confirmed 抽取"
        else:
            from storage.neo4j_store import Neo4jStore

            store = Neo4jStore()
            if not store.available():
                detail["graph"] = "skipped: Neo4j 不可用"
            else:
                counts = write_extractions_to_graph(store, confirmed, doc_id)
                for row in confirmed:
                    key = _graph_key_of(row)
                    if key:
                        kb_store.set_extraction_graph_key(row["id"], key)
                detail["graph"] = counts
                ok_layers += 1
    except Exception as exc:
        logger.exception("图谱层构建失败 doc_id=%s", doc_id)
        detail["graph"] = f"failed: {exc}"

    # ③ 语料层：先落 indexed 状态再刷新（all_indexed_chunks 只取 indexed 文档）
    if ok_layers > 0:
        kb_store.update_document(doc_id, status="indexed")
    try:
        detail["corpus"] = {"chunks": _refresh_text_corpus()}
        ok_layers += 1
    except Exception as exc:
        logger.exception("语料层刷新失败 doc_id=%s", doc_id)
        detail["corpus"] = f"failed: {exc}"

    if ok_layers > 0:
        kb_store.update_document(doc_id, status="indexed", build_detail=detail, error=None)
        return {"doc_id": doc_id, "status": "indexed", "detail": detail}
    kb_store.update_document(doc_id, status="chunked",
                             build_detail=detail, error="三层构建全部失败（详见 build_detail）")
    return {"doc_id": doc_id, "status": "chunked", "detail": detail}


def _graph_key_of(row: dict[str, Any]) -> str | None:
    """抽取行对应的图节点 key（实体=自身；关系=source->target 组合键）。"""
    from storage.neo4j_store import _content_key

    if row["kind"] == "entity":
        return _content_key(row["name"])
    payload = row.get("payload") or {}
    if payload.get("source") and payload.get("target"):
        return f"{_content_key(str(payload['source']))}->{_content_key(str(payload['target']))}"
    return None


def build_documents(doc_ids: list[str] | None = None) -> dict[str, Any]:
    """批量构建（缺省=所有未 indexed 文档——增量语义，§6 ⑥）。"""
    if not doc_ids:
        doc_ids = [
            d["doc_id"] for d in kb_store.list_documents(500) if d["status"] != "indexed"
        ]
    results = [build_one(doc_id) for doc_id in doc_ids]
    indexed = sum(1 for r in results if r.get("status") == "indexed")
    return {"total": len(results), "indexed": indexed, "documents": results}


def delete_doc_vectors(doc_id: str) -> int | str:
    """删除文档的向量挂载（§6 ⑦ 清理）。Milvus 不可用返回 'skipped'。"""
    from kb.vector_store import ChunkVectorStore

    vs = ChunkVectorStore()
    if not vs.available():
        return "skipped: Milvus 不可用"
    return vs.delete_doc(doc_id)
