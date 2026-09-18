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


def _record_trace(session_id: str, steps_builder) -> None:
    """溯源链写入（T2.4）降级策略：IR 快照（含四件套 trace_id）是权威出处，快照落库成功后
    链路表写入失败只记日志、不拖垮管线——trace API 无行即 404 如实，链路可由快照重建。
    """
    try:
        from storage import trace_store

        trace_store.record_steps(steps_builder())
    except Exception:
        logger.exception("session %s 溯源链写入失败（快照为权威出处，链路降级）", session_id)


def _notify_ir_updated(session_id: str, ir_type: str, version: int) -> None:
    """T4.1：快照落库后向 notify 发布 ir_updated（WS 推送为增强，失败仅记日志不拖垮管线）。"""
    try:
        from datetime import datetime, timezone

        from app_config import NOTIFY_BASE_URL

        httpx.post(
            f"{NOTIFY_BASE_URL}/internal/sessions/{session_id}/messages",
            headers=SERVICE_AUTH_HEADERS,
            json={
                "type": "ir_updated",
                "session_id": session_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"ir_type": ir_type, "ir_version": str(version)},
            },
            timeout=3,
        )
    except Exception as exc:
        logger.warning("session %s ir_updated 发布失败（不影响管线）: %s", session_id, exc)


def _a2_trace(ir2: dict, session_id: str) -> list[dict]:
    from storage import ir_store, trace_steps

    return trace_steps.build_a2_steps(ir2, ir_store.get_latest_snapshot_id(session_id, "IR2"), session_id)


def _a3_trace(ir3: dict, ir2: dict | None, session_id: str) -> list[dict]:
    from storage import ir_store, trace_steps

    return trace_steps.build_a3_steps(ir3, ir2, ir_store.get_latest_snapshot_id(session_id, "IR3"), session_id)


def _a4_trace(ir4: dict, ir2: dict | None, session_id: str) -> list[dict]:
    from storage import ir_store, trace_steps

    return trace_steps.build_a4_steps(ir4, ir2, ir_store.get_latest_snapshot_id(session_id, "IR4"), session_id)


def _a5_trace(ir5: dict, session_id: str) -> list[dict]:
    from storage import ir_store, trace_steps

    return trace_steps.build_a5_steps(ir5, ir_store.get_latest_snapshot_id(session_id, "IR5"), session_id)


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
        _notify_ir_updated(session_id, "IR1", version)
        _fire_session_event(session_id, "STAGE_DONE")
        return {"session_id": session_id, "ir": "IR1", "version": version}
    except Exception as exc:
        logger.exception("a1 任务失败 session=%s", session_id)
        _fire_session_event(session_id, "FAIL")
        raise


@shared_task(bind=True, name="sonarcad.a2", max_retries=TASK_MAX_RETRIES)
def a2_task(self, session_id: str) -> dict:
    """A2 方案生成：IR1 → IR2。公式参数由 solver 计算（A-07），LLM 仅文档组织。"""
    try:
        from agents.a2_scheme.agent import A2Agent
        from ir_contract import IrContractError
        from llm.adapter import llm_adapter
        from storage import ir_store

        ir1 = ir_store.get_latest_snapshot(session_id, "IR1")
        if ir1 is None:
            raise RuntimeError(f"session {session_id} 缺少 IR1 快照，无法执行 A2")

        ir2 = A2Agent(llm=llm_adapter).run(ir1)
        # A-06 补强断言：契约校验外再确认四件套非空（防御未来 schema 放松）
        for p in ir2["parameters"]:
            if not all(str(p.get(k, "")).strip() for k in ("source_type", "source_ref", "derivation", "trace_id")):
                raise RuntimeError(f"参数 {p.get('id')} 四件套不完整")

        version = ir_store.save_snapshot(session_id, "IR2", ir2, created_by="A2")
        _record_trace(session_id, lambda: _a2_trace(ir2, session_id))
        ir_store.update_stage(session_id, "A2")
        _notify_ir_updated(session_id, "IR2", version)
        _fire_session_event(session_id, "STAGE_DONE")
        return {"session_id": session_id, "ir": "IR2", "version": version}
    except Exception as exc:
        logger.exception("a2 任务失败 session=%s", session_id)
        _fire_session_event(session_id, "FAIL")
        raise


@shared_task(bind=True, name="sonarcad.a3", max_retries=0)
def a3_task(self, session_id: str) -> dict:
    """A3 CAD 建模：IR2 → IR3（沙箱出 STEP 落 MinIO）。

    重试为 0：修复循环已在任务内部有界收敛（A-13），任务级重试只会重复同样的 LLM 输出；
    失败如实 FAIL（审阅关驳回回环由 workflow 裁决驱动，T2.2）。
    阶段 2 编排：完成后发 STAGE_DONE，由 workflow 派发 A4（A3 不再直接终止管线）。
    """
    try:
        from agents.a3_cad.agent import A3Agent
        from app_config import CAD_EXECUTE_TIMEOUT_S, CAD_FIX_MAX_ROUNDS
        from llm.adapter import llm_adapter
        from storage import ir_store

        ir2 = ir_store.get_latest_snapshot(session_id, "IR2")
        if ir2 is None:
            raise RuntimeError(f"session {session_id} 缺少 IR2 快照，无法执行 A3")

        ir3 = A3Agent(
            llm=llm_adapter,
            fix_max_rounds=CAD_FIX_MAX_ROUNDS,
            execute_timeout_s=CAD_EXECUTE_TIMEOUT_S,
        ).run(session_id, ir2)

        version = ir_store.save_snapshot(session_id, "IR3", ir3, created_by="A3")
        _record_trace(session_id, lambda: _a3_trace(ir3, ir2, session_id))
        ir_store.update_stage(session_id, "A3")
        _notify_ir_updated(session_id, "IR3", version)
        _fire_session_event(session_id, "STAGE_DONE")
        return {"session_id": session_id, "ir": "IR3", "version": version}
    except Exception as exc:
        logger.exception("a3 任务失败 session=%s", session_id)
        _fire_session_event(session_id, "FAIL")
        raise


@shared_task(bind=True, name="sonarcad.a4", max_retries=TASK_MAX_RETRIES)
def a4_task(self, session_id: str) -> dict:
    """A4 合规校验：IR2 + IR3 → IR4（纯确定性，无 LLM；A-09 只出报告不改参数）。

    编排接线（T2.2）：A4 完成由 workflow 在 STAGE_DONE 分支暂停 gate2 人工裁决。
    """
    try:
        from agents.a4_validation.agent import A4Agent
        from ir_contract import IrContractError
        from storage import ir_store

        ir2 = ir_store.get_latest_snapshot(session_id, "IR2")
        ir3 = ir_store.get_latest_snapshot(session_id, "IR3")
        if ir2 is None:
            raise RuntimeError(f"session {session_id} 缺少 IR2 快照，无法执行 A4")
        if ir3 is None:
            raise RuntimeError(f"session {session_id} 缺少 IR3 快照，无法执行 A4")

        try:
            ir4 = A4Agent().run(ir2, ir3)
        except IrContractError as exc:
            raise RuntimeError(f"A4 输出未过 IR4 契约: {exc}") from exc

        version = ir_store.save_snapshot(session_id, "IR4", ir4, created_by="A4")
        _record_trace(session_id, lambda: _a4_trace(ir4, ir2, session_id))
        ir_store.update_stage(session_id, "A4")
        _notify_ir_updated(session_id, "IR4", version)
        _fire_session_event(session_id, "STAGE_DONE")
        return {"session_id": session_id, "ir": "IR4", "version": version}
    except Exception as exc:
        logger.exception("a4 任务失败 session=%s", session_id)
        _fire_session_event(session_id, "FAIL")
        raise


@shared_task(bind=True, name="sonarcad.a5", max_retries=0)
def a5_task(self, session_id: str) -> dict:
    """A5 知识沉淀：IR1 + IR2 + IR4 → IR5（pending 候选，A-10 绝不直接写图谱）。

    链尾任务：完成后发 COMPLETE 终止管线（gate2 批准后由 workflow 派发，T2.2）。
    知识回写走 /knowledge/writeback（人工确认后），不在本任务内。
    重试为 0：候选生成失败多为输入快照缺失，重试无意义。
    """
    try:
        from agents.a5_knowledge.agent import A5Agent
        from ir_contract import IrContractError
        from knowledge.service import get_knowledge_service
        from llm.adapter import llm_adapter
        from storage import ir_store
        from storage.neo4j_store import Neo4jStore

        ir1 = ir_store.get_latest_snapshot(session_id, "IR1")
        ir2 = ir_store.get_latest_snapshot(session_id, "IR2")
        ir4 = ir_store.get_latest_snapshot(session_id, "IR4")
        if ir2 is None:
            raise RuntimeError(f"session {session_id} 缺少 IR2 快照，无法执行 A5")
        if ir4 is None:
            raise RuntimeError(f"session {session_id} 缺少 IR4 快照，无法执行 A5")

        graph = Neo4jStore()
        if not graph.available():
            logger.warning("A5：Neo4j 不可达，知识去重仅 KB 文本级（如实降级）")
            graph = None

        try:
            ir5 = A5Agent(
                llm=llm_adapter, kb=get_knowledge_service(), graph=graph,
            ).run(ir1, ir2, ir4)
        except IrContractError as exc:
            raise RuntimeError(f"A5 输出未过 IR5 契约: {exc}") from exc

        version = ir_store.save_snapshot(session_id, "IR5", ir5, created_by="A5")
        _record_trace(session_id, lambda: _a5_trace(ir5, session_id))
        ir_store.update_stage(session_id, "A5")
        _notify_ir_updated(session_id, "IR5", version)
        _fire_session_event(session_id, "COMPLETE")
        return {
            "session_id": session_id, "ir": "IR5", "version": version,
            "candidates": len(ir5["candidates"]),
        }
    except Exception as exc:
        logger.exception("a5 任务失败 session=%s", session_id)
        _fire_session_event(session_id, "FAIL")
        raise


@shared_task(bind=True, name="sonarcad.kb_extract", max_retries=0)
def kb_extract_task(self, doc_id: str, entity_types: list[str] | None = None) -> dict:
    """KB 本体抽取异步任务（T3.2，§6 ④）：大文档逐 chunk 并行 LLM 抽取。

    重试为 0：单 chunk 失败已在任务内如实跳过，整体重试只会重复同样输出；
    结果（pending 状态）落 kb_extractions，人工确认后才入图。
    """
    from kb.extractor import extract_from_chunks
    from llm.adapter import llm_adapter
    from storage import kb_store

    doc = kb_store.get_document(doc_id)
    if doc is None:
        raise RuntimeError(f"KB 文档不存在: {doc_id}")
    chunks = kb_store.get_chunks(doc_id)
    if not chunks:
        raise RuntimeError(f"KB 文档无 chunks: {doc_id}")

    import app_config

    items = extract_from_chunks(
        chunks, llm_adapter, entity_types, max_workers=app_config.KB_EXTRACT_WORKERS,
    )
    n = kb_store.replace_extractions(doc_id, items)
    logger.info("kb_extract 完成 doc=%s: %d 条抽取（pending）", doc_id, n)
    return {"doc_id": doc_id, "extractions": n}


@shared_task(bind=True, name="sonarcad.kb_build", max_retries=TASK_MAX_RETRIES)
def kb_build_task(self, doc_ids: list[str] | None = None) -> dict:
    """KB 索引异步构建（T3.2，§6 ⑤）：向量层/图谱层/语料层逐层如实构建。

    增量语义：doc_ids 缺省只处理未 indexed 文档（§6 ⑥）。
    与设计会话解耦——不发会话事件；进度经任务状态 + kb_documents.status/build_detail 可查。
    """
    from kb import index_build

    result = index_build.build_documents(doc_ids)
    logger.info("kb_build 完成: total=%s indexed=%s", result["total"], result["indexed"])
    return result


@shared_task(bind=True, name="sonarcad.always_fail", max_retries=0)
def always_fail_task(self, session_id: str = "") -> dict:
    """死信队列验证任务：必然失败、不重试，Reject(requeue=False) 进入 sonarcad.dlq（A-13 验收用）。

    业务任务的重试上限耗尽后也应走同样路径：进死信隔离，人工排查可重放，不静默丢弃。
    """
    _fire_session_event(session_id, "FAIL")
    raise Reject(requeue=False)
