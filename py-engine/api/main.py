"""FastAPI 应用入口（T1.2 扩展）。

内部接口（仅网关/本机可达，不对外）：
- GET  /healthz            存活探针（含 broker/backend 连通性）
- GET  /tasks/{task_id}    Celery 任务状态查询（供 workflow 轮询，契约 §5.4 GET /api/v1/tasks/{id} 的算力侧实现）
- POST /debug/echo         手动入队 echo 任务（联调用；正常流由 workflow START 事件投递）
- GET  /llm/provider       当前生效 LLM 供应商（脱敏输出，宪法 §10.2）
- POST /llm/provider/reload 强制重载供应商配置（热切换验证）
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app_config import CELERY_BROKER_URL, CELERY_RESULT_BACKEND, KNOWLEDGE_KB_DIR, WORKFLOW_BASE_URL
from api.kb import router as kb_router
from api.cad import router as cad_router
from api.cad_agent import router as cad_agent_router
from llm.adapter import active_provider, llm_adapter, reload_providers
from worker.celery_app import celery_app
from worker.tasks import always_fail_task, echo_task

app = FastAPI(
    title="Sonar-CAD v2 算力层",
    version="0.3.0",
    description="A1–A5 Agent / CAD 执行 / 约束求解 / 检索 / 知识治理（T1.2 骨架 → T3 治理 → T8 交互微调）",
)
app.include_router(kb_router, prefix="/api/v1")
app.include_router(cad_router, prefix="/api/v1")  # gateway /api/v1/kb/** 直转（T3.1）
app.include_router(cad_agent_router, prefix="/api/v1")  # gateway /api/v1/cad-agent/**（T8.3 微调）


def _mask(url: str) -> str:
    """连接串中的密码脱敏。"""
    if "://" in url and "@" in url:
        scheme, rest = url.split("://", 1)
        if ":" in rest.split("@", 1)[0]:
            creds, host = rest.rsplit("@", 1)
            user = creds.split(":", 1)[0]
            return f"{scheme}://{user}:****@{host}"
    return url


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    health: dict[str, Any] = {"status": "ok", "service": "py-engine", "version": "0.2.0"}
    # 依赖连通性如实上报（A-16：不夸大）
    try:
        conn = celery_app.connection()
        conn.ensure_connection(max_retries=1, timeout=3)
        conn.close()
        health["broker"] = "ok"
    except Exception as exc:
        health["status"] = "degraded"
        health["broker"] = f"unreachable: {exc}"
    health["broker_url"] = _mask(CELERY_BROKER_URL)
    health["result_backend"] = _mask(CELERY_RESULT_BACKEND)
    health["workflow_base_url"] = WORKFLOW_BASE_URL
    return health


@app.get("/tasks/{task_id}")
def task_status(task_id: str) -> dict[str, Any]:
    result = celery_app.AsyncResult(task_id)
    payload: dict[str, Any] = {"task_id": task_id, "state": result.state}
    if result.successful():
        payload["result"] = result.result
    elif result.failed():
        payload["error"] = str(result.result)
    return payload


class EchoRequest(BaseModel):
    session_id: str
    payload: str = ""


@app.post("/debug/echo")
def debug_echo(req: EchoRequest) -> dict[str, Any]:
    task = echo_task.delay(req.session_id, req.payload)
    return {"task_id": task.id, "state": task.state}


class FailRequest(BaseModel):
    session_id: str = ""


@app.post("/debug/always-fail")
def debug_always_fail(req: FailRequest) -> dict[str, Any]:
    """DLQ 验收入口：必然失败进死信队列。"""
    task = always_fail_task.delay(req.session_id)
    return {"task_id": task.id, "state": task.state}


@app.get("/llm/provider")
def llm_provider() -> dict[str, Any]:
    try:
        return active_provider().redacted()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/llm/provider/reload")
def llm_provider_reload() -> dict[str, str]:
    reload_providers()
    return {"status": "reloaded"}


class SearchRequest(BaseModel):
    query: str


@app.post("/knowledge/search")
def knowledge_search(req: SearchRequest) -> dict[str, Any]:
    """检索调试入口（T1.6）：返回 content + 命中层级 + 降级轨迹（A-14/A-16 如实交代）。"""
    from knowledge.service import get_knowledge_service

    return get_knowledge_service().query(req.query).to_dict()


@app.get("/knowledge/status")
def knowledge_status() -> dict[str, Any]:
    """知识库现状诚实上报（§6 硬性要求）：SDK/扩展层可用性 + 语料规模 + KB 管线统计。"""
    from knowledge.service import get_knowledge_service
    from storage import kb_store

    svc = get_knowledge_service()
    status: dict[str, Any] = {
        "kb_dir": str(svc.kb_dir),
        "graphrag_sdk": svc.sdk is not None,
        "text_corpus_chars": len(svc.kb_text or ""),
        "kb_levels_enabled": svc.enable_kb_levels,
        "dynamic_corpus_chunks": len(svc._dynamic_sources),
    }
    status.update(kb_store.doc_stats())
    if svc.enable_kb_levels:
        from kb.vector_store import ChunkVectorStore

        status["milvus_available"] = ChunkVectorStore().available()
        try:
            from storage.neo4j_store import Neo4jStore

            status["neo4j_available"] = Neo4jStore().available()
        except Exception:
            status["neo4j_available"] = False
    return status


class WritebackRequest(BaseModel):
    """知识审阅关确认后的回写请求（T2.3，A-10：只有确认名单内的候选会入图）。"""

    session_id: str
    candidate_indexes: list[int] | None = None  # 缺省=全部 dedup_status=new 的候选
    kb_id: str | None = None  # PHASE-006 D6-8：目标库（前端弹窗手选；缺省 = 会话首选库/默认库）


@app.post("/knowledge/writeback")
def knowledge_writeback(req: WritebackRequest) -> dict[str, Any]:
    """人工确认后回写 Neo4j：选定候选 → 图谱 MERGE（旧同名实体标 deprecated，节点带库归属 kb_id）→ IR5 v2 written。

    Neo4j 不可达/写入失败：503 如实上报，IR5 保持 pending 可重试（A-16 不伪造成功）。
    """
    from ir_contract import IrContractError
    from storage import ir_store, kb_store, session_meta
    from storage.neo4j_store import Neo4jStore

    ir5 = ir_store.get_latest_snapshot(req.session_id, "IR5")
    if ir5 is None:
        raise HTTPException(status_code=404, detail=f"session {req.session_id} 无 IR5 快照")
    if ir5.get("writeback_status") != "pending":
        raise HTTPException(status_code=409, detail="IR5 非 pending 状态，无可回写候选")

    # D6-8 目标库解析：手选优先（校验存在）→ 会话首选库 → 默认库；响应回带实际归属（链路留痕）
    kb_id: str | None = None
    if req.kb_id:
        if kb_store.get_base(req.kb_id) is None:
            raise HTTPException(status_code=404, detail=f"目标知识库不存在: {req.kb_id}")
        kb_id = req.kb_id
    else:
        kb_id = (session_meta.get_kb_ids(req.session_id) or [None])[0]
        if not kb_id:
            default = next((b for b in kb_store.list_bases() if b["is_default"]), None)
            kb_id = str(default["id"]) if default else None

    candidates = ir5.get("candidates", [])
    if req.candidate_indexes is None:
        selected = [c for c in candidates if c.get("dedup_status") == "new"]
    else:
        if any(i < 0 or i >= len(candidates) for i in req.candidate_indexes):
            raise HTTPException(
                status_code=400,
                detail=f"candidate_indexes 越界（共 {len(candidates)} 条候选）",
            )
        selected = [candidates[i] for i in req.candidate_indexes]
    if not selected:
        raise HTTPException(status_code=400, detail="选定候选为空，无内容可回写")

    store = Neo4jStore()
    if not store.available():
        raise HTTPException(status_code=503, detail="Neo4j 不可达，回写未执行（IR5 保持 pending）")
    try:
        stats = store.write_candidates(selected, req.session_id, kb_id=kb_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Neo4j 回写失败（IR5 保持 pending）: {exc}") from exc

    written_ir5 = {"candidates": selected, "writeback_status": "written"}
    try:
        version = ir_store.save_snapshot(req.session_id, "IR5", written_ir5, created_by="A5/writeback")
    except IrContractError as exc:
        raise HTTPException(status_code=500, detail=f"IR5 v2 未过契约: {exc}") from exc
    return {
        "session_id": req.session_id,
        "written": stats,
        "ir_version": version,
        "kb_id": kb_id,
        "selected_indexes": (
            req.candidate_indexes
            if req.candidate_indexes is not None
            else [i for i, c in enumerate(candidates) if c.get("dedup_status") == "new"]
        ),
    }


class ChatRequest(BaseModel):
    messages: list[dict[str, str]]


@app.post("/llm/chat")
def llm_chat(req: ChatRequest) -> dict[str, Any]:
    """LLM 适配器直连调试入口（真实 Key 不出现在任何日志/响应中）。"""
    try:
        content = llm_adapter.chat(req.messages)
        return {"content": content, "provider": active_provider().redacted()}
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
