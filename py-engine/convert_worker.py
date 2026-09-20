# -*- coding: utf-8 -*-
"""STEP→GLB 转换子进程 worker（2026-09-20，GIL 隔离修复）。

背景：cadquery/trimesh 镶嵌是 CPU 密集的 C 扩展调用，此前在 uvicorn API 进程的
后台线程内执行——长转换把 GIL 占住，API 连 GET /gltf 轮询都响应不了，前端 30s
超时（2026-09-20 实机复现）。改为本模块独立子进程执行，API 进程只读状态：

  cad-previews/{sid}/{version}.glb     成功产物（存在即 ready）
  cad-previews/{sid}/{version}.status  状态标记 {"state","detail","ts"}，成功后删除

用法：python convert_worker.py <sid> <version> <bucket> <object_key>
退出码：0 成功 / 1 失败（失败原因同时写入状态标记，A-16 如实透出）。
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

PREVIEW_BUCKET = "cad-previews"
STALE_SECONDS = 1800  # converting 超 30 分钟无产出 → 判进程中断（实测 31MB GLB 级模型约 10.5 分钟，留足余量）


def status_key(sid: str, version: int) -> str:
    return f"{sid}/{version}.status"


def glb_key(sid: str, version: int) -> str:
    return f"{sid}/{version}.glb"


def is_stale(status: dict[str, Any] | None, now: float) -> bool:
    """converting 状态的超时判定（纯函数，单测覆盖）；缺失/非 converting/failed 永不判陈旧。"""
    if not status or status.get("state") != "converting":
        return False
    ts = status.get("ts")
    return not isinstance(ts, (int, float)) or (now - float(ts)) > STALE_SECONDS


def _ensure_bucket(cli) -> None:
    if not cli.bucket_exists(PREVIEW_BUCKET):
        cli.make_bucket(PREVIEW_BUCKET)


def write_status(cli, sid: str, version: int, payload: dict[str, Any]) -> None:
    body = json.dumps({**payload, "ts": time.time()}, ensure_ascii=False).encode("utf-8")
    cli.put_object(PREVIEW_BUCKET, status_key(sid, version), io.BytesIO(body), length=len(body),
                   content_type="application/json")


def convert_to_glb(step_bytes: bytes) -> bytes:
    """STEP → GLB：cadquery 镶嵌（容差随包围盒缩放）→ trimesh 组 GLB。只在子进程内运行。"""
    import cadquery as cq
    import trimesh

    tmp = os.path.join(tempfile.gettempdir(), f"cad_preview_{int(time.time() * 1000)}.step")
    with open(tmp, "wb") as f:
        f.write(step_bytes)
    try:
        shape = cq.importers.importStep(tmp).val()
        bb = shape.BoundingBox()
        diag = ((bb.xlen**2 + bb.ylen**2 + bb.zlen**2) ** 0.5) or 1000.0
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


def main(argv: list[str]) -> int:
    sid, version, bucket, key = argv[1], int(argv[2]), argv[3], argv[4]
    from storage.object_store import _get_client

    cli = _get_client()
    _ensure_bucket(cli)
    write_status(cli, sid, version, {"state": "converting", "detail": ""})
    try:
        step_bytes = cli.get_object(bucket, key).read()
        glb = convert_to_glb(step_bytes)
        cli.put_object(PREVIEW_BUCKET, glb_key(sid, version), io.BytesIO(glb), length=len(glb),
                       content_type="model/gltf-binary")
    except Exception as exc:  # 如实记录失败原因，IR3 产物本身不受影响
        write_status(cli, sid, version, {"state": "failed", "detail": str(exc)[:300]})
        print(f"cad 预览转换失败 sid={sid} v={version}: {exc}", file=sys.stderr)
        return 1
    try:
        cli.remove_object(PREVIEW_BUCKET, status_key(sid, version))
    except Exception:
        pass  # 标记残留无害：glb 存在即 ready
    print(f"cad 预览转换完成 sid={sid} v={version} glb={len(glb)}B", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
