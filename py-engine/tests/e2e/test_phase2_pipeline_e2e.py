# -*- coding: utf-8 -*-
"""阶段 2 端到端（T2.2/T2.3/T2.4 验收）：五步闭环 + 审阅关通过流与驳回回流 + 溯源反查。

前置（由运行方保证，缺失则 skip）：
- 数据底座 compose 全部 healthy（含 Neo4j——知识回写实连）
- Java 四服务已启动**且为阶段 2 代码**（gate 接线 + 裁决 API + 溯源反查，重启后生效）
- py-engine worker 与 api(8090) 已启动**且为阶段 2 代码**（a4/a5 任务 + 队列，重启 worker 生效）
- LLM 供应商可用（llm_providers 最高优先级行或 .env 真实 key）

流程（单会话走完整门语义）：
  START → A1 → A2 → gate1 暂停 → 批准 → A3 → A4 → gate2 暂停 →
  驳回(rework=A2) → 下游失效断言 → A2 局部回算 → gate1 再次暂停 → 批准 →
  A3/A4 重跑 → gate2 批准 → A5 → completed → 知识审阅关批准 → IR5 written →
  溯源反查（IR2 参数 trace_id → IR0 原文 + IR1 检索引用）

运行（py-engine 目录）：python -m pytest tests/e2e/test_phase2_pipeline_e2e.py -v
（真实 LLM 全程约 15~20 分钟：管线两次经过 A2–A4）
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

GATEWAY = "http://127.0.0.1:8080"
PYAPI = "http://127.0.0.1:8090"

STANDARD_REQUIREMENT = (
    "设计一个拖曳线列阵声呐，16 个水听器阵元，阵元间距 1.5 米，最大工作深度 300 米。"
)


def _pg_dsn() -> str:
    from app_config import DATABASE_URL

    return DATABASE_URL.replace("+psycopg", "")


def _pg_exec(sql: str, fetch: bool = False):
    import psycopg

    with psycopg.connect(_pg_dsn()) as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall() if fetch else None
        conn.commit()
    return rows


def _stack_ready() -> tuple[bool, str]:
    for name, url in (
        ("gateway", f"{GATEWAY}/api/v1/auth/login"),
        ("py-engine", f"{PYAPI}/healthz"),
    ):
        try:
            httpx.post(url, json={}, timeout=5) if "login" in url else httpx.get(url, timeout=5)
        except Exception as exc:
            return False, f"{name} 不可达: {exc}"
    rows = _pg_exec(
        "SELECT count(*) FROM llm_providers WHERE enabled AND priority = (SELECT max(priority) FROM llm_providers)",
        fetch=True,
    )
    if rows and rows[0][0] > 0:
        return True, ""
    from app_config import LLM_API_KEY, LLM_BASE_URL

    if LLM_BASE_URL and LLM_API_KEY and not LLM_API_KEY.startswith("your_api"):
        return True, ""
    return False, "无可用 LLM 供应商（llm_providers 空且 .env 未配置真实 key）"


@pytest.fixture(scope="module")
def stack():
    ok, reason = _stack_ready()
    if not ok:
        pytest.skip(f"e2e 前置不满足：{reason}")
    yield


def _login() -> str:
    r = httpx.post(
        f"{GATEWAY}/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["token"]


def _cleanup(headers, sid=None, pid=None):
    """成功后清理本 run 的会话与项目（失败不清理，保留现场排查）。先会话后项目。"""
    if sid:
        r = httpx.delete(f"{GATEWAY}/api/v1/workflow/sessions/{sid}", headers=headers,
                         params={"force": "true"}, timeout=120)
        print(f"cleanup session -> {r.status_code}")
    if pid:
        r = httpx.delete(f"{GATEWAY}/api/v1/projects/{pid}", headers=headers, timeout=15)
        print(f"cleanup project -> {r.status_code}")


def _session_state(headers, sid: str) -> dict:
    r = httpx.get(f"{GATEWAY}/api/v1/workflow/sessions/{sid}", headers=headers, timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _wait_for(headers, sid: str, want_status: str, want_stage: str | None, budget_s: float) -> dict:
    """轮询到指定状态/阶段；误入终态立即失败。"""
    deadline = time.time() + budget_s
    while time.time() < deadline:
        data = _session_state(headers, sid)
        status, stage = data["pipelineStatus"], data.get("currentStage")
        if status == "paused" and (want_stage is None or stage == want_stage):
            return data
        if status in ("completed", "failed"):
            raise AssertionError(f"期望 {want_status}/{want_stage}，实际终态 {status}/{stage}（stale? 查看服务日志）")
        time.sleep(6)
    raise AssertionError(f"等待 {want_status}/{want_stage} 超时（{budget_s:.0f}s）")


def _ir(headers, sid: str, ir_type: str) -> dict:
    r = httpx.get(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/irs/{ir_type}", headers=headers, timeout=10)
    assert r.status_code == 200, f"{ir_type} 快照缺失: {r.text}"
    data = r.json()["data"]
    content = data["content"] if isinstance(data["content"], dict) else json.loads(data["content"])
    return {"version": data["version"], "invalidated": data["invalidated"], "content": content}


def _versions(headers, sid: str, ir_type: str) -> list[dict]:
    r = httpx.get(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/irs/{ir_type}/versions", headers=headers, timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_phase2_full_gate_loop(stack):
    token = _login()
    headers = {"Authorization": f"Bearer {token}"}
    run_tag = f"p2e2e-{uuid.uuid4().hex[:8]}"
    total_budget = 2400  # 40 分钟：管线两次经过 A2–A4（真实 LLM）

    # 1. 建项目 + 会话（IR0）+ START
    r = httpx.post(f"{GATEWAY}/api/v1/projects", headers=headers,
                   json={"name": run_tag, "code": run_tag}, timeout=15)
    assert r.status_code == 200, r.text
    pid = r.json()["data"]["id"]
    r = httpx.post(f"{GATEWAY}/api/v1/workflow/sessions", headers=headers,
                   json={"projectId": pid, "title": "阶段2门语义e2e", "requirement": STANDARD_REQUIREMENT},
                   timeout=15)
    assert r.status_code == 200, r.text
    sid = r.json()["data"]["id"]
    r = httpx.post(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/events", headers=headers,
                   json={"event": "START"}, timeout=15)
    assert r.status_code == 200, r.text

    # 2. gate1 暂停（A2 完成）→ 批准
    _wait_for(headers, sid, "paused", "gate1", total_budget)
    ir2_v1 = _ir(headers, sid, "IR2")
    assert not ir2_v1["invalidated"]
    params = ir2_v1["content"]["parameters"]
    assert all(p["trace_id"].startswith("tr-") for p in params), "IR2 四件套 trace_id 缺失"
    r = httpx.post(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/reviews/gate1", headers=headers,
                   json={"decision": "approved", "annotations": ["e2e gate1 通过流"]}, timeout=15)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["resumed_to"] == "A3"

    # 3. gate2 暂停（A3+A4 完成）→ 断言 IR3/IR4 → 驳回（显式 rework A2）
    _wait_for(headers, sid, "paused", "gate2", total_budget)
    ir3_v1 = _ir(headers, sid, "IR3")
    assert ir3_v1["content"]["step_file"]["bucket"]
    ir4_v1 = _ir(headers, sid, "IR4")
    assert ir4_v1["content"]["overall"] in ("pass", "fail")
    severities = {i["severity"] for i in ir4_v1["content"]["issues"]}
    assert severities <= {"error", "warning", "info"}, "IR4 severity 超出三级风险"
    r = httpx.post(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/reviews/gate2", headers=headers,
                   json={"decision": "rejected", "annotations": ["e2e 驳回回流演练"],
                         "reworkStage": "A2"}, timeout=15)
    assert r.status_code == 200, r.text
    effects = r.json()["data"]
    assert effects["rework_stage"] == "A2"
    assert effects["invalidated"] == ["IR2", "IR3", "IR4", "IR5"]

    # 4. 下游失效断言：IR2/IR3/IR4 全部版本带 invalidated 标记
    for t in ("IR2", "IR3", "IR4"):
        for v in _versions(headers, sid, t):
            assert v["invalidated"], f"{t} v{v['version']} 未标记失效"

    # 5. 局部回算：A2 重跑 → gate1 再次暂停（门语义：新产物重新过闸）→ 批准
    _wait_for(headers, sid, "paused", "gate1", total_budget)
    ir2_v2 = _ir(headers, sid, "IR2")
    assert ir2_v2["version"] > ir2_v1["version"], "A2 重跑应产出 IR2 新版本"
    assert not ir2_v2["invalidated"], "新版本 IR2 不应被标记失效"
    r = httpx.post(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/reviews/gate1", headers=headers,
                   json={"decision": "approved", "annotations": ["e2e 回算后二次过闸"]}, timeout=15)
    assert r.status_code == 200, r.text

    # 6. 二次 gate2 → 批准 → A5 → completed
    _wait_for(headers, sid, "paused", "gate2", total_budget)
    ir4_v2 = _ir(headers, sid, "IR4")
    assert ir4_v2["version"] > ir4_v1["version"]
    r = httpx.post(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/reviews/gate2", headers=headers,
                   json={"decision": "approved", "annotations": ["e2e gate2 通过流"]}, timeout=15)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["resumed_to"] == "A5"

    deadline = time.time() + total_budget
    while time.time() < deadline:
        data = _session_state(headers, sid)
        if data["pipelineStatus"] == "completed":
            break
        assert data["pipelineStatus"] != "failed", "A5 阶段失败"
        time.sleep(6)
    else:
        raise AssertionError("等待会话完成超时")
    ir5 = _ir(headers, sid, "IR5")
    assert ir5["content"]["writeback_status"] == "pending", "A5 产出必须 pending（A-10）"

    # 7. 知识审阅关：批准 → py-engine 回写 Neo4j → IR5 v2 written
    r = httpx.post(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/reviews/knowledge", headers=headers,
                   json={"decision": "approved", "annotations": ["e2e 知识确认"]}, timeout=30)
    assert r.status_code == 200, r.text
    assert r.json()["data"].get("writeback") != "failed", "Neo4j 回写失败（见 annotations）"
    ir5_v2 = _ir(headers, sid, "IR5")
    assert ir5_v2["version"] > ir5["version"]
    assert ir5_v2["content"]["writeback_status"] == "written"

    # 8. 裁决历史：5 条（gate1 批 / gate2 驳 / gate1 批 / gate2 批 / knowledge 批）
    r = httpx.get(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/reviews", headers=headers, timeout=10)
    records = r.json()["data"]
    assert len(records) == 5, f"裁决记录数异常: {len(records)}"
    decisions = [(rec["gate"], rec["decision"]) for rec in records]
    assert ("knowledge", "approved") in decisions
    assert ("gate2", "rejected") in decisions

    # 9. 溯源反查（E2 判据）：IR2 参数 trace_id → IR0 原文 + IR1 检索引用
    trace_param = next(
        p for p in ir2_v2["content"]["parameters"] if p["source_type"] == "formula"
    )
    r = httpx.get(f"{GATEWAY}/api/v1/trace/{trace_param['trace_id']}", headers=headers, timeout=10)
    assert r.status_code == 200, r.text
    trace = r.json()["data"]
    actors = {s["actor"] for s in trace["steps"]}
    assert "A2" in actors and trace["steps"][0]["action"] == "derive"
    ctx = trace["context"]
    assert "拖曳线列阵" in (ctx.get("ir0_raw_text") or ""), "溯源未及 IR0 需求原文"
    assert ctx.get("ir1_retrieved_refs"), "溯源未及 IR1 检索引用"
    assert (ctx.get("ir2_param") or {}).get("id") == trace_param["id"]

    print(f"\n阶段2 e2e 通过：session={sid} run={run_tag}")
    print(f"  IR2 v1(invalidated)→v2；gate 流：gate1批→gate2驳(rework A2)→gate1批→gate2批→knowledge批")
    print(f"  IR5 written；trace 反查 {trace_param['trace_id']} OK")
    _cleanup(headers, sid, pid)
