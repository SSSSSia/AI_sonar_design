"""T4.3：三维预览（STEP→GLB，异步转换 + MinIO 版本缓存）+ 交付物预签名下载。

设计要点（PHASE-004 §2.6；2026-09-20 修订）：
- 转换重（大装配 OCC 导入分钟级），两段式：POST /gltf/convert 触发；GET /gltf 查询状态/返回字节。
- **转换在独立子进程执行**（convert_worker.py）：cadquery/trimesh 是 CPU 密集 C 扩展，
  此前在 API 进程后台线程跑会把 GIL 占死，轮询请求被饿到前端 30s 超时（实机复现）。
- 状态经 MinIO 标记对象传递（cad-previews/{sid}/{version}.status），API 重启不丢；
  缓存键 cad-previews/{sid}/{version}.glb 命中直接回（同 IR 版本不重算）。
- 下载走 MinIO 预签名 URL（15 分钟），未登录 401；脚本交付物直接用 IR3 内容文本，不依赖对象存储。
- 失败如实 503（转换失败原因透出，A-16），前端保留 STEP 下载入口与重试。
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from api.authz import require_user
from app_config import BUCKET_STEP_MODELS
from convert_worker import PREVIEW_BUCKET, glb_key, is_stale, status_key
from storage import ir_store

logger = logging.getLogger("sonarcad.cad")

router = APIRouter(prefix="/cad", tags=["cad"])


def _ir3_or_404(sid: str) -> tuple[dict[str, Any], int]:
    ir3 = ir_store.get_latest_snapshot(sid, "IR3")
    if not ir3 or not ir3.get("step_file"):
        raise HTTPException(status_code=404, detail="IR3 尚未生成，无 CAD 产物")
    version = ir_store.get_latest_version(sid, "IR3")
    return ir3, version


def _minio():
    from storage.object_store import _get_client

    return _get_client()


def _read_status(sid: str, version: int) -> dict[str, Any] | None:
    """读子进程写的状态标记；缺失/损坏返回 None（视为 idle/未知）。"""
    try:
        data = _minio().get_object(PREVIEW_BUCKET, status_key(sid, version)).read()
        return json.loads(data)
    except Exception:
        return None


@router.post("/sessions/{sid}/gltf/convert")
def start_convert(sid: str, user: str = Depends(require_user)) -> dict[str, Any]:
    ir3, version = _ir3_or_404(sid)
    # 已缓存则直接 ready（幂等）
    try:
        _minio().stat_object(PREVIEW_BUCKET, glb_key(sid, version))
        return {"state": "ready", "version": version}
    except Exception:
        pass
    status = _read_status(sid, version)
    if status and status.get("state") == "converting" and not is_stale(status, time.time()):
        return {"state": "converting", "version": version}
    step = ir3["step_file"]
    subprocess.Popen(  # 独立子进程：GIL 隔离（2026-09-20 前台轮询 30s 超时的根因修复）
        [sys.executable, str(Path(__file__).resolve().parents[1] / "convert_worker.py"),
         sid, str(version), step["bucket"], step["object_key"]],
    )
    logger.info("cad 预览转换子进程已启动 sid=%s v=%s", sid, version)
    return {"state": "converting", "version": version, "note": "大装配首次转换可能需要数分钟，结果按版本缓存"}


@router.get("/sessions/{sid}/gltf")
def get_gltf(sid: str, user: str = Depends(require_user)) -> Response:
    ir3, version = _ir3_or_404(sid)
    try:
        data = _minio().get_object(PREVIEW_BUCKET, glb_key(sid, version)).read()
        return Response(
            content=data, media_type="model/gltf-binary",
            headers={"X-IR-Version": str(version), "Content-Disposition": f'inline; filename="ir3_v{version}.glb"'},
        )
    except Exception:
        pass
    status = _read_status(sid, version)
    if status and status.get("state") == "failed":
        raise HTTPException(
            status_code=503,
            detail=f"三维预览转换失败：{status.get('detail', '')}（可下载 STEP 查看，或重试转换）",
        )
    if is_stale(status, time.time()):
        raise HTTPException(
            status_code=503,
            detail="三维预览转换中断（超 15 分钟无产出）——请重新发起转换",
        )
    return Response(
        content='{"state": "converting"}', media_type="application/json", status_code=202,
        headers={"X-Convert-State": (status or {}).get("state", "idle")},
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

    - 覆盖 step-models / cad-scripts / cad-previews / cad-tunes（微调产物，T8.1；键布局均为 {sid}/xxx）。
    - Neo4j 已过审沉淀的知识**不在清理范围**（共享资产）；其 source_session 标记
      会随会话删除悬空，属如实保留（A-16：不静默伪造来源，也不连坐删知识）。
    - 清理尽力而为：逐桶独立容错，返回逐桶计数，单桶失败不拖垮整体。
    """
    from app_config import BUCKET_CAD_SCRIPTS, BUCKET_CAD_TUNES

    cli = _minio()
    per_bucket: dict[str, int] = {}
    for bucket in (BUCKET_STEP_MODELS, BUCKET_CAD_SCRIPTS, PREVIEW_BUCKET, BUCKET_CAD_TUNES):
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


@router.get("/sessions/{sid}/export/docx")
def export_docx(sid: str, user: str = Depends(require_user)) -> Response:
    """军标格式 Word 导出（T11.5，P2-11）：IR2 三章渲染（设计文档/参数表+溯源/校验结论）。"""
    import urllib.parse

    from docgen.export_docx import export_session_docx

    ir2 = ir_store.get_latest_snapshot(sid, "IR2")
    if ir2 is None:
        raise HTTPException(status_code=404, detail="IR2 尚未生成，无可导出的方案文档")
    ir2_version = ir_store.get_latest_version(sid, "IR2")
    ir3 = ir_store.get_latest_snapshot(sid, "IR3")
    ir4 = ir_store.get_latest_snapshot(sid, "IR4")
    title = ir_store.get_session_title(sid)
    session = {"id": sid, "title": title, **(ir_store.get_session(sid) or {})}
    data = export_session_docx(session, ir2, ir3, ir4, ir2_version)
    filename = f"设计方案_{title or sid}_IR2v{ir2_version}.docx"
    quoted = urllib.parse.quote(filename)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
    )
