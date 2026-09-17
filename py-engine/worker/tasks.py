"""任务定义（T1.2 worker 骨架 / T1.3 起 A1–A3 固定管线任务注册处）。

- sonarcad.echo：T1.2 全链路验证载体（保留作联通性调试）。
- sonarcad.a1：A1 需求解析——读 IR0 快照 → A1Agent → IR1 快照（A-04/A-12）→ 会话 COMPLETE。
  （T1.4/T1.5 的 a2/a3 任务按同模式追加；固定管线由编排层驱动，A-01）

失败路径：回话 FAIL + 异常上抛（如实，A-16）；重试上限 A-13。
"""

from __future__ import annotations

import logging

import httpx
from celery import shared_task
from celery.exceptions import Reject

from app_config import SERVICE_AUTH_HEADERS, TASK_MAX_RETRIES, WORKFLOW_BASE_URL
from worker.celery_app import celery_app

logger = logging.getLogger("sonarcad.worker")


def _fire_session_event(session_id: str, event: str) -> None:
    """回调 workflow 推进会话状态（内网直连 + 服务信任头）。失败仅记录：状态可由重试/对账收敛。"""
    try:
        resp = httpx.post(
            f"{WORKFLOW_BASE_URL}/api/v1/workflow/sessions/{session_id}/events",
            headers=SERVICE_AUTH_HEADERS,
            json={"event": event},
            timeout=15,
        )
        logger.info("session %s event %s -> %s", session_id, event, resp.status_code)
    except Exception as exc:
        logger.error("session %s event %s 回调失败: %s", session_id, event, exc)


@shared_task(bind=True, name="sonarcad.echo", max_retries=TASK_MAX_RETRIES)
def echo_task(self, session_id: str, payload: str = "") -> dict:
    """骨架验证任务：原样回显 payload，成功驱动会话 COMPLETE，失败 FAIL。"""
    try:
        result = {"session_id": session_id, "echo": payload, "worker": self.request.hostname}
        _fire_session_event(session_id, "COMPLETE")
        return result
    except Exception as exc:
        logger.exception("echo 任务失败 session=%s", session_id)
        _fire_session_event(session_id, "FAIL")
        raise


@shared_task(bind=True, name="sonarcad.a1", max_retries=TASK_MAX_RETRIES)
def a1_task(self, session_id: str) -> dict:
    """A1 需求解析：IR0 → IR1。重试语义：LLM/检索瞬时故障可重试（上限 A-13）。"""
    try:
        from agents.a1_requirement.agent import A1Agent
        from ir_contract import IrContractError
        from knowledge.service import get_knowledge_service
        from llm.adapter import llm_adapter
        from storage import ir_store

        ir0 = ir_store.get_latest_snapshot(session_id, "IR0")
        if ir0 is None:
            raise RuntimeError(f"session {session_id} 缺少 IR0 快照，无法执行 A1")

        agent = A1Agent(llm=llm_adapter, kb_batch=get_knowledge_service().query_batch)
        try:
            ir1 = agent.run(ir0)
        except IrContractError as exc:
            # 契约失败不重试（重试也是同样输出）——如实失败
            raise RuntimeError(f"A1 输出未过 IR1 契约: {exc}") from exc

        version = ir_store.save_snapshot(session_id, "IR1", ir1, created_by="A1")
        ir_store.update_stage(session_id, "A1")
        _fire_session_event(session_id, "COMPLETE")
        return {"session_id": session_id, "ir": "IR1", "version": version}
    except Exception as exc:
        logger.exception("a1 任务失败 session=%s", session_id)
        _fire_session_event(session_id, "FAIL")
        raise


@shared_task(bind=True, name="sonarcad.always_fail", max_retries=0)
def always_fail_task(self, session_id: str = "") -> dict:
    """死信队列验证任务：必然失败、不重试，Reject(requeue=False) 进入 sonarcad.dlq（A-13 验收用）。

    业务任务的重试上限耗尽后也应走同样路径：进死信隔离，人工排查可重放，不静默丢弃。
    """
    _fire_session_event(session_id, "FAIL")
    raise Reject(requeue=False)
