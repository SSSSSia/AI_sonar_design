# -*- coding: utf-8 -*-
"""T14.8 单测：小项清账（外部审查问题 9/10 + 审计覆盖）。

- 用例 15：缓存键范围语义隔离（None≠[]）；scoped 空集如实 unavailable 不越界；
- route_bases 逐条查询探测（修只测首条）；
- 用例 16：A5 LLM 整理路径排除 error 点名参数 + 候选绑 source_refs；
- 审计规则覆盖 /knowledge/writeback（v0.6 漏覆盖面）。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage import cache

# ----------------------------------------------------------------------
# 用例 15：缓存键范围语义
# ----------------------------------------------------------------------
def test_case15_cache_key_scope_modes_differ():
    assert cache.kb_cache_key("声呐", None) != cache.kb_cache_key("声呐", [])
    assert cache.kb_cache_key("声呐", ["b", "a"]) == cache.kb_cache_key("声呐", ["a", "b"])  # 排序归一
    assert cache.kb_cache_key("声呐", ["a"]) != cache.kb_cache_key("声呐", ["b"])


# ----------------------------------------------------------------------
# scoped 空集不越界（问题 9a 边界残留）
# ----------------------------------------------------------------------
CHUNKS = [("doc-a1", "第1页", "拖曳线列阵声呐的系统组成包括水听器基阵。")]


@pytest.fixture()
def svc(monkeypatch):
    from knowledge.service import GraphRAGService
    from storage import kb_scope

    s = GraphRAGService()
    s.sdk = None
    s._dynamic_sources = list(CHUNKS)
    monkeypatch.setattr(GraphRAGService, "_ensure_corpus", lambda self: None)
    monkeypatch.setattr(kb_scope, "resolve_doc_ids", lambda kb_ids: [] if kb_ids else ["doc-a1"])
    return s


def test_scoped_empty_resolution_returns_unavailable(svc):
    """选定库解析为空集（无归属文档）→ unavailable，绝不退化全库检索。"""
    r = svc.query("水听器", kb_ids=["kb-empty"])
    assert r.level == "unavailable"
    assert "未归库" in r.content


def test_unscoped_still_searches(svc):
    """None=不过滤语义不受影响（对照）。"""
    monkey_scope = svc
    from storage import kb_scope

    kb_scope.resolve_doc_ids  # noqa: B018 -（打桩已在 fixture）
    r = svc.query("水听器", kb_ids=None)
    assert r.ok and "水听器" in r.content


# ----------------------------------------------------------------------
# route_bases 逐条查询探测（问题 9c）
# ----------------------------------------------------------------------
def test_route_bases_probes_every_query(svc, monkeypatch):
    probed: list[tuple[str, str]] = []

    class _R:
        ok = True
        refs = [{"doc_id": "d"}]

    def fake_query(q, kb_ids=None):
        probed.append((str(kb_ids and kb_ids[0]), q))
        return _R()

    monkeypatch.setattr(svc, "query", fake_query)
    from storage import kb_store

    monkeypatch.setattr(kb_store, "list_bases",
                        lambda: [{"id": "b1", "name": "库一"}])
    sel = svc.route_bases(["拖曳阵组成", "声呐方程", "波束成形"])
    assert sel["mode"] == "auto" and sel["bases"][0]["selected"] is True
    assert len(probed) == 3  # 三条探测查询都打到了该库（此前只测首条）
    assert {q for _, q in probed} == {"拖曳阵组成", "声呐方程", "波束成形"}


def test_probe_queries_segments_ir0():
    from worker.tasks import _probe_queries

    ir0 = {"raw_text": "设计一台拖曳线列阵声呐。阵元数量不少于三十二个；探测距离五公里。"}
    segs = _probe_queries(ir0)
    assert len(segs) == 3 and all(len(s) >= 6 for s in segs)
    assert _probe_queries({"raw_text": ""}) == []


# ----------------------------------------------------------------------
# 用例 16：A5 LLM 路径过滤 + source_refs
# ----------------------------------------------------------------------
def _a5_ir2():
    return {"parameters": [
        {"id": "P-001", "name": "缺陷参数", "value": 1, "unit": "-", "source_type": "case",
         "source_ref": "k", "derivation": "d", "trace_id": "t1"},
        {"id": "P-002", "name": "健康参数", "value": 2, "unit": "-", "source_type": "case",
         "source_ref": "k", "derivation": "d", "trace_id": "t2"},
    ]}


def _ir4_with_error():
    return {"overall": "fail", "issues": [
        {"constraint_id": "CHK-X", "severity": "error", "message": "超差",
         "related_params": ["P-001"], "related_features": []},
    ], "csr": None}


class _FakeLLM:
    def __init__(self, raw):
        self._raw = raw

    def chat(self, messages, **kw):
        return self._raw


def test_case16_llm_candidates_exclude_error_params():
    """LLM 整理路径与兜底同规则：引用 error 点名参数的候选一律排除（用例 16）。"""
    from agents.a5_knowledge.agent import A5Agent
    import json

    raw = json.dumps({"candidates": [
        {"type": "case", "payload": {"name": "含缺陷参数案例",
                                     "params": [{"name": "缺陷参数", "value": 1, "unit": "-"}]}},
        {"type": "case", "payload": {"name": "健康案例",
                                     "params": [{"name": "健康参数", "value": 2, "unit": "-"}]}},
    ]})
    agent = A5Agent(llm=_FakeLLM(raw))
    ir5 = agent.run(None, _a5_ir2(), _ir4_with_error())
    names = [c["payload"]["name"] for c in ir5["candidates"]]
    assert names == ["健康案例"]


def test_a5_candidates_bind_source_refs():
    from agents.a5_knowledge.agent import A5Agent

    agent = A5Agent(llm=_FakeLLM('{"candidates": [{"type": "relation", "payload": '
                                 '{"from": {"name": "健康参数"}, "to": {"name": "健康参数"}}}]}'))
    ir5 = agent.run(None, _a5_ir2(), None, source_refs={"ir2_version": 3, "ir4_version": 2})
    assert ir5["candidates"][0]["payload"]["source_refs"] == {"ir2_version": 3, "ir4_version": 2}


# ----------------------------------------------------------------------
# 审计规则覆盖（v0.6 漏面）
# ----------------------------------------------------------------------
def test_audit_covers_knowledge_writeback():
    from api.audit import match_rule

    hit = match_rule("POST", "/knowledge/writeback")
    assert hit and hit[0] == "knowledge.writeback"
    hit_skip = match_rule("POST", "/knowledge/writeback/skip")
    assert hit_skip and hit_skip[0] == "knowledge.skip"
    assert match_rule("POST", "/knowledge/search") is None  # 检索不留痕（非写操作）
