# -*- coding: utf-8 -*-
"""T3.2 知识管线单测：分块/抽取清洗/向量库/三层构建/检索扩展级。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kb.chunker import chunk_units
from kb.extractor import ExtractionUnavailable, extract_from_chunks
from kb.parsers import ParsedUnit


# ----------------------------------------------------------------------
# 分块（§6 ③：章节优先、重叠可调、chunk 携带 doc_id+location）
# ----------------------------------------------------------------------

def test_chunk_keeps_units_independent():
    units = [ParsedUnit("第1页", "甲" * 100), ParsedUnit("第2页", "乙" * 100)]
    chunks = chunk_units(units, size=200, overlap=50)
    assert [c["location"] for c in chunks] == ["第1页", "第2页"]  # 章节优先不跨界


def test_chunk_splits_long_unit_with_overlap():
    text = "。".join(f"句子{i}内容继续" for i in range(60))  # 远超 size
    chunks = chunk_units([ParsedUnit("第1页", text)], size=300, overlap=60)
    assert len(chunks) > 1
    assert all(c["location"].startswith("第1页#块") for c in chunks)
    assert all(len(c["text"]) <= 300 for c in chunks)
    # 重叠：后一块开头应出现在前一块的内容中
    assert chunks[1]["text"][:20] in chunks[0]["text"] + chunks[1]["text"]


def test_chunk_overlap_must_be_smaller_than_size():
    with pytest.raises(ValueError):
        chunk_units([ParsedUnit("x", "内容")], size=100, overlap=100)


# ----------------------------------------------------------------------
# 抽取（§6 ④：本体白名单清洗 + pending 人工审）
# ----------------------------------------------------------------------

GOOD = (
    '{"entities": [{"type": "DesignConstraint", "name": "外径限制", "description": "外径≤27mm"},'
    ' {"type": "KlingonTech", "name": "越界类型"},'
    ' {"type": "DesignParameter", "name": "", "description": "无名"}],'
    ' "relations": [{"source": "外径限制", "target": "越界类型", "relation": "LIMITS"},'
    ' {"source": "外径限制", "target": "不存在", "relation": "HANGS"}]}'
)


class FakeLlm:
    def __init__(self, raw=None, fail=False):
        self.raw, self.fail = raw, fail

    def chat(self, messages, **kwargs):
        if self.fail:
            raise RuntimeError("LLM 宕机")
        return self.raw


def test_extractor_cleans_to_whitelist():
    items = extract_from_chunks([{"chunk_index": 1, "text": "片段"}], FakeLlm(GOOD))
    # 越界类型/无名实体/悬挂关系全部清洗
    assert [i["kind"] for i in items] == ["entity"]
    assert items[0]["name"] == "外径限制" and items[0]["etype"] == "DesignConstraint"
    assert items[0]["chunk_index"] == 1


def test_extractor_requires_llm():
    with pytest.raises(ExtractionUnavailable):
        extract_from_chunks([{"chunk_index": 1, "text": "x"}], None)


def test_extractor_unparseable_chunk_skipped():
    items = extract_from_chunks(
        [{"chunk_index": 1, "text": "坏"}, {"chunk_index": 2, "text": "好"}],
        FakeLlm(None),
    )
    assert items == []  # 解析失败 chunk 跳过但不抛


# ----------------------------------------------------------------------
# 向量库（fake client 行为 + 真实容器探活）
# ----------------------------------------------------------------------
class FakeMilvus:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.has = True

    def has_collection(self, name):
        return self.has

    def create_collection(self, *a, **k):
        self.has = True

    def delete(self, name, filter):
        doc = filter.split('"')[1]
        self.rows = {k: v for k, v in self.rows.items() if not k.startswith(doc + ":")}

    def insert(self, name, rows):
        for r in rows:
            self.rows[r["chunk_key"]] = r

    def query(self, name, filter, output_fields):
        doc = filter.split('"')[1]
        return [k for k in self.rows if k.startswith(doc + ":")]

    def search(self, name, data, limit, output_fields):
        return [[{"entity": {**v}, "distance": 0.9} for k, v in list(self.rows.items())[:limit]]]

    def list_collections(self):
        return ["kb_chunks"]

    def prepare_index_params(self):
        return type("IP", (), {"add_index": lambda self, **k: None})()


def test_vector_store_upsert_delete_search_roundtrip():
    from kb.vector_store import ChunkVectorStore

    fake = FakeMilvus()
    vs = ChunkVectorStore(client=fake)
    assert vs.available()
    n = vs.upsert_chunks("doc1",
                         [{"chunk_index": 1, "location": "第1页", "text": "甲"},
                          {"chunk_index": 2, "location": "第1页#块2", "text": "乙"}],
                         [[0.1] * 4, [0.2] * 4])
    assert n == 2
    assert set(fake.rows) == {"doc1:1", "doc1:2"}
    # 增量重写：同 doc 先删后插
    vs.upsert_chunks("doc1", [{"chunk_index": 1, "location": "第1页", "text": "甲2"}], [[0.3] * 4])
    assert set(fake.rows) == {"doc1:1"}
    assert vs.delete_doc("doc1") == 1
    assert fake.rows == {}
    hits = vs.search([0.1] * 4, top_k=3)
    assert hits == []


def test_vector_store_real_container_probe():
    """实连本机 Milvus（compose 就绪时探活；不可达只记 skip 不断言）。"""
    from kb.vector_store import ChunkVectorStore

    vs = ChunkVectorStore()
    assert isinstance(vs.available(), bool)


# ----------------------------------------------------------------------
# 三层构建（fakes：向量/图谱/语料逐层如实降级）
# ----------------------------------------------------------------------
@pytest.fixture()
def kb_doc_env(monkeypatch):
    """构造一个 chunked 文档 + 抽取行的假存储环境。"""
    docs = {"d1": {"doc_id": "d1", "status": "chunked"}}
    chunks = {"d1": [{"chunk_index": 1, "location": "第1页", "text": "外径≤27mm", "embedded": False}]}
    extractions = {"d1": [
        {"id": 1, "doc_id": "d1", "kind": "entity", "etype": "DesignConstraint",
         "name": "外径限制", "payload": {"description": "x"}, "status": "confirmed", "graph_key": None},
    ]}
    updates: list[tuple] = []
    import storage.kb_store as ks

    monkeypatch.setattr(ks, "get_document", lambda doc_id: docs.get(doc_id))
    monkeypatch.setattr(ks, "get_chunks", lambda doc_id: chunks.get(doc_id, []))
    monkeypatch.setattr(ks, "get_confirmed_extractions", lambda doc_id=None: extractions.get(doc_id, []))
    monkeypatch.setattr(ks, "set_extraction_graph_key", lambda i, k: extractions["d1"][0].update(graph_key=k))
    monkeypatch.setattr(ks, "mark_embedded", lambda doc_id, e: chunks[doc_id][0].update(embedded=e))
    monkeypatch.setattr(ks, "update_document",
                        lambda doc_id, **f: (updates.append((doc_id, f)), docs[doc_id].update(f)))
    monkeypatch.setattr(ks, "all_indexed_chunks", lambda limit=5000: [])
    return {"docs": docs, "chunks": chunks, "extractions": extractions, "updates": updates}


def test_build_one_all_layers_ok(kb_doc_env, monkeypatch):
    import kb.index_build as ib

    monkeypatch.setattr("kb.embeddings.embed_texts", lambda texts: [[0.1] * 1024] * len(texts))
    monkeypatch.setattr("kb.vector_store.ChunkVectorStore.available", lambda self: True)
    written = {}
    monkeypatch.setattr("kb.vector_store.ChunkVectorStore.upsert_chunks",
                        lambda self, doc_id, chunks, vectors: written.update({doc_id: len(chunks)}) or len(chunks))

    class FakeGraph:
        def available(self):
            return True

        def _get_driver(self):
            return None

    import storage.neo4j_store as ns

    monkeypatch.setattr(ns, "Neo4jStore", lambda: FakeGraph())
    monkeypatch.setattr(ib, "write_extractions_to_graph",
                        lambda store, extractions, doc_id: {"entities": 1, "relations": 0})
    monkeypatch.setattr(ib, "_graph_key_of", lambda row: "abc")
    monkeypatch.setattr(ib, "_refresh_text_corpus", lambda: 1)

    result = ib.build_one("d1")
    assert result["status"] == "indexed"
    assert result["detail"]["vector"] == {"written": 1}
    assert result["detail"]["graph"] == {"entities": 1, "relations": 0}
    assert written["d1"] == 1
    assert kb_doc_env["extractions"]["d1"][0]["graph_key"] == "abc"


def test_build_one_degrades_layer_by_layer(kb_doc_env, monkeypatch):
    """Milvus/embedding/Neo4j 逐层不可用：如实 skipped，语料层兜底 → 仍 indexed（A-16）。"""
    import kb.index_build as ib

    monkeypatch.setattr("kb.vector_store.ChunkVectorStore.available", lambda self: False)
    monkeypatch.setattr(ib, "_refresh_text_corpus", lambda: 1)

    result = ib.build_one("d1")
    assert result["status"] == "indexed"
    assert "skipped: Milvus 不可用" == result["detail"]["vector"]
    assert "skipped: 无 confirmed 抽取" in str(result["detail"]["graph"]) or "skipped" in str(result["detail"]["graph"])


def test_build_one_all_layers_fail_honest(kb_doc_env, monkeypatch):
    import kb.index_build as ib

    def boom(*a, **k):
        raise RuntimeError("全挂")

    monkeypatch.setattr("kb.vector_store.ChunkVectorStore.available", lambda self: False)
    monkeypatch.setattr(ib, "_refresh_text_corpus", boom)
    monkeypatch.setattr(kb_doc_env and ib, "get_confirmed_extractions",
                        lambda doc_id=None: (_ for _ in ()).throw(RuntimeError("neo4j 挂")),
                        raising=False)
    # 图谱层：confirmed 存在但 store 不可用
    result = ib.build_one("d1")
    assert result["status"] == "chunked"  # 全部层失败 → 状态如实回退，不伪造 indexed


def test_incremental_default_only_processes_unindexed(kb_doc_env, monkeypatch):
    """增量语义（§6 ⑥）：缺省只构建未 indexed 文档。"""
    import kb.index_build as ib

    kb_doc_env["docs"]["d2"] = {"doc_id": "d2", "status": "indexed"}
    calls: list[str] = []
    monkeypatch.setattr(ib, "build_one", lambda doc_id: calls.append(doc_id) or {"status": "indexed"})
    monkeypatch.setattr("storage.kb_store.list_documents",
                        lambda limit=500: [{"doc_id": "d1", "status": "chunked"},
                                           {"doc_id": "d2", "status": "indexed"}])
    result = ib.build_documents()
    assert calls == ["d1"]
    assert result["total"] == 1


# ----------------------------------------------------------------------
# 检索扩展级（graph/vector 逐级降级 + 动态语料）
# ----------------------------------------------------------------------
def test_dynamic_corpus_fused_into_text_level():
    from knowledge.service import GraphRAGService

    svc = GraphRAGService(kb_dir="nonexistent-dir", enable_kb_levels=False)
    svc.refresh_dynamic_corpus([("docX", "第1页", "拖曳阵外径不超过 27mm 的硬约束")])
    result = svc.query("外径 27mm")
    assert result.level == "text"
    assert "27mm" in result.content
    assert result.refs[0]["doc_id"] == "docX"
    assert result.refs[0]["location"] == "第1页"  # 动态源带真实页码/章节（§6 ② 升级）


def test_kb_levels_disabled_by_default_in_tests():
    from knowledge.service import GraphRAGService

    svc = GraphRAGService(kb_dir="nonexistent-dir")
    assert svc.enable_kb_levels is False
    result = svc.query("任何查询")
    assert result.level in ("text", "unavailable")  # 不触发 neo4j/milvus 探针


def test_graph_level_hit_with_fake_neo4j(monkeypatch):
    from knowledge.service import GraphRAGService

    svc = GraphRAGService(kb_dir="nonexistent-dir", enable_kb_levels=True)

    class FakeStore:
        def available(self):
            return True

        def find_entities_matching(self, query, limit=5):
            return [{"label": "DesignConstraint", "name": "外径限制",
                     "description": "外径≤27mm", "source_doc": "docX"}]

    import storage.neo4j_store as ns

    monkeypatch.setattr(ns, "Neo4jStore", lambda: FakeStore())
    hit = svc._try_neo4j("外径 限制")
    assert hit is not None
    content, refs = hit
    assert "外径限制" in content
    assert refs[0] == {"doc_id": "docX", "location": "DesignConstraint:外径限制"}


def test_match_terms_extracts_chinese_bigrams():
    from storage.neo4j_store import _match_terms

    terms = _match_terms("拖曳线列阵 外径")
    assert "外径" in terms
    assert any("拖曳" in t for t in terms)
    assert len(terms) <= 12
