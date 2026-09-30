"""CAD 交互微调 API（T8.3，PHASE-008，ADR-004）。

FreeCAD 插件与前端的会话面：插件经 gateway JWT 登录后长轮询工具调用/回传结果/
上传定稿产物；前端创建微调会话、看轮次与版本时间线。端点契约见 PHASE-008 §2.4。

诚实语义（A-16）：迟到回传拒收 409、非 awaiting_upload 拒绝上传 409、
LLM/插件失败透因到 round.error，不伪造成功。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from api.authz import require_user, require_writer
from cad_agent import service
from storage import ir_store, tune_store

logger = logging.getLogger("sonarcad.cad_agent.api")

router = APIRouter(prefix="/cad-agent", tags=["cad-agent"])

_LONGPOLL_CAP_S = 25  # 低于网关默认读超时，插件按返回即发起下一轮


def _tune_or_404(tune_id: str) -> dict[str, Any]:
    tune = tune_store.get_tune_session(tune_id)
    if tune is None:
        raise HTTPException(status_code=404, detail=f"微调会话不存在: {tune_id}")
    return tune


def _ir3_or_404(sid: str) -> tuple[dict[str, Any], int]:
    ir3 = ir_store.get_latest_snapshot(sid, "IR3")
    if not ir3 or not ir3.get("step_file"):
        raise HTTPException(status_code=404, detail="IR3 尚未生成，无 CAD 产物可微调")
    return ir3, ir_store.get_latest_version(sid, "IR3")


# ----------------------------------------------------------------------
# 微调会话
# ----------------------------------------------------------------------
@router.post("/sessions/{sid}/tune")
def create_tune(sid: str, user: str = Depends(require_writer)) -> dict[str, Any]:
    """创建微调会话（A3 之后、审阅关②之前；同会话已有 active 会话→409 返回既有 id）。"""
    service.ensure_healed()
    _ir3_or_404(sid)
    active = tune_store.get_active_tune_session(sid)
    if active:
        raise HTTPException(status_code=409, detail=f"该设计会话已有进行中的微调会话: {active['id']}")
    _, version = _ir3_or_404(sid)
    row = tune_store.create_tune_session(sid, baseline_ir_version=version, created_by=user)
    return _with_rounds(row)


@router.get("/sessions/{sid}/tune")
def list_tunes(sid: str, user: str = Depends(require_user)) -> dict[str, Any]:
    service.ensure_healed()
    return {"sessions": [_with_rounds(r) for r in tune_store.list_tune_sessions(sid)]}


@router.get("/tune/{tune_id}")
def get_tune(tune_id: str, user: str = Depends(require_user)) -> dict[str, Any]:
    service.ensure_healed()
    return _with_rounds(_tune_or_404(tune_id))


def _with_rounds(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "rounds": tune_store.list_rounds(row["id"])}


# ----------------------------------------------------------------------
# 模型上下文（插件拉取）
# ----------------------------------------------------------------------
@router.get("/tune/{tune_id}/context")
def get_context(tune_id: str, version: Optional[int] = Query(default=None),
                user: str = Depends(require_user)) -> dict[str, Any]:
    tune = _tune_or_404(tune_id)
    sid = str(tune["design_session_id"])
    v = version or ir_store.get_latest_version(sid, "IR3")
    if v <= 0:
        raise HTTPException(status_code=404, detail="IR3 尚未生成，无产物可拉取")
    ir3 = ir_store.get_snapshot(sid, "IR3", v)
    if not ir3 or not ir3.get("step_file"):
        raise HTTPException(status_code=404, detail=f"IR3 v{v} 不存在或无 STEP 产物")
    ref = ir3["step_file"]
    from storage.object_store import _get_client

    url = _get_client().presigned_get_object(ref["bucket"], ref["object_key"],
                                             expires=timedelta(minutes=15))
    from cad_agent.service import _geometry_summary_text

    return {
        "tune_id": tune_id,
        "version": v,
        "step_url": url,
        "filename": ref["object_key"].split("/")[-1] or f"ir3_v{v}.step",
        "geometry_summary": _geometry_summary_text(ir3.get("geometry_summary")),
        "tune_status": tune["status"],
    }


# ----------------------------------------------------------------------
# 对话轮次
# ----------------------------------------------------------------------
class MessageBody(BaseModel):
    text: str
    selection: dict[str, Any] | None = None


@router.post("/tune/{tune_id}/messages")
def post_message(tune_id: str, body: MessageBody, user: str = Depends(require_writer)) -> dict[str, Any]:
    """设计人员输入 → 创建轮次并后台起 Runner；立即返回 round_no（面板轮询 /rounds/{n}）。"""
    service.ensure_healed()
    tune = _tune_or_404(tune_id)
    if tune["status"] != "active":
        raise HTTPException(status_code=409, detail=f"微调会话已结束（{tune['status']}），不可继续对话")
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="消息内容不能为空")
    sid = str(tune["design_session_id"])
    latest = tune_store.latest_round_no(tune_id)
    if latest:
        last = tune_store.get_round(tune_id, latest)
        if last and last["status"] in ("running", "awaiting_upload"):
            raise HTTPException(status_code=409,
                                detail=f"上一轮 r{latest} 尚未收敛（{last['status']}），请等待完成或先上传/放弃该轮产物")
    _, baseline = _ir3_or_404(sid)
    rnd = tune_store.create_round(tune_id, latest + 1, baseline_ir_version=baseline,
                                  prompt=body.text.strip(), selection=body.selection, created_by=user)
    service.spawn_round(tune, rnd)
    return rnd


@router.get("/tune/{tune_id}/rounds/{round_no}")
def get_round(tune_id: str, round_no: int, user: str = Depends(require_user)) -> dict[str, Any]:
    rnd = tune_store.get_round(tune_id, round_no)
    if rnd is None:
        raise HTTPException(status_code=404, detail=f"轮次 r{round_no} 不存在")
    return rnd


# ----------------------------------------------------------------------
# 工具调用长轮询（插件侧）
# ----------------------------------------------------------------------
@router.get("/tune/{tune_id}/tool-calls")
async def poll_tool_calls(tune_id: str, cursor: int = Query(default=0, ge=0),
                          wait: int = Query(default=15, ge=0, le=_LONGPOLL_CAP_S),
                          user: str = Depends(require_user)) -> dict[str, Any]:
    """长轮询：cursor 之后 pending 的工具调用；wait 秒内无新调用返回空数组。

    T9.1 async 化：原同步实现每连接占用线程池线程最长 25s（wait 上限），
    插件并发轮询会耗尽默认 40 线程池导致全站「连接建立但无响应」；
    现挂起在事件循环上，仅每 0.5s 借线程做一次快查。
    """
    await asyncio.to_thread(_tune_or_404, tune_id)
    deadline = time.time() + wait
    while True:
        calls = await asyncio.to_thread(tune_store.poll_tool_calls, tune_id, after_id=cursor)
        if calls:
            return {"calls": calls, "cursor": calls[-1]["call_id"]}
        if time.time() >= deadline:
            return {"calls": [], "cursor": cursor}
        await asyncio.sleep(0.5)


class ToolResultBody(BaseModel):
    ok: bool = False
    stdout: str | None = None
    quality: dict[str, Any] | None = None
    doc_summary: str | None = None
    error: str | None = None


@router.post("/tune/{tune_id}/tool-calls/{call_id}/result")
def complete_tool_call(tune_id: str, call_id: int, body: ToolResultBody,
                       user: str = Depends(require_user)) -> dict[str, Any]:
    _tune_or_404(tune_id)
    if tune_store.get_tool_call(call_id) is None:
        raise HTTPException(status_code=404, detail=f"工具调用 {call_id} 不存在")
    done = tune_store.complete_tool_call(call_id, body.model_dump())
    if done is None:
        raise HTTPException(status_code=409, detail=f"工具调用 {call_id} 已完成或已超时——迟到回传被拒收")
    return {"call_id": call_id, "status": "done"}


# ----------------------------------------------------------------------
# 定稿上传 / 结束
# ----------------------------------------------------------------------
@router.post("/tune/{tune_id}/versions")
def upload_version(tune_id: str,
                   round_no: int = Form(...),
                   step_file: UploadFile = File(...),
                   fcstd_file: Optional[UploadFile] = File(default=None),
                   script: str = Form(default=""),
                   user: str = Depends(require_writer)) -> dict[str, Any]:
    """轮次收敛后插件上传产物：STEP 必带，FCStd 可选（平台持久化=保存机制补全）。

    服务端实测几何并追加 IR3 版本；前端三维预览经 ir_updated 自动刷新。
    """
    tune = _tune_or_404(tune_id)
    if tune["status"] != "active":
        raise HTTPException(status_code=409, detail=f"微调会话已结束（{tune['status']}），不可上传")
    step_bytes = step_file.file.read()
    if not step_bytes:
        raise HTTPException(status_code=400, detail="step_file 为空")
    fcstd_bytes = fcstd_file.file.read() if fcstd_file else None
    try:
        return service.finalize_version(tune, round_no, step_bytes, fcstd_bytes, script or None)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class FinalizeBody(BaseModel):
    note: str | None = None


@router.post("/tune/{tune_id}/versions/manual")
def upload_manual_version(tune_id: str,
                          step_file: UploadFile = File(...),
                          fcstd_file: Optional[UploadFile] = File(default=None),
                          user: str = Depends(require_writer)) -> dict[str, Any]:
    """设计师在 FreeCAD 视口手动调整后直接上传：新建 manual 轮次承接（复用版本链与留痕）。"""
    tune = _tune_or_404(tune_id)
    if tune["status"] != "active":
        raise HTTPException(status_code=409, detail=f"微调会话已结束（{tune['status']}），不可上传")
    step_bytes = step_file.file.read()
    if not step_bytes:
        raise HTTPException(status_code=400, detail="step_file 为空")
    fcstd_bytes = fcstd_file.file.read() if fcstd_file else None
    round_no = service.begin_manual_round(tune)
    try:
        return service.finalize_version(tune, round_no, step_bytes, fcstd_bytes,
                                        service.MANUAL_UPLOAD_SCRIPT)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/tune/{tune_id}/finalize")
def finalize_tune(tune_id: str, body: FinalizeBody | None = None,
                  user: str = Depends(require_writer)) -> dict[str, Any]:
    """定稿：审阅关②可审最新版本。"""
    tune = _tune_or_404(tune_id)
    if tune["status"] != "active":
        raise HTTPException(status_code=409, detail=f"微调会话当前为 {tune['status']}，不可定稿")
    note = body.note if body else None
    row = tune_store.update_tune_status(tune_id, "finalized", note=note)
    _notify_cad(tune, "finalized")
    return row


@router.post("/tune/{tune_id}/close")
def close_tune(tune_id: str, user: str = Depends(require_writer)) -> dict[str, Any]:
    """放弃微调（已产生版本如实保留留痕）。"""
    tune = _tune_or_404(tune_id)
    if tune["status"] != "active":
        raise HTTPException(status_code=409, detail=f"微调会话当前为 {tune['status']}，不可放弃")
    row = tune_store.update_tune_status(tune_id, "abandoned")
    _notify_cad(tune, "closed")
    return row


def _notify_cad(tune: dict[str, Any], state: str) -> None:
    service._notify_tune(str(tune["design_session_id"]), tune["id"], state)
