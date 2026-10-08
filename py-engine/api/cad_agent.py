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

    from app_config import MINIO_ACCESS_KEY, MINIO_PRESIGN_PUBLIC_ENDPOINT, MINIO_SECRET_KEY

    if MINIO_PRESIGN_PUBLIC_ENDPOINT:
        # 容器化：内部 endpoint（minio:9000）签出的 URL 宿主/插件不可达，且签名含 host
        # 不可事后替换（SignatureDoesNotMatch）——用公开端点独立 client 现签（T10.5）
        from urllib.parse import urlparse

        from minio import Minio

        pu = urlparse(MINIO_PRESIGN_PUBLIC_ENDPOINT)
        secure = pu.scheme == "https"
        endpoint = f"{pu.hostname}:{pu.port or (443 if secure else 9000)}"
        # region 显式固定（MinIO 默认 us-east-1）：免 presign 前置 bucket-location 网络查询
        # （公开端点在容器内不可达，查询必挂）
        public_client = Minio(endpoint, access_key=MINIO_ACCESS_KEY,
                              secret_key=MINIO_SECRET_KEY, secure=secure, region="us-east-1")
        url = public_client.presigned_get_object(ref["bucket"], ref["object_key"],
                                                 expires=timedelta(minutes=15))
    else:
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


# ----------------------------------------------------------------------
# E1 参数变更提案（T11.3，P2-1）：diff → 人工确认 → IR2 回写 → gate2 复审
# ----------------------------------------------------------------------
@router.get("/sessions/{sid}/param-proposals")
def list_param_proposals(sid: str, status: Optional[str] = Query(default=None),
                         user: str = Depends(require_user)) -> dict[str, Any]:
    """会话的参数变更提案列表（E1）。status 过滤：proposed|applied|ignored。"""
    from storage import proposal_store

    try:
        rows = proposal_store.list_for_session(sid, status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"proposals": rows}


class ConfirmProposalsBody(BaseModel):
    proposal_ids: list[str] = []  # 空 = 应用全部 proposed（前端「全部应用」）


@router.post("/sessions/{sid}/param-proposals/confirm")
def confirm_param_proposals(sid: str, body: ConfirmProposalsBody,
                            user: str = Depends(require_writer)) -> dict[str, Any]:
    """确认回写（E1 核心闭环）：IR2 追加新版本（value+derivation 同步+修订段）→
    IR4/IR5 定向失效 → 重派 a4_task → STAGE_DONE 后自动重挂 gate2 复审（复用
    pauseAtGate 乐观条件：paused+A4 ≠ gate2 命中更新）。仅 paused 会话可回写
    （completed 无复审链路，如实 409）；全部提案已 applied 时视为重派重试（幂等）。
    T14.2：同参数多轮提案先归并（每参数只应用最新一条，旧提案标 superseded）——
    "全部应用"不再发生旧值反向覆盖（审查问题 5a）。"""
    from storage import proposal_store
    from cad_agent.param_proposals import apply_proposals, merge_by_param

    rows = proposal_store.list_for_session(sid, "proposed")
    if body.proposal_ids:
        wanted = set(body.proposal_ids)
        rows = [r for r in rows if str(r["id"]) in wanted]
        missing = wanted - {str(r["id"]) for r in rows}
        if missing:
            raise HTTPException(status_code=409,
                                detail=f"提案不可应用（不存在或已处理）: {sorted(missing)[:3]}")
    if not rows:
        applied_rows = proposal_store.list_for_session(sid, "applied")
        if applied_rows:
            # 重试路径：提案已落地但 a4 重派未完成 → 仅重派
            _redispatch_a4(sid)
            return {"applied": 0, "ir2_version": applied_rows[0].get("applied_ir2_version"),
                    "redispatched": True, "note": "提案已应用过，本次仅重派 A4（上轮派发可能失败）"}
        raise HTTPException(status_code=400, detail="无可应用的提案")

    winners, superseded_rows = merge_by_param(rows)
    superseded_ids = [str(r["id"]) for r in superseded_rows]
    if superseded_ids:
        proposal_store.mark(superseded_ids, "superseded", user)
    rows = winners

    session = _session_or_404(sid)
    if session.get("pipeline_status") != "paused":
        raise HTTPException(
            status_code=409,
            detail=f"会话状态为 {session.get('pipeline_status')}：提案回写需审阅关②暂停态（复审链路）；"
                   "已完成会话的参数变更请走审阅关驳回返工")

    ir2 = ir_store.get_latest_snapshot(sid, "IR2")
    if ir2 is None:
        raise HTTPException(status_code=404, detail="IR2 快照缺失，无法回写")
    round_no = max(int(r["round_no"]) for r in rows)  # 修订段标注最新轮次
    new_ir2 = apply_proposals(ir2, rows, round_no)
    from ir_contract import validate_ir

    try:
        new_ir2 = validate_ir("IR2", new_ir2)
    except Exception as exc:  # IrContractError 等
        raise HTTPException(status_code=500, detail=f"回写后 IR2 未过契约校验: {exc}") from exc

    ir2_version = ir_store.save_snapshot(sid, "IR2", new_ir2, created_by="e1_tune")
    invalidated = ir_store.mark_invalidated(sid, ["IR4", "IR5"])
    applied_ids = [str(r["id"]) for r in rows]
    proposal_store.mark(applied_ids, "applied", user, applied_ir2_version=ir2_version)
    # T12.2：反向确认也计入传播链（origin=reverse_confirm，depth 顺延，不设上限检查——
    # 反向 confirm 不重派 A3，几何即真相，链在此自然终止）
    from storage import param_update_store

    for r in rows:
        try:
            param_update_store.record(sid, str(r["param_id"]), float(r["old_value"]),
                                      float(r["new_value"]), str(r.get("unit") or "mm"),
                                      f"反向提案确认（r{r['round_no']} 微调）",
                                      param_update_store.current_depth(sid) + 1,
                                      ir2_version, user, origin="reverse_confirm")
        except Exception:  # 链记录失败不阻断确认主链路
            logger.warning("E1 反向确认链深记录失败 session=%s", sid, exc_info=True)
    try:
        _redispatch_a4(sid)
    except Exception as exc:
        # 派发失败自愈：IR2/失效已落地，提案退回 proposed 供幂等重试（A-16 如实报状态）
        proposal_store.rearm(sid, ir2_version)
        logger.warning("E1 a4 重派失败 session=%s（提案已退回 proposed）: %s", sid, exc)
        raise HTTPException(
            status_code=503,
            detail=f"IR2 v{ir2_version} 已回写、IR4/IR5 已失效，但 A4 重派失败（{exc}）；请重试确认") from exc
    logger.info("E1 回写完成 session=%s: IR2 v%s, 失效=%s, 提案 %d 条（取代 %d 条）, 重派 A4",
                sid, ir2_version, invalidated, len(applied_ids), len(superseded_ids))
    return {"applied": len(applied_ids), "ir2_version": ir2_version,
            "invalidated": invalidated, "redispatched": True,
            "superseded": superseded_ids,
            **({"note": f"{len(superseded_ids)} 条旧轮次提案被最新提案取代，未应用"}
               if superseded_ids else {})}


@router.post("/param-proposals/{proposal_id}/ignore")
def ignore_param_proposal(proposal_id: str, user: str = Depends(require_writer)) -> dict[str, Any]:
    """忽略提案（不回写）。"""
    from storage import proposal_store

    row = proposal_store.get(proposal_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"提案不存在: {proposal_id}")
    if row["status"] != "proposed":
        raise HTTPException(status_code=409, detail=f"提案已处理（{row['status']}）")
    proposal_store.mark([proposal_id], "ignored", user)
    return {"id": proposal_id, "status": "ignored"}


def _session_or_404(sid: str) -> dict[str, Any]:
    row = ir_store.get_session(sid)
    if row is None:
        raise HTTPException(status_code=404, detail=f"设计会话不存在: {sid}")
    return row


def _redispatch_a4(sid: str) -> None:
    """重派 a4_task（E1 复审链）：与 kb_build 同款进程内 delay（celery_app 模块级 set_default）。
    派发失败抛出由调用方转 503（IR2 已落库、提案已标 applied，幂等重试入口见 confirm）。"""
    from worker.tasks import a4_task

    a4_task.delay(sid)


# ----------------------------------------------------------------------
# E1 正向参数更新（T12.2）：文档→CAD（总体设计 §5.6/§8.2 认领）
# ----------------------------------------------------------------------
class ParamUpdateBody(BaseModel):
    value: float
    reason: Optional[str] = None


@router.post("/sessions/{sid}/parameters/{pid}/update")
def update_parameter(sid: str, pid: str, body: ParamUpdateBody,
                     user: str = Depends(require_writer)) -> dict[str, Any]:
    """E1 正向联动：修改 IR2 参数 → A3 重新建模 → gate2 复审（局部回环，不回 A1/A2）。

    变更抑制（系统实施方案 Origin Lock/Max Depth=3 的务实落地）：
    ①仅 paused@gate2 可发起（单飞行窗口，串行化）；②传播深度 >3 拒绝；③A3 重算后
    几何若仍与参数有偏差，反向提案照常生成但挂起待人工确认（确认制=回写拦截器）。"""
    from storage import param_update_store
    from cad_agent.param_proposals import apply_forward_update

    session = _session_or_404(sid)
    if not (session.get("pipeline_status") == "paused" and session.get("current_stage") == "gate2"):
        raise HTTPException(
            status_code=409,
            detail=f"会话状态为 {session.get('pipeline_status')}@{session.get('current_stage')}："
                   "正向参数更新仅可在审阅关②暂停态发起（gate1 前改需求走 IR1 修订，"
                   "已完成会话走审阅关驳回返工）")
    if body.value <= 0:
        raise HTTPException(status_code=400, detail="参数值必须为正数")

    ir2 = ir_store.get_latest_snapshot(sid, "IR2")
    if ir2 is None:
        raise HTTPException(status_code=404, detail="IR2 快照缺失")
    param = next((p for p in ir2.get("parameters") or [] if str(p.get("id")) == str(pid)), None)
    if param is None:
        raise HTTPException(status_code=404, detail=f"参数不存在: {pid}")

    try:
        depth = param_update_store.next_depth_or_raise(sid)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    old_value = param.get("value")
    new_ir2 = apply_forward_update(ir2, pid, body.value, body.reason)
    from ir_contract import validate_ir

    try:
        new_ir2 = validate_ir("IR2", new_ir2)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"更新后 IR2 未过契约校验: {exc}") from exc

    ir2_version = ir_store.save_snapshot(sid, "IR2", new_ir2, created_by="e1_forward")
    invalidated = ir_store.mark_invalidated(sid, ["IR3", "IR4", "IR5"])
    param_update_store.record(sid, pid,
                              float(old_value) if isinstance(old_value, (int, float)) else None,
                              float(body.value), str(param.get("unit") or ""), body.reason,
                              depth, ir2_version, user)
    try:
        from worker.tasks import a3_task

        a3_task.delay(sid)
    except Exception as exc:
        logger.warning("E1 正向更新 a3 重派失败 session=%s: %s", sid, exc)
        raise HTTPException(
            status_code=503,
            detail=f"IR2 v{ir2_version} 已回写、IR3/IR4/IR5 已失效，但 A3 重派失败（{exc}）；"
                   "请稍后以相同参数值重试（将追加新版本，历史留痕）") from exc
    logger.info("E1 正向更新 session=%s %s: %s→%s（depth=%s, IR2 v%s），已重派 A3",
                sid, pid, old_value, body.value, depth, ir2_version)
    return {"ir2_version": ir2_version, "param_id": pid, "old_value": old_value,
            "new_value": body.value, "invalidated": invalidated, "depth": depth,
            "a3_redispatched": True,
            "note": "A3 将按新参数重新建模并重算 A4，完成后自动回到审阅关②复审"}
