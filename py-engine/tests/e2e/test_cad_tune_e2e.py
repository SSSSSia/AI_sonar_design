# -*- coding: utf-8 -*-
"""PHASE-008 微调 e2e（fake 插件客户端驱动 HTTP 协议全流程）：

自建 mock LLM（随机空闲端口+独立 provider 行，不干扰现网供应商）→ 建微调会话 →
消息 → 长轮询取工具调用（e2e 扮演 FreeCAD 插件回传脚本化结果）→ 轮次收敛 →
上传**真实 STEP**（cadquery 生成，几何实测应提取出特征）→ IR3 版本追加 →
finalize → gate2 批准冻结（review 的 ir_snapshot_id = 微调版本快照）。

前置：gateway/workflow/py-engine/数据底座可达（LLM 由本测试自带 mock 满足）。
基线设计会话与 IR0~A3 管线产物直接落库构造——主链路已由 towed_array/multikb e2e 覆盖，
本测试聚焦微调协议闭环。运行（py-engine 目录）：python -m pytest tests/e2e/test_cad_tune_e2e.py -v
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

GATEWAY = "http://127.0.0.1:8080"
PYAPI = "http://127.0.0.1:8090"
SID = "00000000-0000-0000-0000-400000000001"
PROVIDER_NAME = "mock-tune-e2e"


def _pg_exec(sql: str, fetch: bool = False):
    import psycopg

    from app_config import DATABASE_URL

    with psycopg.connect(DATABASE_URL.replace("+psycopg", "")) as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall() if fetch else None
        conn.commit()
    return rows


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))  # 容器化 py-api 经 host.docker.internal 访问，须对外监听
        return s.getsockname()[1]


def _build_step(path: str, outer_r: float = 10.0) -> None:
    """cadquery 生成真实 STEP（基线=方底座；微调版=底座+圆环，尺寸可区分）。"""
    import cadquery as cq

    box = cq.Workplane("XY").box(100, 20, 20)
    if outer_r > 10.0:  # 微调版：叠一个圆环柱（几何实测可提取出两段/一体特征）
        ring = cq.Workplane("XY").circle(outer_r).circle(outer_r - 4).extrude(30)
        model = box.union(ring)
    else:
        model = box
    cq.exporters.export(model, path, exportType="STEP")


def _login() -> str:
    r = httpx.post(f"{GATEWAY}/api/v1/auth/login",
                   json={"username": "admin", "password": "admin123"}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["data"]["token"]


def _h(token: str) -> dict:
    """网关 JWT + py-engine 信任头（直连 8090 时 authz 要求；经网关会被覆盖，无害）。"""
    return {"Authorization": f"Bearer {token}",
            "X-User-Id": "t87-e2e-admin", "X-User-Roles": "admin"}


def _wait_round(token: str, tune_id: str, round_no: int, statuses: tuple[str, ...],
                timeout_s: float = 120.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = httpx.get(f"{PYAPI}/api/v1/cad-agent/tune/{tune_id}/rounds/{round_no}",
                      headers=_h(token), timeout=10)
        if r.status_code == 200 and r.json().get("status") in statuses:
            return r.json()
        time.sleep(1)
    raise AssertionError(f"轮次 r{round_no} {timeout_s}s 内未进入 {statuses}")


@pytest.fixture(scope="module")
def env():
    from storage import ir_store, object_store
    from app_config import BUCKET_STEP_MODELS

    # ---- 栈可达性 ----
    try:
        httpx.post(f"{GATEWAY}/api/v1/auth/login", json={}, timeout=5)
        httpx.get(f"{PYAPI}/healthz", timeout=5)
    except Exception as exc:
        pytest.skip(f"e2e 前置不满足（服务不可达）: {exc}")

    # ---- 自带 mock LLM（随机空闲端口，独立 provider 行，最高优先级）----
    port = _free_port()
    import os

    mock_log = os.path.join(tempfile.gettempdir(), "t87_mock_llm.log")
    mock_err = os.path.join(tempfile.gettempdir(), "t87_mock_llm.err")
    Path(mock_log).write_text("", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.e2e.mock_llm_server"],
        cwd=str(Path(__file__).resolve().parents[2]),
        env={**os.environ, "MOCK_LLM_PORT": str(port), "MOCK_LLM_LOG": mock_log},
        stdout=subprocess.DEVNULL, stderr=open(mock_err, "w"),
    )
    # mock LLM 绑定 0.0.0.0；注册地址供 py-api 消费——py-api 容器化时须用 host.docker.internal
    # （容器内 127.0.0.1 不是宿主），宿主开发模式默认 127.0.0.1 不变（T10.5 适配）
    mock_host = os.environ.get("CAD_TUNE_MOCK_HOST", "127.0.0.1")
    base_url = f"http://{mock_host}:{port}"
    ready = False
    for _ in range(30):
        try:
            # 健康探测从测试进程（宿主）走本机回环；base_url（注册给 py-api）才是消费地址
            h = httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=2).json()
            if h.get("tune_routing"):
                ready = True
                break
        except Exception:
            time.sleep(1)
    if not ready:
        proc.terminate()
        pytest.skip("mock LLM 未就绪或无微调路由（旧进程占用 8091？）")

    _pg_exec(
        f"""INSERT INTO llm_providers (name, base_url, api_key, model, enabled, priority, timeout_s)
            VALUES ('{PROVIDER_NAME}', '{base_url}/v1', 'sk-e2e-tune', 'mock', true, 1000, 60)
            ON CONFLICT (name) DO UPDATE SET enabled = true, priority = 1000, base_url = '{base_url}/v1'""",
    )
    httpx.post(f"{PYAPI}/llm/provider/reload", timeout=5)

    # ---- 基线：设计会话（paused@gate2）+ 真实 STEP + IR3 v1 ----
    # DO UPDATE：上一轮 teardown 中断时可能残留旧行（状态已被 gate2 批准推进），强制复位
    _pg_exec(
        f"""INSERT INTO design_sessions (id, project_id, pipeline_status, current_stage, title)
            SELECT '{SID}'::uuid, p.id, 'paused', 'gate2', 't87-微调e2e'
            FROM projects p LIMIT 1
            ON CONFLICT (id) DO UPDATE SET pipeline_status = 'paused', current_stage = 'gate2'""",
    )
    step_path = str(Path(tempfile.gettempdir()) / "t87_baseline.step")
    _build_step(step_path)
    step_ref = object_store.upload_bytes(Path(step_path).read_bytes(),
                                         BUCKET_STEP_MODELS, f"{SID}/sonar_model.step",
                                         content_type="application/step")
    from ir_contract import validate_ir

    ir_store.save_snapshot(SID, "IR3", validate_ir("IR3", {
        "cadquery_script": "# t87 baseline\nresult = cq.Workplane('XY').box(100, 20, 20)",
        "step_file": step_ref,
        "features": [],
        "geometry_summary": {"source": "extracted", "bbox_mm": [100.0, 20.0, 20.0],
                             "volume_mm3": 40000.0, "part_count": 1, "detail": "t87 e2e 基线"},
    }), created_by="t87")

    yield {"sid": SID, "base_ir_version": ir_store.get_latest_version(SID, "IR3")}

    # ---- 清理（traceability_chain/review_records/ir_snapshots 均无级联，按依赖逆序删）----
    _pg_exec(f"DELETE FROM traceability_chain WHERE session_id = '{SID}'")
    _pg_exec(f"DELETE FROM review_records WHERE session_id = '{SID}'")
    _pg_exec(f"DELETE FROM ir_snapshots WHERE session_id = '{SID}'")
    _pg_exec(f"DELETE FROM design_sessions WHERE id = '{SID}'")
    for bucket in ("step-models", "cad-tunes", "cad-scripts", "cad-previews"):
        try:
            cli = object_store._get_client()
            names = [o.object_name for o in cli.list_objects(bucket, prefix=f"{SID}/", recursive=True)]
            if names:
                for e in cli.remove_objects(bucket, names):
                    pass
        except Exception:
            pass
    _pg_exec(f"DELETE FROM llm_providers WHERE name = '{PROVIDER_NAME}'")
    try:
        httpx.post(f"{PYAPI}/llm/provider/reload", timeout=5)
    except Exception:
        pass
    proc.terminate()


def test_cad_tune_full_flow(env):
    from storage import ir_store

    sid = env["sid"]
    base_v = env["base_ir_version"]
    assert base_v >= 1
    token = _login()

    # ① 建微调会话
    r = httpx.post(f"{PYAPI}/api/v1/cad-agent/sessions/{sid}/tune", headers=_h(token), timeout=15)
    assert r.status_code == 200, r.text
    tune = r.json()
    tune_id = tune["id"]
    assert tune["status"] == "active" and tune["baseline_ir_version"] == base_v

    # ② 插件拉取模型上下文（预签名 URL 可下载真实 STEP）
    ctx = httpx.get(f"{PYAPI}/api/v1/cad-agent/tune/{tune_id}/context", headers=_h(token), timeout=15).json()
    assert ctx["version"] == base_v
    step_bytes = httpx.get(ctx["step_url"], timeout=60).content
    assert step_bytes.startswith(b"ISO-10303-21")

    # ③ 设计人员消息 → Runner 起轮
    r = httpx.post(f"{PYAPI}/api/v1/cad-agent/tune/{tune_id}/messages", headers=_h(token),
                   json={"text": "在底座上加一圈法兰环", "selection": {"kind": "Face", "area_mm2": 2000.0}},
                   timeout=15)
    assert r.status_code == 200, r.text
    round_no = r.json()["round_no"]

    # ④ 扮演插件：长轮询取工具调用 → 回传脚本化执行结果
    deadline = time.time() + 90
    calls = []
    while time.time() < deadline and not calls:
        resp = httpx.get(f"{PYAPI}/api/v1/cad-agent/tune/{tune_id}/tool-calls",
                         params={"cursor": 0, "wait": 10}, headers=_h(token), timeout=30).json()
        calls = resp.get("calls", [])
    assert calls, "90s 内未收到工具调用（AgentLoop/mock LLM 链路断）"
    call = calls[0]
    assert call["name"] == "execute_edit_code"
    assert "safe_fuse" in call["args"]["code"]

    r = httpx.post(f"{PYAPI}/api/v1/cad-agent/tune/{tune_id}/tool-calls/{call['call_id']}/result",
                   headers=_h(token),
                   json={"ok": True, "quality": {"status": "pass"}, "doc_summary": "bbox 100x60x30 (e2e fake plugin)"},
                   timeout=15)
    assert r.status_code == 200

    # ⑤ 轮次收敛 → awaiting_upload（mock LLM 第二轮返回收尾文本）
    rnd = _wait_round(token, tune_id, round_no, ("awaiting_upload", "done", "failed"))
    assert rnd["status"] == "awaiting_upload", f"期望编辑轮，实际 {rnd['status']}: {rnd.get('error')}"
    assert rnd["script"] and "safe_fuse" in rnd["script"]

    # ⑥ 上传真实微调 STEP（cadquery 生成：底座+圆环）→ IR3 v2 实测提取
    tuned_path = str(Path(tempfile.gettempdir()) / "t87_tuned.step")
    _build_step(tuned_path, outer_r=30.0)
    files = {"step_file": ("tuned.step", Path(tuned_path).read_bytes())}
    r = httpx.post(f"{PYAPI}/api/v1/cad-agent/tune/{tune_id}/versions", headers=_h(token),
                   data={"round_no": str(round_no)}, files=files, timeout=180)
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["ir_version"] == base_v + 1
    # 几何实测成功（真实 STEP，非降级）
    assert up["geometry_summary"]["source"] == "extracted", up["geometry_summary"]

    ir3 = ir_store.get_snapshot(sid, "IR3", up["ir_version"])
    assert ir3["step_file"]["bucket"] == "cad-tunes"
    assert "CAD 微调 r1" in ir3["cadquery_script"] and "t87 baseline" in ir3["cadquery_script"]
    assert len(ir3["features"]) >= 1, "真实 STEP 实测应提取出特征"
    # 轮次回填
    rnd = httpx.get(f"{PYAPI}/api/v1/cad-agent/tune/{tune_id}/rounds/{round_no}",
                    headers=_h(token), timeout=10).json()
    assert rnd["status"] == "done" and rnd["result_ir3_version"] == up["ir_version"]

    # ⑦ finalize → gate2 批准冻结（review 绑定微调版本快照，D8-3）
    r = httpx.post(f"{PYAPI}/api/v1/cad-agent/tune/{tune_id}/finalize", headers=_h(token),
                   json={"note": "e2e 定稿"}, timeout=15)
    assert r.status_code == 200 and r.json()["status"] == "finalized"

    latest_snapshot_id = ir_store.get_latest_snapshot_id(sid, "IR3")
    r = httpx.post(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/reviews/gate2", headers=_h(token),
                   json={"decision": "approved", "annotations": []}, timeout=15)
    assert r.status_code == 200, r.text
    reviews = httpx.get(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/reviews", headers=_h(token), timeout=15).json()["data"]
    gate2 = [x for x in reviews if x["gate"] == "gate2"]
    assert gate2 and gate2[-1]["ir_snapshot_id"] == latest_snapshot_id, \
        f"gate2 裁决应冻结微调版本快照 {latest_snapshot_id}，实际 {gate2[-1]['ir_snapshot_id']}"
