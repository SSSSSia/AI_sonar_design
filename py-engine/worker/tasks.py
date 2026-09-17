"""任务定义（T1.2）。

echo 任务是 T1.2 的全链路验证载体：workflow 在会话 START 时投递 → worker 消费 →
回调 workflow 推进会话状态（COMPLETE/FAIL 由真实任务驱动，而非人工 POST）。
T1.3 起 A1–A3 任务在本文件按同模式注册（固定管线，A-01）。
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
    """骨架验证任务：原样回显 payload，成功驱动会话 COMPLETE，失败 FAIL。

    顶层异常不重试（echo 无业务可重试语义），直接 FAIL——重试语义随 T1.3+ 各任务
    按需定义，但必须有上限（A-13）。
    """
    try:
        result = {"session_id": session_id, "echo": payload, "worker": self.request.hostname}
        _fire_session_event(session_id, "COMPLETE")
        return result
    except Exception as exc:
        logger.exception("echo 任务失败 session=%s", session_id)
        _fire_session_event(session_id, "FAIL")
        raise


@shared_task(bind=True, name="sonarcad.always_fail", max_retries=0)
def always_fail_task(self, session_id: str = "") -> dict:
    """死信队列验证任务：必然失败、不重试，Reject(requeue=False) 进入 sonarcad.dlq（A-13 验收用）。

    业务任务的重试上限耗尽后也应走同样路径：进死信隔离，人工排查可重放，不静默丢弃。
    """
    _fire_session_event(session_id, "FAIL")
    raise Reject(requeue=False)
