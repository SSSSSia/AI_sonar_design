# -*- coding: utf-8 -*-
"""T14.2 单测：提案应用正确性与确认幂等（外部审查问题 5/11）。

回归验收案例（审查 §6）：
  05 多轮提案：100→120→140 全部应用，终值必须是 140（旧提案不得反向覆盖）；
  06 同值符号：width=100; length=100 只改 length，width 保持不变；
  11 重复确认：双击/重试不产生冲突参数版本（归并+状态机单向+重试路径）。
存储层 monkeypatch，无真实 DB。
"""

import copy
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cad_agent.param_proposals import (
    _own_symbol,
    apply_forward_update,
    apply_proposals,
    merge_by_param,
    sync_derivation,
)
from solver.recompute import parse_inputs_from_derivation

ADMIN = {"X-User-Id": "a", "X-User-Roles": "admin"}


def _row(pid, new, round_no, created_at, proposal_id=None):
    return {"id": proposal_id or f"p-{round_no}", "session_id": "s-1", "tune_id": "t-1",
            "param_id": pid, "param_name": f"参数{pid}", "unit": "mm",
            "old_value": 100, "new_value": new, "delta_pct": 20.0, "dimension": 0,
            "round_no": round_no, "created_at": created_at, "status": "proposed",
            "ir3_version": 3, "baseline_ir_version": 2, "note": None}


def _ir2(derivation="len=100 mm", pid="P-001"):
    return {"design_document": "# 方案", "parameters": [
        {"id": pid, "name": "缆长", "value": 100, "unit": "mm", "source_type": "estimated",
         "source_ref": "", "derivation": derivation, "confidence": "medium", "trace_id": "t"},
    ]}


# ----------------------------------------------------------------------
# 用例 05：多轮同参数提案归并
# ----------------------------------------------------------------------
def test_merge_by_param_keeps_latest_round():
    rows = [_row("P-001", 120, 1, "2026-10-08T10:00:00"),
            _row("P-001", 140, 2, "2026-10-08T11:00:00")]
    winners, losers = merge_by_param(rows)
    assert len(winners) == 1 and winners[0]["new_value"] == 140
    assert len(losers) == 1 and losers[0]["new_value"] == 120


def test_merge_by_param_created_at_tiebreak():
    rows = [_row("P-001", 120, 2, "2026-10-08T09:00:00"),
            _row("P-001", 140, 2, "2026-10-08T09:30:00")]
    winners, losers = merge_by_param(rows)
    assert winners[0]["new_value"] == 140 and losers[0]["new_value"] == 120


def test_merge_by_param_multi_params_unaffected():
    rows = [_row("P-001", 120, 1, "2026-10-08T10:00:00"),
            _row("P-002", 60, 1, "2026-10-08T10:00:00")]
    winners, losers = merge_by_param(rows)
    assert len(winners) == 2 and losers == []


def test_case05_all_apply_value_not_rolled_back():
    """100→120(r1)、100→140(r2) 一起确认：终值必须 140，derivation 与值一致。"""
    rows = [_row("P-001", 120, 1, "2026-10-08T10:00:00"),
            _row("P-001", 140, 2, "2026-10-08T11:00:00")]
    winners, _losers = merge_by_param(rows)
    new_ir2 = apply_proposals(_ir2(), winners, round_no=2)
    p = new_ir2["parameters"][0]
    assert p["value"] == 140
    assert parse_inputs_from_derivation(p["derivation"])["len"] == 140.0


# ----------------------------------------------------------------------
# 用例 06：同值符号只改目标
# ----------------------------------------------------------------------
def test_sync_by_symbol_only_target():
    d = "width=100; length=100（外廓）"
    out = sync_derivation(d, 100.0, 120.0, "r1", symbol="length")
    assert "length=120" in out and "width=100" in out
    assert "width=120" not in out


def test_own_symbol_kin_match():
    sym = _own_symbol({"id": "LENGTH", "derivation": "width=100; length=100"}, 100.0)
    assert sym == "length"


def test_case06_apply_only_target_param():
    """width/length 同值：改 LENGTH 参数，width 的 100 不被串改。"""
    ir2 = {"design_document": "# 方案", "parameters": [
        {"id": "LENGTH", "name": "长度", "value": 100, "unit": "mm", "source_type": "estimated",
         "source_ref": "", "derivation": "width=100; length=100", "confidence": "medium",
         "trace_id": "t"},
    ]}
    applied = [{"param_id": "LENGTH", "param_name": "长度", "unit": "mm",
                "old_value": 100, "new_value": 120, "delta_pct": 20.0, "dimension": 0}]
    new_ir2 = apply_proposals(ir2, applied, round_no=1)
    p = new_ir2["parameters"][0]
    assert p["value"] == 120
    assert "length=120" in p["derivation"] and "width=100" in p["derivation"]


def test_ambiguous_without_kin_not_inlined():
    """无亲缘符号且同值歧义：不内联替换（宁缺勿错），value 已更新、留痕注明。"""
    ir2 = _ir2(derivation="width=100; length=100", pid="P-009")
    applied = [{"param_id": "P-009", "param_name": "某长", "unit": "mm",
                "old_value": 100, "new_value": 120, "delta_pct": 20.0, "dimension": 0}]
    new_ir2 = apply_proposals(ir2, applied, round_no=1)
    p = new_ir2["parameters"][0]
    assert p["value"] == 120
    assert "width=100" in p["derivation"] and "length=100" in p["derivation"]  # 未误改
    assert "同值符号" in p["derivation"] and "E1 实测修订" in p["derivation"]


def test_forward_update_symbol_sync():
    ir2 = {"design_document": "# 方案", "parameters": [
        {"id": "LENGTH", "name": "长度", "value": 100, "unit": "mm", "source_type": "estimated",
         "source_ref": "", "derivation": "width=100; length=100", "confidence": "medium",
         "trace_id": "t"},
    ]}
    new_ir2 = apply_forward_update(ir2, "LENGTH", 120.0, "任务书修订")
    p = new_ir2["parameters"][0]
    assert p["value"] == 120
    assert "length=120" in p["derivation"] and "width=100" in p["derivation"]
    assert "修订记录（E1 正向参数更新）" in new_ir2["design_document"]


def test_ir2_input_immutable():
    ir2 = _ir2()
    original = copy.deepcopy(ir2)
    applied = [{"param_id": "P-001", "param_name": "缆长", "unit": "mm",
                "old_value": 100, "new_value": 120, "delta_pct": 20.0, "dimension": 0}]
    apply_proposals(ir2, applied, round_no=1)
    assert ir2 == original  # A-12 旧快照不可变


# ----------------------------------------------------------------------
# API：confirm 端点归并 + 幂等（用例 11 的状态机面）
# ----------------------------------------------------------------------
def test_confirm_merges_and_marks_superseded(client, patched, monkeypatch):
    from storage import proposal_store

    rows = [_row("P-001", 120, 1, "2026-10-08T10:00:00", "p1"),
            _row("P-001", 140, 2, "2026-10-08T11:00:00", "p2")]
    marks = []
    monkeypatch.setattr(proposal_store, "list_for_session",
                        lambda sid, status=None: rows if status == "proposed" else [])
    monkeypatch.setattr(proposal_store, "mark",
                        lambda ids, status, by, applied_ir2_version=None:
                        marks.append((list(ids), status)) or len(ids))

    r = client.post("/api/v1/cad-agent/sessions/s-1/param-proposals/confirm", headers=ADMIN,
                    json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] == 1 and body["ir2_version"] == 2
    assert body["superseded"] == ["p1"]
    new_ir2 = patched["saved"][-1][1]
    assert new_ir2["parameters"][0]["value"] == 140  # 终值不被 r1 的 120 反向覆盖
    assert (["p2"], "applied") in marks and (["p1"], "superseded") in marks


def test_confirm_double_submit_hits_retry_path(client, patched, monkeypatch):
    """重复确认：无 proposed 但有 applied → 仅重派（幂等，不产生新 IR2 版本）。"""
    from storage import proposal_store

    applied_row = _row("P-001", 140, 2, "2026-10-08T11:00:00", "p2")
    applied_row["status"] = "applied"
    applied_row["applied_ir2_version"] = 2
    calls = {"redispatch": 0}
    monkeypatch.setattr(proposal_store, "list_for_session",
                        lambda sid, status=None: [] if status == "proposed" else [applied_row])
    monkeypatch.setattr("api.cad_agent._redispatch_a4",
                        lambda sid: calls.__setitem__("redispatch", calls["redispatch"] + 1))

    r = client.post("/api/v1/cad-agent/sessions/s-1/param-proposals/confirm", headers=ADMIN,
                    json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] == 0 and body["redispatched"] is True
    assert calls["redispatch"] == 1
    assert len(patched["saved"]) == 0  # 幂等：不追加 IR2 版本


# ----------------------------------------------------------------------
# pytest fixture：client + patched（与 test_t122_forward 同款）
# ----------------------------------------------------------------------
@pytest.fixture()
def client():
    from api.main import app

    return TestClient(app)


@pytest.fixture()
def patched(monkeypatch):
    from storage import ir_store, param_update_store

    state = {
        "session": {"id": "s-1", "pipeline_status": "paused", "current_stage": "gate2"},
        "ir2": _ir2(),
        "depth": 0,
        "dispatched": [],
        "invalidated": [],
        "saved": [],
    }
    monkeypatch.setattr(ir_store, "get_session", lambda sid: state["session"])
    monkeypatch.setattr(ir_store, "get_latest_snapshot",
                        lambda sid, t: state["ir2"] if t == "IR2" else None)
    monkeypatch.setattr(ir_store, "save_snapshot",
                        lambda sid, t, c, created_by: state["saved"].append((t, c)) or 2)
    monkeypatch.setattr(ir_store, "mark_invalidated",
                        lambda sid, types: state["invalidated"].extend(types) or types)

    monkeypatch.setattr(param_update_store, "current_depth", lambda sid: state["depth"])
    monkeypatch.setattr(param_update_store, "record",
                        lambda *a, **k: {"id": "u-1"})

    class _A4:
        @staticmethod
        def delay(sid):
            state["dispatched"].append(("a4", sid))

    import worker.tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "a4_task", _A4)
    return state
