"""T4.3：三维预览（STEP→GLB，异步转换 + MinIO 版本缓存）+ 交付物预签名下载。

设计要点（PHASE-004 §2.6）：
- 转换重（大装配 OCC 导入分钟级），两段式：POST /gltf/convert 触发后台线程；GET /gltf 查询状态/返回字节。
- 缓存键 cad-previews/{sid}/{version}.glb：命中直接回（同 IR 版本不重算）。
- 下载走 MinIO 预签名 URL（15 分钟），未登录 401；脚本交付物直接用 IR3 内容文本，不依赖对象存储。
- 失败如实 503（转换失败原因透出，A-16），前端保留 STEP 下载入口。
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from api.authz import require_user
from app_config import BUCKET_STEP_MODELS
from storage import ir_store

logger = logging.getLogger("sonarcad.cad")

router = APIRouter(prefix="/cad", tags=["cad"])

PREVIEW_BUCKET = "cad-previews"
# 进程内转换状态：{f"{sid}:{version}": {"state": "converting"|"ready"|"failed", "detail": str}}
_convert_states: dict[str, dict[str, str]] = {}
_states_lock = threading.Lock()


def _ir3_or_404(sid: str) -> tuple[dict[str, Any], int]:
    ir3 = ir_store.get_latest_snapshot(sid, "IR3")
    if not ir3 or not ir3.get("step_file"):
        raise HTTPException(status_code=404, detail="IR3 尚未生成，无 CAD 产物")
    version = ir_store.get_latest_version(sid, "IR3")
    return ir3, version


def _minio():
    from storage.object_store import _get_client

    return _get_client()


def _ensure_preview_bucket() -> None:
    cli = _minio()
    if not cli.bucket_exists(PREVIEW_BUCKET):
        cli.make_bucket(PREVIEW_BUCKET)


def _convert_to_glb(step_bytes: bytes, diag_hint: float | None = None) -> bytes:
    """STEP → GLB。cadquery 镶嵌（容差随包围盒缩放）→ trimesh 组 GLB。CPU 密集，调用方放后台线程。"""
    import cadquery as cq
    import trimesh

    tmp = os.path.join(tempfile.gettempdir(), f"cad_preview_{int(time.time()*1000)}.step")
    with open(tmp, "wb") as f:
        f.write(step_bytes)
    try:
        shape = cq.importers.importStep(tmp).val()
        bb = shape.BoundingBox()
        diag = diag_hint or ((bb.xlen**2 + bb.ylen**2 + bb.zlen**2) ** 0.5) or 1000.0
        tol = max(1.0, diag * 0.0003)
        verts, tris = shape.tessellate(tol)
        mesh = trimesh.Trimesh(
            vertices=[(v.x, v.y, v.z) for v in verts], faces=list(tris), process=True
        )
        buf = io.BytesIO()
        mesh.export(buf, file_type="glb")
        return buf.getvalue()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _run_convert(sid: str, version: int, bucket: str, key: str) -> None:
    state_key = f"{sid}:{version}"
    try:
        step_bytes = _minio().get_object(bucket, key).read()
        glb = _convert_to_glb(step_bytes)
        _ensure_preview_bucket()
        _minio().put_object(
            PREVIEW_BUCKET, f"{sid}/{version}.glb", io.BytesIO(glb), length=len(glb),
            content_type="model/gltf-binary",
        )
        with _states_lock:
            _convert_states[state_key] = {"state": "ready", "detail": ""}
        logger.info("cad 预览转换完成 sid=%s v%s glb=%dB", sid, version, len(glb))
    except Exception as exc:  # 如实记录失败原因（A-16）
        logger.exception("cad 预览转换失败 sid=%s v%s", sid, version)
        with _states_lock:
            _convert_states[state_key] = {"state": "failed", "detail": str(exc)[:300]}


@router.post("/sessions/{sid}/gltf/convert")
def start_convert(sid: str, user: str = Depends(require_user)) -> dict[str, Any]:
    ir3, version = _ir3_or_404(sid)
    state_key = f"{sid}:{version}"
    with _states_lock:
        state = _convert_states.get(state_key)
    # 已缓存则直接标记 ready（幂等）
    try:
        _minio().stat_object(PREVIEW_BUCKET, f"{sid}/{version}.glb")
        return {"state": "ready", "version": version}
    except Exception:
        pass
    if state and state["state"] == "converting":
        return {"state": "converting", "version": version}
    step = ir3["step_file"]
    thread = threading.Thread(
        target=_run_convert, args=(sid, version, step["bucket"], step["object_key"]),
        name=f"cad-convert-{sid[:8]}", daemon=True,
    )
    with _states_lock:
        _convert_states[state_key] = {"state": "converting", "detail": ""}
    thread.start()
    return {"state": "converting", "version": version, "note": "大装配首次转换可能需要数分钟，结果按版本缓存"}


@router.get("/sessions/{sid}/gltf")
def get_gltf(sid: str, user: str = Depends(require_user)) -> Response:
    ir3, version = _ir3_or_404(sid)
    try:
        data = _minio().get_object(PREVIEW_BUCKET, f"{sid}/{version}.glb").read()
        return Response(
            content=data, media_type="model/gltf-binary",
            headers={"X-IR-Version": str(version), "Content-Disposition": f'inline; filename="ir3_v{version}.glb"'},
        )
    except Exception:
        pass
    with _states_lock:
        state = _convert_states.get(f"{sid}:{version}")
    if state and state["state"] == "failed":
        raise HTTPException(status_code=503, detail=f"三维预览转换失败：{state['detail']}（可下载 STEP 查看）")
    return Response(
        content='{"state": "converting"}', media_type="application/json", status_code=202,
        headers={"X-Convert-State": (state or {}).get("state", "idle")},
    )


@router.get("/sessions/{sid}/download")
def download(sid: str, kind: str = Query(..., pattern="^(step|script)$"), user: str = Depends(require_user)) -> dict[str, Any]:
    ir3, version = _ir3_or_404(sid)
    if kind == "step":
        from datetime import timedelta

        ref = ir3["step_file"]
        url = _minio().presigned_get_object(ref["bucket"], ref["object_key"], expires=timedelta(minutes=15))
        filename = ref["object_key"].split("/")[-1] or f"ir3_v{version}.step"
        return {"url": url, "filename": filename, "expires_in": 900}
    # 脚本直接用 IR3 内容（不依赖对象存储键约定）
    import urllib.parse

    script = ir3.get("cadquery_script", "")
    data_uri = "data:text/x-python;charset=utf-8," + urllib.parse.quote(script)
    return {"url": data_uri, "filename": f"ir3_v{version}.py", "expires_in": 0, "inline": True}


@router.delete("/sessions/{sid}/artifacts")
def delete_session_artifacts(sid: str, user: str = Depends(require_user)) -> dict:
    """会话删除联动清理（workflow SessionCleanup 内网直连）：MinIO 三桶按 {sid}/ 前缀清理。

    - 覆盖 step-models / cad-scripts / cad-previews（键布局均为 {sid}/xxx）。
    - Neo4j 已过审沉淀的知识**不在清理范围**（共享资产）；其 source_session 标记
      会随会话删除悬空，属如实保留（A-16：不静默伪造来源，也不连坐删知识）。
    - 清理尽力而为：逐桶独立容错，返回逐桶计数，单桶失败不拖垮整体。
    """
    from app_config import BUCKET_CAD_SCRIPTS

    cli = _minio()
    per_bucket: dict[str, int] = {}
    for bucket in (BUCKET_STEP_MODELS, BUCKET_CAD_SCRIPTS, PREVIEW_BUCKET):
        try:
            if not cli.bucket_exists(bucket):
                per_bucket[bucket] = 0
                continue
            names = [o.object_name for o in cli.list_objects(bucket, prefix=f"{sid}/", recursive=True)]
            if names:
                for _err in cli.remove_objects(bucket, names):
                    logger.warning("MinIO 删除失败 %s: %s", bucket, _err)
            per_bucket[bucket] = len(names)
        except Exception as exc:
            logger.warning("会话 %s 产物清理失败（桶 %s）：%s", sid, bucket, exc)
            per_bucket[bucket] = -1
    total = sum(v for v in per_bucket.values() if v > 0)
    logger.info("会话 %s CAD 产物清理完成：%s", sid, per_bucket)
    return {"session_id": sid, "minio_objects_removed": total, "per_bucket": per_bucket}
