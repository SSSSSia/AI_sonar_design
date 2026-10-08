"""CAD 交互微调编排服务（T8.3，PHASE-008）。

职责：轮次生命周期编排（消息→Runner 线程→状态推进）、工具协议服务端实现
（tune_store 建行+等插件回传）、定稿上传→几何实测→IR3 版本追加、WS 事件发布。

载体决策（D8-6）：AgentLoop 跑 API 进程线程池——工具等待是 DB 轮询非 CPU 密集；
服务重启时 running/awaiting_upload 轮次由 ensure_healed() 启动自愈标 failed（A-16）。
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from app_config import (
    BUCKET_CAD_TUNES,
    CAD_INSPECT_TIMEOUT_S,
    CAD_TUNE_MAX_CONTEXT_TOKENS,
    CAD_TUNE_MAX_ITERATIONS,
    CAD_TUNE_MAX_WORKERS,
    CAD_TUNE_TOOL_TIMEOUT_S,
    NOTIFY_BASE_URL,
    SERVICE_AUTH_HEADERS,
)
from cad_agent.loop import TuneRoundRunner
from cad_agent.prompts import build_round_context
from storage import ir_store, session_meta, tune_store

logger = logging.getLogger("sonarcad.cad_agent.service")

GEOMETRY_INSPECT_CLI = Path(__file__).resolve().parents[1] / "cad_engine" / "geometry_inspect.py"

_executor: ThreadPoolExecutor | None = None
_healed = False
_heal_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=CAD_TUNE_MAX_WORKERS, thread_name_prefix="cad-tune")
    return _executor


def ensure_healed() -> None:
    """进程首次接到微调请求时做一次中断自愈（重启后 running 轮次如实标 failed）。"""
    global _healed
    if _healed:
        return
    with _heal_lock:
        if _healed:
            return
        try:
            tune_store.expire_running_rounds()
        except Exception as exc:
            logger.warning("微调轮启动自愈失败（不影响服务）：%s", exc)
        _healed = True


# ----------------------------------------------------------------------
# WS 事件（复用 worker→notify 同路 HTTP 发布；失败仅记日志，推送为增强）
# ----------------------------------------------------------------------
def _notify_tune(session_id: str, tune_id: str, state: str,
                 round_no: int | None = None, ir_version: int | None = None) -> None:
    try:
        httpx.post(
            f"{NOTIFY_BASE_URL}/internal/sessions/{session_id}/messages",
            headers=SERVICE_AUTH_HEADERS,
            json={
                "type": "cad_tune_updated",
                "session_id": session_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"tune_id": tune_id, "state": state,
                            "round_no": round_no, "ir_version": str(ir_version) if ir_version else None},
            },
            timeout=3,
        )
    except Exception as exc:
        logger.warning("session %s cad_tune_updated 发布失败（不影响微调）: %s", session_id, exc)


def _notify_ir_updated(session_id: str, ir_type: str, version: int) -> None:
    """微调上传产生新 IR3 版本后，同路发 ir_updated——前端既有预览刷新逻辑零改动。"""
    try:
        from worker.tasks import _notify_ir_updated as worker_notify

        worker_notify(session_id, ir_type, version)
    except Exception as exc:
        logger.warning("session %s ir_updated 发布失败（不影响微调）: %s", session_id, exc)


# ----------------------------------------------------------------------
# 轮次上下文
# ----------------------------------------------------------------------
def _geometry_summary_text(summary: dict[str, Any] | None) -> str | None:
    if not summary:
        return None
    lines: list[str] = []
    if summary.get("bbox_mm"):
        lines.append(f"overall bbox (mm): {summary['bbox_mm']}")
    if summary.get("volume_mm3") is not None:
        lines.append(f"overall volume: {summary['volume_mm3']} mm3")
    if summary.get("part_count") is not None:
        lines.append(f"solid parts: {summary['part_count']}")
    if summary.get("detail"):
        lines.append(f"detail: {summary['detail']}")
    return "\n".join(lines) or None


def _kb_digest(session_id: str, prompt: str) -> str | None:
    """会话选库的领域知识摘要（阶段 6 scoped 检索；失败/零命中如实返回 None，不阻塞微调）。"""
    try:
        from knowledge.service import get_knowledge_service

        kb_ids = session_meta.get_kb_ids(session_id)
        result = get_knowledge_service().query(prompt[:200], kb_ids=kb_ids)
        d = result.to_dict()
        content = (d.get("content") or "").strip()
        if not content or d.get("level") == "unavailable":
            return None
        return content[:600]
    except Exception as exc:
        logger.info("微调上下文 kb 摘要跳过（session=%s）：%s", session_id, exc)
        return None


def build_context_for_round(session_id: str, baseline_version: int,
                            prompt: str, selection: dict | None) -> str:
    ir3 = ir_store.get_snapshot(session_id, "IR3", baseline_version)
    return build_round_context(
        baseline_version=baseline_version,
        geometry_summary=_geometry_summary_text((ir3 or {}).get("geometry_summary")),
        selection=selection,
        kb_digest=_kb_digest(session_id, prompt),
    )


# ----------------------------------------------------------------------
# 工具协议服务端（Runner 的 tool_executor 实现）
# ----------------------------------------------------------------------
def protocol_execute(round_id: str, name: str, args: dict) -> dict:
    """下发工具调用给插件并阻塞等待回传；超时由 wait_tool_call 合成错误结果。"""
    row = tune_store.create_tool_call(round_id, name, args)
    result_row = tune_store.wait_tool_call(row["id"], CAD_TUNE_TOOL_TIMEOUT_S)
    result = result_row.get("result") if result_row else None
    if not isinstance(result, dict):
        result = {"ok": False, "error": "插件无有效回传"}
    return result


def _llm_chat(messages: list[dict]) -> str:
    from llm.adapter import llm_adapter

    return llm_adapter.chat(messages)


# ----------------------------------------------------------------------
# 轮次执行（线程池内）
# ----------------------------------------------------------------------
def spawn_round(tune: dict[str, Any], round_row: dict[str, Any]) -> None:
    _get_executor().submit(_run_round, tune, round_row)


def _run_round(tune: dict[str, Any], round_row: dict[str, Any]) -> None:
    session_id = str(tune["design_session_id"])
    tune_id = tune["id"]
    round_id = round_row["id"]
    try:
        context = build_context_for_round(session_id, round_row["baseline_ir_version"],
                                          round_row["prompt"], round_row.get("selection"))
        runner = TuneRoundRunner(
            context=context,
            user_prompt=round_row["prompt"],
            llm_chat=_llm_chat,
            tool_executor=lambda name, args: protocol_execute(round_id, name, args),
            max_iterations=CAD_TUNE_MAX_ITERATIONS,
            max_context_tokens=CAD_TUNE_MAX_CONTEXT_TOKENS,
        )
        result = runner.run()
    except Exception as exc:
        logger.exception("微调轮内部异常 tune=%s round=%s", tune_id, round_row["round_no"])
        tune_store.update_round(round_id, status="failed", error=f"微调轮内部异常：{exc}")
        _notify_tune(session_id, tune_id, "failed", round_row["round_no"])
        return

    trace = {
        "tools": result.llm_trace,
        "runner": {"success": result.success, "summary": result.summary, "error": result.error},
    }
    if result.success and result.script:
        tune_store.update_round(round_id, status="awaiting_upload",
                                script=result.script, llm_trace=trace)
        _notify_tune(session_id, tune_id, "awaiting_upload", round_row["round_no"])
    elif result.success:  # 问答轮：无几何变更，不触发上传
        tune_store.update_round(round_id, status="done", llm_trace=trace)
        _notify_tune(session_id, tune_id, "round_done", round_row["round_no"])
    else:
        tune_store.update_round(round_id, status="failed", error=result.summary, llm_trace=trace)
        _notify_tune(session_id, tune_id, "failed", round_row["round_no"])
    logger.info("微调轮收敛 tune=%s round=%s success=%s script=%s",
                tune_id, round_row["round_no"], result.success, bool(result.script))


# ----------------------------------------------------------------------
# 定稿上传 → 几何实测 → IR3 版本追加
# ----------------------------------------------------------------------
MANUAL_UPLOAD_SCRIPT = "# 手动上传（设计师在 FreeCAD 视口直接调整，无对话编辑脚本）"


def begin_manual_round(tune: dict[str, Any]) -> int:
    """手动上传通道：新建一个 manual 轮次承接，复用轮次留痕与版本链（A-02 溯源不断）。

    单用户桌面插件场景，latest_round_no+1 的并发窗口可忽略。
    """
    round_no = tune_store.latest_round_no(str(tune["id"])) + 1
    rnd = tune_store.create_round(
        str(tune["id"]), round_no, tune.get("baseline_ir_version"),
        "（手动上传：设计师在 FreeCAD 视口直接调整，无对话脚本）", None, created_by="manual")
    tune_store.update_round(rnd["id"], status="awaiting_upload", script=MANUAL_UPLOAD_SCRIPT)
    return round_no


def _inspect_step_file(step_path: str) -> dict[str, Any] | None:
    """subprocess 调 geometry_inspect.py（A3 同款：OCCT 不进 agent 进程）。

    None=提取禁用；{"ok": False, "error": ...}=失败/超时（A-16 如实），不抛异常。
    """
    if CAD_INSPECT_TIMEOUT_S <= 0 or not step_path:
        return None
    out_path = f"{step_path}.inspect.json"
    cmd = [sys.executable, str(GEOMETRY_INSPECT_CLI), step_path, out_path]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=CAD_INSPECT_TIMEOUT_S, check=False)
        result = json.loads(Path(out_path).read_text(encoding="utf-8"))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"geometry inspect timeout after {CAD_INSPECT_TIMEOUT_S}s"}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"geometry inspect output unreadable: {exc}"}
    finally:
        try:
            if Path(out_path).exists():
                Path(out_path).unlink()
        except OSError:
            pass
    return result


def finalize_version(tune: dict[str, Any], round_no: int,
                     step_bytes: bytes, fcstd_bytes: bytes | None,
                     script_override: str | None) -> dict[str, Any]:
    """上传轮次产物：MinIO 落盘 → STEP 实测 → IR3 追加新版本 → 回填轮次 → WS 推送。

    几何提取失败不阻断（features=[] + summary 如实 extraction_failed，管线继续——A3 同款）。
    微调版本的 features 不做参数联动（人工编辑有意偏离设计参数，偏差交 gate2 人工裁决）。
    """
    from ir_contract import validate_ir
    from storage import object_store

    session_id = str(tune["design_session_id"])
    tune_id = tune["id"]
    rnd = tune_store.get_round(tune_id, round_no)
    if rnd is None:
        raise ValueError(f"轮次 r{round_no} 不存在")
    if rnd["status"] != "awaiting_upload":
        raise ValueError(f"轮次 r{round_no} 状态为 {rnd['status']}，不可上传（需 awaiting_upload）")

    script = (script_override or "").strip() or rnd.get("script") or ""
    if not script:
        raise ValueError("轮次无编辑脚本可上传")

    step_key = f"{session_id}/{tune_id}/r{round_no}.step"
    fcstd_key = f"{session_id}/{tune_id}/r{round_no}.fcstd"
    script_key = f"{session_id}/{tune_id}/r{round_no}.py"
    step_ref = object_store.upload_bytes(step_bytes, BUCKET_CAD_TUNES, step_key,
                                         content_type="application/step")
    if fcstd_bytes:
        object_store.upload_bytes(fcstd_bytes, BUCKET_CAD_TUNES, fcstd_key,
                                  content_type="application/x-fcstd")
    object_store.upload_bytes(script.encode("utf-8"), BUCKET_CAD_TUNES, script_key,
                              content_type="text/x-python")

    # 几何实测（临时文件喂 CLI）
    inspect: dict[str, Any] | None
    with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as tmp:
        tmp.write(step_bytes)
        tmp_path = tmp.name
    try:
        inspect = _inspect_step_file(tmp_path)
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass

    features: list[dict[str, Any]] = []
    summary: dict[str, Any]
    if inspect is None:
        summary = {"source": "extraction_failed", "detail": "跳过：几何提取已禁用（CAD_INSPECT_TIMEOUT_S=0）"}
    elif not inspect.get("ok"):
        detail = str(inspect.get("error") or "unknown error")[:500]
        logger.warning("微调版本几何提取失败（降级继续）: %s", detail)
        summary = {"source": "extraction_failed", "detail": f"人工微调 r{round_no} · {detail}"}
    else:
        parts = inspect.get("parts", [])
        for part in parts:
            features.append({
                "feature_id": f"F-{part['index']:03d}",
                "segment": f"SEG-{part['index']:02d}",
                "geometry": {
                    "bbox_mm": part["bbox_mm"],
                    "volume_mm3": part["volume_mm3"],
                    "center_mm": part["center_mm"],
                },
                "linked_param_id": None,  # 人工微调版本解除参数联动（D8 记录于 phase 文档 §2.4）
            })
        overall = inspect.get("overall", {})
        meta = inspect.get("meta", {})
        summary = {
            "source": "extracted",
            "bbox_mm": overall.get("bbox_mm"),
            "volume_mm3": overall.get("volume_mm3"),
            "part_count": overall.get("part_count"),
            "detail": f"人工微调 r{round_no} · {meta.get('segmentation', '?')} · {meta.get('extractor', '?')}"
                      f"（参数联动解除，与设计参数的偏差由审阅关②人工裁决）",
        }

    # 脚本溯源链：基线脚本 + 追加微调段（如实保留完整来龙去脉，A-02）
    base_ir3 = ir_store.get_latest_snapshot(session_id, "IR3") or {}
    base_script = base_ir3.get("cadquery_script", "")
    combined = base_script + f"\n\n# ---- CAD 微调 r{round_no}（FreeCAD 人工对话编辑，增量脚本；基线为 STEP 导入，非参数化链）----\n" + script

    ir3 = validate_ir("IR3", {
        "cadquery_script": combined,
        "step_file": step_ref,
        "features": features,
        "geometry_summary": summary,
    })
    version = ir_store.save_snapshot(session_id, "IR3", ir3, created_by="cad_tune")
    # T14.5（审查问题 4b）：模型已变更 → 旧校验报告（IR4）/知识候选（IR5）立即失效，
    # 不以现行身份展示（get_latest_snapshot 对算力侧读过滤已失效版本）——复审链路照旧，
    # 由 gate2 人工复审 / 提案确认回写触发重验。
    invalidated = ir_store.mark_invalidated(session_id, ["IR4", "IR5"])
    tune_store.update_round(rnd["id"], status="done", result_ir3_version=version)
    _notify_ir_updated(session_id, "IR3", version)
    _notify_tune(session_id, tune_id, "version_ready", round_no, version)
    logger.info("微调版本已定稿 session=%s tune=%s r%s → IR3 v%s（失效=%s）",
                session_id, tune_id, round_no, version, invalidated)
    _generate_param_proposals(tune, round_no, ir3, version)
    return {"ir_version": version, "geometry_summary": summary, "step_ref": step_ref,
            "invalidated": invalidated}


def _generate_param_proposals(tune: dict[str, Any], round_no: int, new_ir3: dict[str, Any],
                              ir3_version: int) -> None:
    """E1（T11.3）：定稿后 diff 基线 IR3 生成参数变更提案（落 cad_tune_param_proposals）。

    提案是增强而非权威：任何失败仅告警，不影响定稿主链路。无联动基线（链式微调）或
    分段数变化时如实产 0 条（A-16）。
    """
    try:
        from cad_agent.param_proposals import diff_proposals
        from storage import ir_store as _ir_store, proposal_store

        session_id = str(tune["design_session_id"])
        baseline_version = int(tune["baseline_ir_version"])
        baseline_ir3 = _ir_store.get_snapshot(session_id, "IR3", baseline_version)
        ir2 = _ir_store.get_latest_snapshot(session_id, "IR2")
        if baseline_ir3 is None or ir2 is None:
            logger.info("E1 提案跳过：基线 IR3 v%s 或 IR2 缺失 session=%s", baseline_version, session_id)
            return
        found = diff_proposals(ir2, baseline_ir3, new_ir3)
        stored = proposal_store.replace_round(session_id, str(tune["id"]), round_no,
                                              ir3_version, baseline_version, found)
        logger.info("E1 提案生成 session=%s tune=%s r%s：%d 条（落库 %d）",
                    session_id, tune["id"], round_no, len(found), len(stored))
    except Exception:  # noqa: BLE001 — 提案失败不阻断定稿
        logger.exception("E1 参数提案生成失败（定稿不受影响）session=%s tune=%s", tune.get("design_session_id"), tune.get("id"))
