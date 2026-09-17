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
from llm.adapter import active_provider, llm_adapter, reload_providers
from worker.celery_app import celery_app
from worker.tasks import always_fail_task, echo_task

app = FastAPI(
    title="Sonar-CAD v2 算力层",
    version="0.2.0",
    description="A1–A5 Agent / CAD 执行 / 约束求解 / 检索（T1.2：worker 骨架 + LLM 适配器）",
)


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
    """知识库现状诚实上报：SDK 可用性 / 语料规模。"""
    from knowledge.service import get_knowledge_service

    svc = get_knowledge_service()
    return {
        "kb_dir": str(svc.kb_dir),
        "graphrag_sdk": svc.sdk is not None,
        "text_corpus_chars": len(svc.kb_text or ""),
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
