# -*- coding: utf-8 -*-
"""PHASE-006 多库 e2e：建双库（含共享文档）→ 定向/Agent 两模式检索 → 归属变更生效 → 回写归属。

前置与主链路 e2e 相同（数据底座 + Java 四服务 + py-engine api/worker + 可用 LLM 供应商），
不满足即 skip。运行（py-engine 目录）：python -m pytest tests/e2e/test_multikb_e2e.py -v
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

GATEWAY = "http://127.0.0.1:8080"
PYAPI = "http://127.0.0.1:8090"

DOC_A = "拖曳线列阵声呐由水听器基阵、前置放大模块与定向悬挂段组成，总体设计需先确定阵长与阵元数。"
DOC_B = "舷侧阵声呐的波束成形与基阵孔径设计要点：孔径决定低频指向性，波束宽度与阵列长度成反比。"
REQUIREMENT = "设计一个拖曳线列阵声呐，16 个水听器阵元，阵元间距 1.5 米，最大工作深度 300 米。"


def _pg_exec(sql: str, fetch: bool = False):
    import psycopg

    from app_config import DATABASE_URL

    with psycopg.connect(DATABASE_URL.replace("+psycopg", "")) as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall() if fetch else None
        conn.commit()
    return rows


def _stack_ready() -> tuple[bool, str]:
    for name, fn in (("gateway", lambda: httpx.post(f"{GATEWAY}/api/v1/auth/login", json={}, timeout=5)),
                     ("py-engine", lambda: httpx.get(f"{PYAPI}/healthz", timeout=5))):
        try:
            fn()
        except Exception as exc:
            return False, f"{name} 不可达: {exc}"
    rows = _pg_exec("SELECT count(*) FROM llm_providers WHERE enabled", fetch=True)
    if rows and rows[0][0] > 0:
        return True, ""
    return False, "无可用 LLM 供应商"


@pytest.fixture(scope="module")
def stack():
    ok, reason = _stack_ready()
    if not ok:
        pytest.skip(f"e2e 前置不满足：{reason}")
    yield


def _login() -> str:
    r = httpx.post(f"{GATEWAY}/api/v1/auth/login",
                   json={"username": "admin", "password": "admin123"}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["data"]["token"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _upload(token: str, filename: str, text: str, base_id: str | None) -> str:
    r = httpx.post(f"{GATEWAY}/api/v1/kb/documents",
                   headers=_h(token),
                   files={"file": (filename, text.encode("utf-8"), "text/plain")},
                   params={"base_id": base_id} if base_id else None,
                   timeout=120)
    assert r.status_code == 200, r.text
    return r.json()["doc_id"]


def _wait_indexed(token: str, doc_ids: list[str], timeout_s: int = 300) -> None:
    t0 = time.time()
    remaining = set(doc_ids)
    while remaining and time.time() - t0 < timeout_s:
        r = httpx.get(f"{GATEWAY}/api/v1/kb/documents", headers=_h(token), timeout=15)
        assert r.status_code == 200, r.text
        for d in r.json()["documents"]:
            if d["doc_id"] in remaining:
                if d["status"] == "indexed":
                    remaining.discard(d["doc_id"])
                elif d["status"] == "failed":
                    pytest.fail(f"文档 {d['doc_id']} 构建失败: {d.get('error')}")
        if remaining:
            time.sleep(5)
    assert not remaining, f"超时未入索引: {remaining}"


def test_multikb_flow(stack):
    token = _login()
    headers = _h(token)
    suffix = uuid.uuid4().hex[:6]
    base_a = base_b = None
    doc_a = doc_shared = session_sid = None
    try:
        # ---- 1. 建双库 ----
        for name in (f"e2e-拖曳库-{suffix}", f"e2e-舷侧库-{suffix}"):
            r = httpx.post(f"{GATEWAY}/api/v1/kb/bases", headers=headers,
                           json={"name": name, "description": "multikb e2e"}, timeout=30)
            assert r.status_code == 200, r.text
        bases = httpx.get(f"{GATEWAY}/api/v1/kb/bases", headers=headers, timeout=15).json()["bases"]
        base_a = next(b["id"] for b in bases if b["name"] == f"e2e-拖曳库-{suffix}")
        base_b = next(b["id"] for b in bases if b["name"] == f"e2e-舷侧库-{suffix}")

        # ---- 2. 各传一份文档 + 共享文档归两库 ----
        doc_a = _upload(token, f"towed-{suffix}.txt", DOC_A, base_a)
        doc_shared = _upload(token, f"shared-{suffix}.txt", DOC_A, base_a)
        r = httpx.post(f"{GATEWAY}/api/v1/kb/bases/{base_b}/docs", headers=headers,
                       json={"doc_ids": [doc_shared]}, timeout=30)
        assert r.status_code == 200 and r.json()["added"] == 1, r.text

        # ---- 3. 分块 + 构建 + 等待 indexed ----
        for did in (doc_a, doc_shared):
            r = httpx.post(f"{GATEWAY}/api/v1/kb/documents/{did}/chunk", headers=headers, json={}, timeout=60)
            assert r.status_code == 200, r.text
        r = httpx.post(f"{GATEWAY}/api/v1/kb/build", headers=headers,
                       json={"doc_ids": [doc_a, doc_shared]}, timeout=30)
        assert r.status_code == 200, r.text
        _wait_indexed(token, [doc_a, doc_shared])

        # ---- 4. 会话选库 A（manual）：IR1 引用必须全部归属库 A ----
        project = httpx.get(f"{GATEWAY}/api/v1/projects?page=1&size=1", headers=headers, timeout=15).json()["data"]["records"][0]
        r = httpx.post(f"{GATEWAY}/api/v1/workflow/sessions", headers=headers, json={
            "projectId": project["id"], "title": f"multikb-manual-{suffix}",
            "requirement": REQUIREMENT, "kbIds": [base_a],
        }, timeout=30)
        assert r.status_code == 200, r.text
        session_sid = r.json()["data"]["id"]
        assert r.json()["data"].get("kbNames") == ["e2e-拖曳库-" + suffix]
        r = httpx.post(f"{GATEWAY}/api/v1/workflow/sessions/{session_sid}/events",
                       headers=headers, json={"event": "START"}, timeout=30)
        assert r.status_code == 200, r.text

        # 等 IR1（A1 完成 = STAGE_DONE 后进入 A2；快照出现即校验）
        ir1 = None
        t0 = time.time()
        while time.time() - t0 < 600:
            rr = httpx.get(f"{GATEWAY}/api/v1/workflow/sessions/{session_sid}/irs/IR1",
                           headers=headers, timeout=15)
            if rr.status_code == 200 and rr.json().get("data"):
                env = rr.json()["data"]
                content = env.get("content")
                ir1 = content if isinstance(content, dict) else __import__("json").loads(content or "null")
                if ir1 and ir1.get("retrieved_context") is not None:
                    break
            time.sleep(6)
        assert ir1, "600s 内未生成 IR1 快照"
        assert ir1.get("kb_selection", {}).get("mode") == "manual"
        sel_ids = [b["kb_id"] for b in ir1["kb_selection"]["bases"] if b["selected"]]
        assert base_a in sel_ids and base_b not in sel_ids
        kb_ref_ids = {c.get("kb_id") for c in ir1["retrieved_context"] if c.get("kb_id")}
        assert all(k in (base_a, "") for k in kb_ref_ids), f"引用出现非选定库: {kb_ref_ids}"

        # ---- 5. 删库 A → 共享文档仍有归属（库 B），doc_a 转未归库 ----
        r = httpx.delete(f"{GATEWAY}/api/v1/kb/bases/{base_a}", headers=headers, timeout=30)
        assert r.status_code == 200 and r.json()["deleted"], r.text
        base_a = None  # 已删
        rows = _pg_exec(
            f"SELECT count(DISTINCT base_id) FROM kb_base_documents WHERE doc_id = '{doc_shared}'",
            fetch=True,
        )
        assert rows[0][0] == 1, "共享文档应仍归属库 B"
        r = httpx.get(f"{GATEWAY}/api/v1/kb/documents?unassigned=true", headers=headers, timeout=15)
        unassigned_ids = {d["doc_id"] for d in r.json()["documents"]}
        assert doc_a in unassigned_ids, "库 A 专属文档应转未归库"
        assert doc_shared not in unassigned_ids, "共享文档不应进未归库"

        # ---- 6. 清理会话（终止流程即可，不必跑完全管线） ----
        httpx.post(f"{GATEWAY}/api/v1/workflow/sessions/{session_sid}/events",
                   headers=headers, json={"event": "FAIL"}, timeout=30)
        httpx.delete(f"{GATEWAY}/api/v1/workflow/sessions/{session_sid}?force=true",
                     headers=headers, timeout=120)
        session_sid = None

        print(f"\n[multikb e2e] 双库/共享文档/定向检索留痕/删库转未归库 全部通过（suffix={suffix}）")
    finally:
        # 环境还原：删测试库、测试文档、测试会话
        for bid in (base_a, base_b):
            if bid:
                httpx.delete(f"{GATEWAY}/api/v1/kb/bases/{bid}", headers=headers, timeout=30)
        for did in (doc_a, doc_shared):
            if did:
                httpx.delete(f"{GATEWAY}/api/v1/kb/documents/{did}", headers=headers, timeout=60)
        if session_sid:
            httpx.delete(f"{GATEWAY}/api/v1/workflow/sessions/{session_sid}?force=true",
                         headers=headers, timeout=120)
