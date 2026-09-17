# -*- coding: utf-8 -*-
"""T1.7 端到端冒烟（宪法 §9.3）：自然语言需求 → MQ/状态机 → IR0–IR3 快照 + STEP 落 MinIO。

前置（由运行方保证，缺失则 skip 并给出原因）：
- 数据底座 compose 全部 healthy（PG/Redis/RabbitMQ/MinIO）
- Java 四服务已启动（gateway 8080 / auth 8081 / workflow 8082 / biz 8083）
- py-engine worker 与 api(8090) 已启动
- mock LLM 已注册为 llm_providers 中 enabled 且 priority 最高的一行
  （真实 key 就绪后删除该行即走真实供应商，本测试无需改动）

运行（py-engine 目录）：python -m pytest tests/e2e/test_towed_array_e2e.py -v
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

GATEWAY = "http://127.0.0.1:8080"
PYAPI = "http://127.0.0.1:8090"
PG_DSN = "postgresql://{user}:{pw}@{host}:{port}/{db}"

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
    # 表空时允许 .env 真实供应商兜底（D-12），占位符不算
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


def test_towed_array_full_chain(stack):
    token = _login()
    headers = {"Authorization": f"Bearer {token}"}
    run_tag = f"e2e-{uuid.uuid4().hex[:8]}"

    # 1. 建项目 + 带需求的会话（IR0 固化）
    r = httpx.post(f"{GATEWAY}/api/v1/projects", headers=headers,
                   json={"name": run_tag, "code": run_tag}, timeout=15)
    assert r.status_code == 200, r.text
    pid = r.json()["data"]["id"]

    r = httpx.post(
        f"{GATEWAY}/api/v1/workflow/sessions",
        headers=headers,
        json={"projectId": pid, "title": "拖曳线列阵标准样例", "requirement": STANDARD_REQUIREMENT},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    sid = r.json()["data"]["id"]

    # 2. START → 编排层驱动固定管线 A1→A2→A3
    r = httpx.post(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/events", headers=headers,
                   json={"event": "START"}, timeout=15)
    assert r.status_code == 200 and r.json()["data"]["pipelineStatus"] == "running", r.text

    # 3. 轮询到终态（上限 25 分钟：真实 LLM 时 A1–A3 共 4~8 次调用，公网延迟波动大）
    # 阶段 2 起管线带审阅关（A2 后 gate1、A4 后 gate2）：到达暂停即批准，门语义见 PHASE-002 §2.1
    deadline = time.time() + 1500

    def _state() -> dict:
        r = httpx.get(f"{GATEWAY}/api/v1/workflow/sessions/{sid}", headers=headers, timeout=10)
        return r.json()["data"]

    def _approve(gate: str) -> None:
        r = httpx.post(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/reviews/{gate}",
                       headers=headers, json={"decision": "approved",
                                              "annotations": ["T1.7 e2e 自动过闸"]},
                       timeout=15)
        assert r.status_code == 200, r.text

    final = None
    while time.time() < deadline:
        data = _state()
        status = data["pipelineStatus"]
        if status == "paused" and data.get("currentStage") in ("gate1", "gate2"):
            _approve(data["currentStage"])
            continue
        if status in ("completed", "failed"):
            final = status
            break
        time.sleep(6)
    assert final == "completed", f"会话未在时限内完成（final={final}）"

    # 4. IR0–IR3 快照齐全且 created_by 正确
    expected_creator = {"IR0": "user", "IR1": "A1", "IR2": "A2", "IR3": "A3"}
    contents = {}
    for ir_type, creator in expected_creator.items():
        r = httpx.get(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/irs/{ir_type}", headers=headers, timeout=10)
        assert r.status_code == 200, f"{ir_type} 快照缺失: {r.text}"
        data = r.json()["data"]
        assert data["created_by"] == creator, f"{ir_type} created_by={data['created_by']}"
        contents[ir_type] = data["content"] if isinstance(data["content"], dict) else json.loads(data["content"])

    # 5. IR2 契约核心断言：四件套齐全 + 公式参数数值可复算（A-06/A-07）
    for p in contents["IR2"]["parameters"]:
        assert p["trace_id"].startswith("tr-"), f"{p['id']} 缺 trace_id"
        assert p["source_type"] in ("formula", "case", "standard", "estimated")
        assert str(p["source_ref"]).strip() and str(p["derivation"]).strip()
    formulas = [p for p in contents["IR2"]["parameters"] if p["source_type"] == "formula"]
    assert formulas, "IR2 缺少公式类参数（A-07：公式数值必须由代码计算）"
    import math
    import re

    di = next((p for p in formulas if "指向性" in p["name"]), None)
    assert di is not None, f"缺少指向性指数公式参数，现有: {[p['name'] for p in formulas]}"
    m = re.search(r"n=([\d.]+)", di["derivation"])
    assert m, "DI derivation 未记录输入 n"
    assert abs(di["value"] - 10 * math.log10(float(m.group(1)))) < 1e-3, "公式参数数值必须可由 derivation 复算"

    # 6. IR3 + STEP 产物在 MinIO（D-10 分桶）真实可读
    step_ref = contents["IR3"]["step_file"]
    assert step_ref["bucket"] and step_ref["object_key"]
    from minio import Minio
    from app_config import MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY

    client = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=False)
    stat = client.stat_object(step_ref["bucket"], step_ref["object_key"])
    assert stat.size > 1000, f"STEP 过小: {stat.size}"

    # STEP 可被 OCC 重新读入（几何有效性抽查）
    tmp = Path("tests/e2e/_verify.step")
    client.fget_object(step_ref["bucket"], step_ref["object_key"], str(tmp))
    import cadquery as cq

    shape = cq.importers.importStep(str(tmp))
    assert shape.val().Volume() > 0
    tmp.unlink()

    # 7. 会话 stage=A5（管线走满五棒：A3 之后 A4 校验、gate2 批准、A5 沉淀）
    r = httpx.get(f"{GATEWAY}/api/v1/workflow/sessions/{sid}", headers=headers, timeout=10)
    assert r.json()["data"]["currentStage"] == "A5"

    print(f"\ne2e 通过：session={sid} run={run_tag} step={step_ref}")
