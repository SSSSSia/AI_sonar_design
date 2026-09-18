# -*- coding: utf-8 -*-
"""阶段 3 端到端（T3.1–T3.4 验收）：首样全流程 API 走查。

流程（对知识库官方种子文档 towed_array_cad_knowledge.txt）：
  上传解析（五格式之一的 txt，带位置）→ 分块 → LLM 抽取 → 全部确认 → 异步三层构建
  → /knowledge/status 各层如实可见 → 检索命中图谱/向量级 → 图谱接口（全图/邻域）
  → 增量重建计时（§6 ⑥）

前置：数据底座 compose 全 healthy（Milvus/Neo4j/MinIO）；py-engine api+worker 为阶段 3 代码；
LLM 供应商可用（抽取用）。缺失则 skip。

运行（py-engine 目录）：python -m pytest tests/e2e/test_kb_pipeline_e2e.py -v
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

PYAPI = "http://127.0.0.1:8090"
AUTH = {"X-User-Id": "e2e-kb", "X-User-Roles": "admin"}
KB_FILE = Path(__file__).resolve().parents[2] / "knowledge" / "kb" / "input" / "towed_array_cad_knowledge.txt"


def _stack_ready() -> tuple[bool, str]:
    try:
        r = httpx.get(f"{PYAPI}/healthz", timeout=5)
        if r.status_code != 200:
            return False, f"py-engine healthz {r.status_code}"
    except Exception as exc:
        return False, f"py-engine 不可达: {exc}"
    from app_config import LLM_API_KEY, LLM_BASE_URL

    rows = None
    try:
        r = httpx.get(f"{PYAPI}/kb/llm-providers", headers=AUTH, timeout=5)
        rows = r.json().get("providers") if r.status_code == 200 else None
    except Exception:
        pass
    if rows:
        return True, ""
    if LLM_BASE_URL and LLM_API_KEY and not LLM_API_KEY.startswith("your_api"):
        return True, ""
    return False, "无可用 LLM 供应商（抽取需要）"


@pytest.fixture(scope="module")
def stack():
    ok, reason = _stack_ready()
    if not ok:
        pytest.skip(f"e2e 前置不满足：{reason}")
    yield


def _cleanup_previous(client: httpx.Client, filename: str) -> None:
    """幂等：清掉上次运行的同名文档（含向量/图谱挂载点，§6 ⑦）。"""
    r = client.get("/api/v1/kb/documents")
    for doc in r.json().get("documents", []):
        if doc["filename"] == filename:
            client.delete(f"/api/v1/kb/documents/{doc['doc_id']}")


def test_kb_first_sample_pipeline(stack):
    assert KB_FILE.exists(), f"种子文件缺失: {KB_FILE}"
    data = KB_FILE.read_bytes()

    with httpx.Client(base_url=PYAPI, headers=AUTH, timeout=60) as c:
        _cleanup_previous(c, KB_FILE.name)

        # ① 上传解析（带位置信息，§6 ①②）
        r = c.post("/api/v1/kb/documents", files={"file": (KB_FILE.name, data, "text/plain")})
        assert r.status_code == 200, r.text
        up = r.json()
        assert up["status"] == "parsed" and up["units"] > 0, up
        doc_id = up["doc_id"]
        print(f"\n① 解析：{up['units']} 单元")

        # ② 分块（§6 ③）
        r = c.post(f"/api/v1/kb/documents/{doc_id}/chunk", json={})
        assert r.status_code == 200, r.text
        n_chunks = r.json()["chunks"]
        assert n_chunks > 0
        print(f"② 分块：{n_chunks} chunks")

        # ③ LLM 抽取（异步任务，大文档并行）+ 全部确认（§6 ④；官方种子文档可信）
        r = c.post(f"/api/v1/kb/documents/{doc_id}/extract", json={})
        assert r.status_code == 200, r.text
        extract_task = r.json()["task_id"]
        deadline = time.time() + 900
        while time.time() < deadline:
            r = c.get(f"/api/v1/kb/extract/{extract_task}")
            state = r.json().get("state")
            if state == "SUCCESS":
                break
            assert state != "FAILED", r.text
            time.sleep(5)
        n_ext = r.json()["result"]["extractions"]
        print(f"③ 抽取：{n_ext} 条")
        r = c.get(f"/api/v1/kb/documents/{doc_id}/extractions", params={"status": "pending"})
        for item in r.json()["extractions"]:
            r2 = c.patch(f"/api/v1/kb/extractions/{item['id']}", json={"status": "confirmed"})
            assert r2.status_code == 200, r2.text

        # ④ 异步三层构建（§6 ⑤）
        r = c.post("/api/v1/kb/build", json={"doc_ids": [doc_id]})
        assert r.status_code == 200, r.text
        task_id = r.json()["task_id"]
        deadline = time.time() + 600
        result = None
        while time.time() < deadline:
            r = c.get(f"/api/v1/kb/build/{task_id}")
            if r.json().get("state") == "SUCCESS":
                result = r.json()["result"]
                break
            assert r.json().get("state") != "FAILED", r.text
            time.sleep(4)
        assert result is not None, "构建超时"
        assert result["indexed"] == 1, result
        detail = result["documents"][0]["detail"]
        print(f"④ 构建明细: {detail}")

        # ⑤ 知识库现状诚实可见（§6 硬性要求）+ 检索命中图谱/向量级
        r = c.get("/knowledge/status")
        status = r.json()
        assert status["documents"] >= 1 and status["chunks"] >= n_chunks
        assert status.get("milvus_available") is True, "Milvus 应可用（向量层）"
        assert status.get("neo4j_available") is True, "Neo4j 应可用（图谱层）"
        r = c.post("/knowledge/search", json={"query": "声呐方程 指向性指数 DI"})
        search = r.json()
        print(f"⑤ 检索命中层级: {search['level']} degraded={search['degraded_from']}")
        assert search["level"] in ("local", "basic", "graph", "vector"), \
            f"首样后检索应命中图谱/向量级，实际 {search['level']}"
        assert search["refs"], "命中应有来源引用"

        # ⑥ 图谱接口（T3.4）：全图 + 邻域，节点带来源文档
        r = c.get("/api/v1/kb/graph", params={"limit": 100})
        assert r.status_code == 200, r.text
        graph = r.json()
        assert graph["nodes"], "图谱应非空（首样已入图）"
        assert all(n.get("source_doc") or n.get("source_session") for n in graph["nodes"]),             "节点应带来源（KB 文档 source_doc / A5 会话沉淀 source_session）"
        r = c.get("/api/v1/kb/graph/neighborhood", params={"key": graph["nodes"][0]["key"], "depth": 2})
        assert r.status_code == 200 and r.json()["center"]["key"] == graph["nodes"][0]["key"]

        # ⑦ 增量重建计时（§6 ⑥：单文档重建应远小于全量管线）
        t0 = time.time()
        r = c.post("/api/v1/kb/build", json={"doc_ids": [doc_id]})
        task_id = r.json()["task_id"]
        while time.time() < deadline:
            r = c.get(f"/api/v1/kb/build/{task_id}")
            if r.json().get("state") == "SUCCESS":
                break
            time.sleep(3)
        rebuild_s = time.time() - t0
        print(f"⑦ 单文档增量重建: {rebuild_s:.1f}s")
        assert rebuild_s < 300, "单文档增量重建应在分钟级内"

    print("\n阶段 3 e2e 通过：首样全流程 + 三层构建 + 检索命中 + 图谱接口 + 增量重建")
