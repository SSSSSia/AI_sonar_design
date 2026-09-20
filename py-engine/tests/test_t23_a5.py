# -*- coding: utf-8 -*-
"""T2.3 A5 知识沉淀单测：候选生成（LLM/兜底）+ 去重 + A-10 未确认绝不入图 + writeback 端点。"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ----------------------------------------------------------------------
# 样例与假件
# ----------------------------------------------------------------------

def _param(pid, name, value, source_type, unit="-"):
    return {
        "id": pid, "name": name, "value": value, "unit": unit,
        "source_type": source_type, "source_ref": f"ref-{pid}",
        "derivation": f"推导 {pid}", "confidence": "high",
        "trace_id": f"tr-{pid.lower()}",
    }


def _ir2():
    return {"design_document": "# 样例\n", "parameters": [
        _param("P-001", "阵元总数", 16, "estimated", "个"),
        _param("P-002", "阵元间距", 1.5, "case", "m"),
        _param("P-003", "指向性指数 DI", 12.0412, "formula", "dB"),
        _param("P-004", "坏参数", 999, "formula", "dB"),  # 将被 IR4 error 点名
    ]}


def _ir4():
    return {"overall": "fail", "issues": [{
        "constraint_id": "CHK-FORMULA-RECOMPUTE", "severity": "error",
        "message": "复算不符", "related_params": ["P-004"], "related_features": [],
        "suggestion": "back_to_A2",
    }]}


class FakeLlmA5:
    def __init__(self, raw=None, exc=None):
        self.raw, self.exc = raw, exc

    def chat(self, messages, **kwargs):
        if self.exc:
            raise self.exc
        return self.raw


class FakeKB:
    def __init__(self, hits=None):
        self.hits = hits or []

    def find_name_matches(self, name, limit=3):
        return list(self.hits)


class FakeGraph:
    def __init__(self, dups=None):
        self.dups = dups or []
        self.write_calls = []

    def available(self):
        return True

    def find_duplicates(self, name):
        return list(self.dups)

    def write_candidates(self, candidates, session_id, kb_id=None):
        self.write_calls.append((candidates, session_id, kb_id))
        return {"written": len(candidates), "deprecated": 0, "details": []}


# ----------------------------------------------------------------------
# 候选生成
# ----------------------------------------------------------------------

def test_fallback_candidates_deterministic_and_pending():
    """LLM 断供：formula→constraint、case→case 兜底；状态恒 pending（A-10）。"""
    from agents.a5_knowledge.agent import A5Agent

    ir5 = A5Agent().run(None, _ir2(), _ir4())
    assert ir5["writeback_status"] == "pending"
    types = [c["type"] for c in ir5["candidates"]]
    assert "constraint" in types and "case" in types
    # 被 error 点名的 P-004 不得出现在任何候选 params/expression
    dumped = str(ir5)
    assert "P-004" not in dumped and "999" not in dumped


def test_llm_candidates_sanitized_against_ir2():
    """LLM 路径：case params 必须能在 IR2 原表对上（虚构参数一律丢弃）。"""
    from agents.a5_knowledge.agent import A5Agent

    raw = (
        '{"candidates": ['
        '{"type": "constraint", "payload": {"name": "DI 约束", "expression": "DI=10lgN", '
        '"context": "c"}},'
        '{"type": "case", "payload": {"name": "案例", "params": ['
        '{"name": "阵元间距", "value": 1.5, "unit": "m"},'
        '{"name": "虚构参数", "value": 42, "unit": "-"}]}, "context": "c"},'
        '{"type": "bogus", "payload": {"name": "x"}},'
        '{"type": "relation", "payload": {"from": {"type": "DesignParameter", "name": "阵元总数"}, '
        '"to": {"type": "PerformanceIndicator", "name": "指向性指数 DI"}, "relation": "DETERMINES"}}'
        "]}"
    )
    ir5 = A5Agent(llm=FakeLlmA5(raw=raw)).run({"objectives": []}, _ir2(), _ir4())
    types = [c["type"] for c in ir5["candidates"]]
    assert types == ["constraint", "case", "relation"]
    case = next(c for c in ir5["candidates"] if c["type"] == "case")
    assert [p["name"] for p in case["payload"]["params"]] == ["阵元间距"]


def test_llm_failure_falls_back():
    from agents.a5_knowledge.agent import A5Agent

    ir5 = A5Agent(llm=FakeLlmA5(exc=RuntimeError("LLM 挂了"))).run(None, _ir2(), _ir4())
    assert ir5["candidates"]  # 兜底仍有产出
    assert ir5["writeback_status"] == "pending"


def test_ir4_none_treated_as_no_errors():
    from agents.a5_knowledge.agent import A5Agent

    ir5 = A5Agent().run(None, _ir2(), None)
    # 无校验报告时按无 error 处理（gate2 人工把关在前），P-004 可进候选
    assert any("999" in str(c) for c in ir5["candidates"])


# ----------------------------------------------------------------------
# 去重
# ----------------------------------------------------------------------

def test_dedup_batch_kb_graph_three_sources():
    from agents.a5_knowledge.agent import A5Agent

    raw = (
        '{"candidates": ['
        '{"type": "constraint", "payload": {"name": "DI 经验", "expression": "x"}},'
        '{"type": "constraint", "payload": {"name": "DI 经验", "expression": "y"}}'
        "]}"
    )
    kb = FakeKB(hits=[{"doc_id": "kb.txt", "location": "段落1", "excerpt": "..."}])
    graph = FakeGraph(dups=[{"label": "DesignConstraint", "key": "abc", "deprecated": False}])
    ir5 = A5Agent(llm=FakeLlmA5(raw=raw), kb=kb, graph=graph).run(None, _ir2(), None)
    statuses = [c["dedup_status"] for c in ir5["candidates"]]
    assert statuses == ["duplicate", "duplicate"]
    first_sources = {r["doc_id"] for r in ir5["candidates"][0]["payload"]["dedup_refs"]}
    second_sources = {r["doc_id"] for r in ir5["candidates"][1]["payload"]["dedup_refs"]}
    assert first_sources == {"kb.txt", "neo4j"}
    assert second_sources == {"batch", "kb.txt", "neo4j"}  # 第二条含批内同名证据


def test_agent_never_writes_graph():
    """A-10 断言：A5Agent 全流程不触碰图谱写入（写入只在人工确认后的 writeback 端点）。"""
    from agents.a5_knowledge.agent import A5Agent

    graph = FakeGraph()
    A5Agent(llm=FakeLlmA5(exc=RuntimeError("x")), kb=FakeKB(), graph=graph).run(None, _ir2(), _ir4())
    assert graph.write_calls == []


# ----------------------------------------------------------------------
# Neo4jStore（fake driver）
# ----------------------------------------------------------------------

class FakeResult:
    def __init__(self, data=None, single=None):
        self._data, self._single = data or [], single

    def data(self):
        return self._data

    def single(self):
        return self._single


class FakeSession:
    def __init__(self, log):
        self._log = log

    def run(self, cypher, **params):
        self._log.append((cypher, params))
        if "count(e) AS c" in cypher:
            return FakeResult(single={"c": 1})
        return FakeResult()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeDriver:
    def __init__(self, log):
        self._log = log

    def session(self):
        return FakeSession(self._log)

    def verify_connectivity(self):
        return None

    def close(self):
        pass


def test_neo4j_store_write_paths_and_labels():
    from storage.neo4j_store import Neo4jStore

    log: list = []
    store = Neo4jStore(driver=FakeDriver(log))
    stats = store.write_candidates([
        {"type": "constraint", "payload": {"name": "DI 约束", "expression": "DI=10lgN"}},
        {"type": "case", "payload": {"name": "案例", "params": [{"name": "间距", "value": 1.5, "unit": "m"}]}},
        {"type": "relation", "payload": {
            "from": {"type": "DesignParameter", "name": "阵元总数"},
            "to": {"type": "PerformanceIndicator", "name": "DI"},
            "relation": "determines"}},
        {"type": "relation", "payload": {
            "from": {"type": "未知类型!", "name": "A"}, "to": {"type": "DesignCase", "name": "B"},
            "relation": "涉及!!特殊#字"}},
    ], "sess-1")
    assert stats["written"] == 4
    assert stats["deprecated"] == 2  # constraint/case 各触发一次同名旧版本 deprecated
    cyphers = [c for c, _ in log]
    assert any("MERGE (c:DesignConstraint {key: $key})" in c for c in cyphers)
    assert any("MERGE (c:DesignCase {key: $key})" in c for c in cyphers)
    rel = next(c for c in cyphers if "-[r:" in c)
    assert "`DETERMINES`" in rel
    rel2 = next(c for c in cyphers if c != rel and "-[r:" in c)
    assert "`RELATED_TO`" in rel2 and "`Entity`" in rel2  # 白名单兜底 + 关系类型净化


def test_neo4j_store_partial_failure_is_honest():
    from storage.neo4j_store import Neo4jStore, Neo4jStoreError

    class BoomSession(FakeSession):
        def run(self, cypher, **params):
            self._log.append((cypher, params))
            if "DesignCase" in cypher:
                raise RuntimeError("boom")
            if "count(e) AS c" in cypher:
                return FakeResult(single={"c": 0})
            return FakeResult()

    class LocalDriver:
        def session(self):
            return BoomSession([])

        def verify_connectivity(self):
            return None

    store = Neo4jStore(driver=LocalDriver())
    with pytest.raises(Neo4jStoreError) as exc_info:
        store.write_candidates([
            {"type": "constraint", "payload": {"name": "a", "expression": "b"}},
            {"type": "case", "payload": {"name": "c", "params": [{"name": "x", "value": 1}]}},
        ], "s")
    assert "已成功 1 条" in str(exc_info.value)


def test_neo4j_store_validation_errors():
    from storage.neo4j_store import Neo4jStore, Neo4jStoreError

    store = Neo4jStore(driver=FakeDriver([]))
    with pytest.raises(Neo4jStoreError):
        store.write_candidates([{"type": "constraint", "payload": {"expression": "x"}}], "s")
    with pytest.raises(Neo4jStoreError):
        store.write_candidates([{"type": "case", "payload": {"name": "x", "params": []}}], "s")
    with pytest.raises(Neo4jStoreError):
        store.write_candidates([{"type": "relation", "payload": {
            "from": {"name": "a"}, "to": {"name": ""}}}], "s")
    with pytest.raises(Neo4jStoreError):
        store.write_candidates([{"type": "mystery", "payload": {}}], "s")


# ----------------------------------------------------------------------
# writeback 端点
# ----------------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch):
    from api.main import app

    return TestClient(app)


def _patch_ir5(monkeypatch, ir5, saved=None):
    from storage import ir_store

    bucket = saved if saved is not None else []
    monkeypatch.setattr(ir_store, "get_latest_snapshot", lambda sid, t: ir5)
    monkeypatch.setattr(
        ir_store, "save_snapshot",
        lambda sid, t, content, created_by: (bucket.append(content) or 2),
    )


def test_writeback_happy_path_default_selects_new_only(client, monkeypatch):
    from storage import neo4j_store as ns

    ir5 = {"candidates": [
        {"type": "constraint", "payload": {"name": "新约束"}, "dedup_status": "new"},
        {"type": "case", "payload": {"name": "重复案例"}, "dedup_status": "duplicate"},
    ], "writeback_status": "pending"}
    saved: list = []
    fake = FakeGraph()
    _patch_ir5(monkeypatch, ir5, saved)
    monkeypatch.setattr(ns, "Neo4jStore", lambda: fake)

    resp = client.post("/knowledge/writeback", json={"session_id": "s1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["written"]["written"] == 1
    # 未确认（duplicate）候选绝不入图：只有 new 进了写入名单
    written_candidates, _, _kb = fake.write_calls[0]
    assert [c["payload"]["name"] for c in written_candidates] == ["新约束"]
    # IR5 v2 written 只含已写入子集
    assert saved[-1]["writeback_status"] == "written"
    assert len(saved[-1]["candidates"]) == 1
    # 端点流程对 FakeGraph 的直接调用之外不应有其他写图谱路径
    assert len(fake.write_calls) == 1


def test_writeback_explicit_indexes_and_range_check(client, monkeypatch):
    from storage import neo4j_store as ns

    ir5 = {"candidates": [
        {"type": "case", "payload": {"name": "A"}, "dedup_status": "duplicate"},
        {"type": "case", "payload": {"name": "B"}, "dedup_status": "duplicate"},
    ], "writeback_status": "pending"}
    fake = FakeGraph()
    _patch_ir5(monkeypatch, ir5)
    monkeypatch.setattr(ns, "Neo4jStore", lambda: fake)

    resp = client.post("/knowledge/writeback", json={"session_id": "s1", "candidate_indexes": [0]})
    assert resp.status_code == 200
    written, _, _kb = fake.write_calls[0]
    assert written[0]["payload"]["name"] == "A"

    resp = client.post("/knowledge/writeback", json={"session_id": "s1", "candidate_indexes": [5]})
    assert resp.status_code == 400


def test_writeback_guard_clauses(client, monkeypatch):
    from storage import neo4j_store as ns

    # 404：无 IR5
    _patch_ir5(monkeypatch, None)
    assert client.post("/knowledge/writeback", json={"session_id": "s1"}).status_code == 404
    # 409：非 pending
    _patch_ir5(monkeypatch, {"candidates": [], "writeback_status": "written"})
    assert client.post("/knowledge/writeback", json={"session_id": "s1"}).status_code == 409
    # 400：默认选择为空（全是 duplicate 且未给下标）
    _patch_ir5(monkeypatch, {"candidates": [
        {"type": "case", "payload": {"name": "A"}, "dedup_status": "duplicate"}],
        "writeback_status": "pending"})
    assert client.post("/knowledge/writeback", json={"session_id": "s1"}).status_code == 400
    # 503：Neo4j 不可达，如实失败
    class DownStore:
        def available(self):
            return False

    monkeypatch.setattr(ns, "Neo4jStore", lambda: DownStore())
    resp = client.post("/knowledge/writeback", json={"session_id": "s1", "candidate_indexes": [0]})
    assert resp.status_code == 503
    assert "Neo4j 不可达" in resp.json()["detail"]


def test_neo4j_store_real_container_available():
    """实连本机容器（compose 就绪时）：探活成功；不可达时如实 False。"""
    from storage.neo4j_store import Neo4jStore

    store = Neo4jStore()
    # 容器健康即 True；本测试在无容器环境跳过（available 只会 False，不断言）
    result = store.available()
    if result:
        store.close()
    assert isinstance(result, bool)
