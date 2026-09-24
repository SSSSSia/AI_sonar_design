# -*- coding: utf-8 -*-
"""T8.1 微调域存储单测（PHASE-008）：tune_store 三表状态机 + 出口序列化 + 启动自愈。

真库（tests 与现网共用 sonarcad 库）：造固定 uuid 的 design_sessions 测试行
（00000000-...-2000000000NN 段，teardown 删除级联清微调表），不碰现网数据。
"""

from __future__ import annotations

import threading
import uuid as uuid_mod

import psycopg
import pytest

from storage import tune_store
from storage.tune_store import _connect

# 固定测试段 uuid（带横线——design_sessions.id 是 uuid 列，T6.3 教训）
DESIGN_SID = "00000000-0000-0000-0000-200000000001"


@pytest.fixture(scope="module", autouse=True)
def design_session_row():
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO design_sessions (id, project_id, pipeline_status, title)
               SELECT %s::uuid, p.id, 'paused', 't81-微调存储单测' FROM projects p LIMIT 1
               ON CONFLICT (id) DO NOTHING""",
            (DESIGN_SID,),
        )
    yield
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM design_sessions WHERE id = %s", (DESIGN_SID,))


def _make_session(status: str = "active") -> dict:
    row = tune_store.create_tune_session(DESIGN_SID, baseline_ir_version=3, created_by="t81", note="单测")
    if status != "active":
        tune_store.update_tune_status(row["id"], status)
        row = tune_store.get_tune_session(row["id"])
    return row


# ----------------------------------------------------------------------
# cad_tune_sessions
# ----------------------------------------------------------------------
def test_tune_session_lifecycle():
    row = _make_session()
    try:
        assert row["design_session_id"] == DESIGN_SID
        assert row["status"] == "active"
        assert row["baseline_ir_version"] == 3
        # uuid/datetime 出口序列化（psycopg 原生对象直进契约会炸 jsonschema——T6.7 教训）
        assert isinstance(row["id"], str) and uuid_mod.UUID(row["id"])
        assert isinstance(row["created_at"], str) and "T" in row["created_at"]

        got = tune_store.get_tune_session(row["id"])
        assert got["id"] == row["id"]
        active = tune_store.get_active_tune_session(DESIGN_SID)
        assert active["id"] == row["id"]

        upd = tune_store.update_tune_status(row["id"], "finalized", note="定稿")
        assert upd["status"] == "finalized" and upd["note"] == "定稿"
        assert tune_store.get_active_tune_session(DESIGN_SID) is None
    finally:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM cad_tune_sessions WHERE id = %s", (row["id"],))


def test_tune_session_invalid_status_rejected():
    with pytest.raises(ValueError, match="非法微调会话状态"):
        tune_store.update_tune_status(str(uuid_mod.uuid4()), "running")


def test_list_tune_sessions_desc():
    import time

    rows = [_make_session()]
    time.sleep(0.05)  # created_at 精度内同刻创建排序不稳定，错开
    rows.append(_make_session())
    try:
        listed = tune_store.list_tune_sessions(DESIGN_SID)
        ids = [r["id"] for r in listed]
        assert set(ids) == {r["id"] for r in rows}
        assert ids.index(rows[1]["id"]) < ids.index(rows[0]["id"])  # DESC：后建在前
        assert all(r["design_session_id"] == DESIGN_SID for r in listed)
    finally:
        for r in rows:
            with _connect() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM cad_tune_sessions WHERE id = %s", (r["id"],))


# ----------------------------------------------------------------------
# cad_tune_rounds
# ----------------------------------------------------------------------
def test_round_lifecycle_and_unique_constraint():
    tune = _make_session()
    try:
        assert tune_store.latest_round_no(tune["id"]) == 0
        r1 = tune_store.create_round(tune["id"], 1, baseline_ir_version=3,
                                     prompt="声学罩加厚 5mm", selection={"kind": "Face", "area_mm2": 120.5},
                                     created_by="t81")
        assert r1["round_no"] == 1 and r1["status"] == "running"
        assert r1["selection"]["kind"] == "Face"  # jsonb 出口为 dict
        assert tune_store.latest_round_no(tune["id"]) == 1

        # 白名单更新：轮次收敛 → awaiting_upload → done + 回填 IR3 版本
        tune_store.update_round(r1["id"], status="awaiting_upload", script="result = model")
        tune_store.update_round(r1["id"], status="done", result_ir3_version=4,
                                llm_trace=[{"call_id": 1, "ok": True}])
        got = tune_store.get_round(tune["id"], 1)
        assert got["status"] == "done" and got["script"] == "result = model"
        assert got["result_ir3_version"] == 4 and got["llm_trace"][0]["call_id"] == 1

        # 同会话 round_no 唯一约束
        tune_store.create_round(tune["id"], 2, baseline_ir_version=4, prompt="再改", selection=None, created_by="t81")
        with pytest.raises(psycopg.errors.UniqueViolation):
            tune_store.create_round(tune["id"], 2, baseline_ir_version=4, prompt="重复", selection=None, created_by="t81")
        assert len(tune_store.list_rounds(tune["id"])) == 2
        # 白名单外的字段被忽略（不抛错、不落库）
        tune_store.update_round(r1["id"], prompt="不应被改")
        assert tune_store.get_round(tune["id"], 1)["prompt"] == "声学罩加厚 5mm"
    finally:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM cad_tune_sessions WHERE id = %s", (tune["id"],))


# ----------------------------------------------------------------------
# cad_tune_tool_calls：pending → done | timeout
# ----------------------------------------------------------------------
def test_tool_call_done_path_and_late_result_rejected():
    tune = _make_session()
    try:
        rnd = tune_store.create_round(tune["id"], 1, 3, "加孔阵", None, "t81")
        call = tune_store.create_tool_call(rnd["id"], "execute_edit_code", {"code": "result = model"})
        assert call["status"] == "pending" and isinstance(call["id"], int)

        # 插件长轮询视角：cursor=0 能取到；cursor 越过后取不到
        pending = tune_store.poll_tool_calls(tune["id"], after_id=0)
        assert [c["call_id"] for c in pending] == [call["id"]]
        assert pending[0]["round_no"] == 1
        assert tune_store.poll_tool_calls(tune["id"], after_id=call["id"]) == []

        done = tune_store.complete_tool_call(call["id"], {"ok": True, "stdout": "ok", "quality": {"status": "pass"}})
        assert done["status"] == "done" and done["result"]["quality"]["status"] == "pass"
        # 迟到回传拒收（已完成/超时后 complete 返回 None——如实拒绝）
        assert tune_store.complete_tool_call(call["id"], {"ok": True}) is None
    finally:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM cad_tune_sessions WHERE id = %s", (tune["id"],))


def test_wait_tool_call_timeout_marks_and_rejects_late():
    tune = _make_session()
    try:
        rnd = tune_store.create_round(tune["id"], 1, 3, "改", None, "t81")
        call = tune_store.create_tool_call(rnd["id"], "execute_edit_code", {"code": "x"})
        row = tune_store.wait_tool_call(call["id"], timeout_s=0.8, poll_interval_s=0.2)
        assert row["status"] == "timeout"
        assert "超时" in row["result"]["error"]
        # 超时后插件迟到回传被拒收
        assert tune_store.complete_tool_call(call["id"], {"ok": True}) is None
    finally:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM cad_tune_sessions WHERE id = %s", (tune["id"],))


def test_wait_tool_call_returns_on_completion():
    tune = _make_session()
    try:
        rnd = tune_store.create_round(tune["id"], 1, 3, "改", None, "t81")
        call = tune_store.create_tool_call(rnd["id"], "execute_edit_code", {"code": "x"})

        def _late_complete():
            import time

            time.sleep(0.4)
            tune_store.complete_tool_call(call["id"], {"ok": True, "doc_summary": "bbox 100x20x20"})

        threading.Thread(target=_late_complete).start()
        row = tune_store.wait_tool_call(call["id"], timeout_s=5, poll_interval_s=0.1)
        assert row["status"] == "done" and row["result"]["doc_summary"].startswith("bbox")
    finally:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM cad_tune_sessions WHERE id = %s", (tune["id"],))


# ----------------------------------------------------------------------
# 启动自愈（expire_running_rounds）
# ----------------------------------------------------------------------
def test_expire_running_rounds_self_heal():
    tune = _make_session()
    try:
        rnd = tune_store.create_round(tune["id"], 1, 3, "中断轮", None, "t81")
        call = tune_store.create_tool_call(rnd["id"], "execute_edit_code", {"code": "x"})
        # 跑一个正常轮不受影响
        other = tune_store.create_round(tune["id"], 2, 3, "正常轮", None, "t81")
        tune_store.update_round(other["id"], status="done", result_ir3_version=5)

        n = tune_store.expire_running_rounds()
        assert n >= 1
        got = tune_store.get_round(tune["id"], 1)
        assert got["status"] == "failed" and "服务重启" in got["error"]
        timed_out = tune_store.get_tool_call(call["id"])
        assert timed_out["status"] == "timeout"
        assert tune_store.get_round(tune["id"], 2)["status"] == "done"  # 完成轮不受牵连
    finally:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM cad_tune_sessions WHERE id = %s", (tune["id"],))
