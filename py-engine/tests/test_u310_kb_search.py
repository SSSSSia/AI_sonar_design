# -*- coding: utf-8 -*-
"""U3.10 知识检索端点单测：POST /api/v1/kb/search（包装三级降级检索 service）。

mock get_knowledge_service 与 kb_store.list_documents（不依赖真实 Milvus/Neo4j）；
覆盖：向量级逐块命中带分数与文档名解析、非向量级聚合单命中（不造分数）、
参数校验 422、RetrievalResult.hits 的 to_dict 往返（缓存语义）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app
from knowledge.service import RetrievalResult

client = TestClient(app)
HDRS = {"X-User-Id": "t-u310", "X-User-Roles": "designer"}


class _FakeSvc:
    def __init__(self, result: RetrievalResult):
        self._r = result

    def query(self, query: str, kb_ids=None) -> RetrievalResult:  # noqa: ANN001
        self._r.hits = self._r.hits or []
        return self._r


def _patch(monkeypatch, result, docs=None):
    import api.kb as kb_api
    import knowledge.service as svc_mod

    monkeypatch.setattr(svc_mod, "get_knowledge_service", lambda: _FakeSvc(result))
    monkeypatch.setattr(kb_api.kb_store, "list_documents", lambda limit=1000: docs or [])


def test_vector_hits_with_scores_and_doc_names(monkeypatch):
    r = RetrievalResult(
        "知识库向量检索结果（Milvus chunks）：…", "vector", ["local", "basic", "graph"],
        refs=[{"doc_id": "doc_a", "location": "段落3"}],
        hits=[
            {"doc_id": "doc_a", "location": "段落3", "text": "阵元间距…", "score": 0.51},
            {"doc_id": "doc_b", "location": "P34", "text": "半波长设计…", "score": 0.48},
        ],
    )
    _patch(monkeypatch, r, docs=[
        {"doc_id": "doc_a", "filename": "拖曳阵知识.txt"},
        {"doc_id": "doc_b", "filename": "声呐手册.pdf"},
    ])
    resp = client.post("/api/v1/kb/search", json={"query": "阵元间距"},
                       headers=HDRS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["level"] == "vector" and data["ok"] is True
    assert data["degraded_from"] == ["local", "basic", "graph"]
    assert data["hits"][0]["doc_name"] == "拖曳阵知识.txt"
    assert data["hits"][0]["score"] == 0.51
    assert data["hits"][1]["doc_name"] == "声呐手册.pdf"
    assert data["elapsed_ms"] >= 0


def test_non_vector_level_single_hit_no_score(monkeypatch):
    r = RetrievalResult("文本级聚合内容", "text", ["graphrag_sdk_unavailable"])
    _patch(monkeypatch, r)
    resp = client.post("/api/v1/kb/search", json={"query": "任意"}, headers=HDRS)
    data = resp.json()
    assert data["level"] == "text"
    assert len(data["hits"]) == 1
    assert data["hits"][0]["score"] is None  # 非向量级不造分数（A-16）
    assert data["hits"][0]["text"] == "文本级聚合内容"


def test_unavailable_ok_false(monkeypatch):
    r = RetrievalResult("选定知识库范围内无可检索知识", "unavailable", [])
    _patch(monkeypatch, r)
    resp = client.post("/api/v1/kb/search", json={"query": "x", "kb_ids": ["k1"]}, headers=HDRS)
    data = resp.json()
    assert data["ok"] is False and data["hits"] == []


def test_validation_422(monkeypatch):
    r = RetrievalResult("x", "text", [])
    _patch(monkeypatch, r)
    assert client.post("/api/v1/kb/search", json={"query": "   "}, headers=HDRS).status_code == 422
    assert client.post("/api/v1/kb/search", json={"query": "x", "kb_ids": []}, headers=HDRS).status_code == 422


def test_retrieval_result_hits_roundtrip():
    r = RetrievalResult("c", "vector", [], [], hits=[{"doc_id": "d", "score": 0.5}])
    d = r.to_dict()
    assert d["hits"] == [{"doc_id": "d", "score": 0.5}]
    r2 = RetrievalResult(d["content"], d["level"], d.get("degraded_from", []),
                         d.get("refs", []), d.get("hits", []))
    assert r2.hits == r.hits
