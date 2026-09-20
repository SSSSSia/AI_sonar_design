# -*- coding: utf-8 -*-
"""T2.4 溯源链单测：链路步骤构造（A2 derive / A3 transform / A4 validate / A5 assign）+ 写入。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage import trace_steps


def _param(pid, name, value, source_type, unit="-"):
    return {
        "id": pid, "name": name, "value": value, "unit": unit,
        "source_type": source_type, "source_ref": f"ref-{pid}",
        "derivation": f"推导 {pid}", "confidence": "high",
        "trace_id": f"tr-{pid.lower()}",
    }


def _ir2():
    return {"design_document": "# x", "parameters": [
        _param("P-001", "阵元总数", 16, "estimated", "个"),
        _param("P-002", "阵元间距", 1.5, "case", "m"),
        _param("P-003", "指向性指数 DI", 12.0412, "formula", "dB"),
    ]}


def test_build_a2_steps_derive_rows():
    rows = trace_steps.build_a2_steps(_ir2(), "snap-1", "s1")
    assert len(rows) == 3
    assert [r["step_no"] for r in rows] == [1, 2, 3]
    assert all(r["actor"] == "A2" and r["action"] == "derive" for r in rows)
    first = rows[0]
    assert first["trace_id"] == "tr-p-001"
    assert first["ir_snapshot_id"] == "snap-1"
    assert first["payload"] == {
        "param_id": "P-001", "name": "阵元总数",
        "source_type": "estimated",
        "source_ref": "ref-P-001", "derivation": "推导 P-001", "confidence": "high",
        "value": 16, "unit": "个",
    }


def test_build_a2_steps_skips_traceless_params():
    ir2 = {"parameters": [{"id": "P-009", "name": "坏参数", "trace_id": ""}]}
    assert trace_steps.build_a2_steps(ir2, "snap", "s1") == []


def test_build_a3_steps_inherits_param_trace():
    ir3 = {"features": [
        {"feature_id": "F-001", "segment": "model", "geometry": {}, "linked_param_id": "P-002"},
        {"feature_id": "F-002", "segment": "model", "geometry": {}, "linked_param_id": "P-999"},  # 悬空
    ]}
    rows = trace_steps.build_a3_steps(ir3, _ir2(), "snap-2", "s1")
    assert len(rows) == 1  # 悬空引用如实跳过（A4 负责报 error）
    assert rows[0]["trace_id"] == "tr-p-002"
    assert rows[0]["action"] == "transform"
    assert rows[0]["payload"]["feature_id"] == "F-001"


def test_build_a4_steps_validate_rows_with_suggestion():
    ir4 = {"overall": "fail", "issues": [
        {"constraint_id": "C-1", "severity": "error", "message": "x",
         "related_params": ["P-003", "P-001"], "related_features": [],
         "suggestion": "back_to_A2"},
        {"constraint_id": "C-2", "severity": "info", "message": "y",
         "related_params": [], "related_features": []},  # 无关联参数，不建行
    ]}
    rows = trace_steps.build_a4_steps(ir4, _ir2(), "snap-3", "s1")
    assert len(rows) == 1
    assert rows[0]["trace_id"] == "tr-p-003"  # 取首个关联参数
    assert rows[0]["actor"] == "A4" and rows[0]["action"] == "validate"
    assert rows[0]["payload"]["suggestion"] == "back_to_A2"


def test_build_a5_steps_assign_from_source_trace_ids():
    ir5 = {"candidates": [
        {"type": "constraint", "payload": {"name": "x", "source_trace_ids": ["tr-p-003"]},
         "dedup_status": "new"},
        {"type": "case", "payload": {"name": "无来源"}, "dedup_status": "new"},
    ]}
    rows = trace_steps.build_a5_steps(ir5, "snap-4", "s1")
    assert len(rows) == 1
    assert rows[0]["trace_id"] == "tr-p-003"
    assert rows[0]["actor"] == "A5" and rows[0]["action"] == "assign"
    assert rows[0]["payload"] == {"candidate_index": 0, "type": "constraint"}


def test_a5_agent_attaches_source_trace_ids():
    """A5 候选回填来源参数 trace_id（兜底路径）。"""
    from agents.a5_knowledge.agent import A5Agent

    ir5 = A5Agent().run(None, _ir2(), None)
    constraint = next(c for c in ir5["candidates"] if c["type"] == "constraint")
    assert constraint["payload"]["source_trace_ids"] == ["tr-p-003"]
    case = next(c for c in ir5["candidates"] if c["type"] == "case")
    assert case["payload"]["source_trace_ids"] == ["tr-p-002"]


def test_record_steps_batch_insert(monkeypatch):
    """record_steps：参数映射与 payload JSON 序列化（fake 连接）。"""
    import json

    from storage import trace_store

    calls: list = []

    class FakeCursor:
        def __init__(self):
            self.executed = None

        def executemany(self, sql, params):
            self.executed = (sql, params)
            calls.append(self.executed)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeConn:
        def __init__(self):
            self.cursor_obj = FakeCursor()
            self.committed = False

        def cursor(self):
            return self.cursor_obj

        def commit(self):
            self.committed = True

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    conn = FakeConn()
    monkeypatch.setattr(trace_store, "_connect", lambda: conn)

    n = trace_store.record_steps([
        {"trace_id": "tr-x", "step_no": 1, "actor": "A2", "action": "derive",
         "ir_snapshot_id": "snap", "payload": {"param_id": "P-001"}, "session_id": "s1"},
    ])
    assert n == 1
    sql, params = calls[0]
    assert "INSERT INTO traceability_chain" in sql
    row = params[0]
    assert row[0] == "tr-x" and row[1] == 1 and row[5] == "snap"
    assert json.loads(row[6]) == {"param_id": "P-001"}
    assert conn.committed

    assert trace_store.record_steps([]) == 0  # 空行不触库
