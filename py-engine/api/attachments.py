"""会话参考图片附件上传（T18.5，ADR-017；gateway /api/v1/attachments/**）。

创建会话前直传：sha256 内容寻址（前 16 位前缀/文件名，重复上传幂等）→ MinIO
session-attachments 桶 → 返回 IR 引用 {bucket, object_key, sha256}（前端随创建请求
传给 workflow 写进 IR0 快照 attachments，契约=ir0.schema.json T18.5 加法演进）。
仅图片（content_type 前缀 image/）、≤5MB、≤上传端点逐张语义（数量上限在创建侧 Java 校验）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from api.authz import require_user
from storage import object_store

router = APIRouter(prefix="/attachments", tags=["attachments"])

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5MB/张（会话参考图，超限前端如实提示）


@router.post("/upload")
def upload_attachment(
    file: UploadFile = File(...),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """上传会话参考图片，返回 IR 引用（幂等：同内容同 object_key）。"""
    filename = Path(file.filename or "unnamed").name or "unnamed"
    content_type = file.content_type or ""
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="仅支持图片文件（image/*）")
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=422, detail="图片内容为空")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail=f"图片超过大小上限 {MAX_IMAGE_BYTES} 字节")

    sha = hashlib.sha256(data).hexdigest()
    object_key = f"{sha[:16]}/{filename}"
    ref = object_store.upload_bytes(data, object_store.BUCKET_SESSION_ATTACHMENTS,
                                    object_key, content_type=content_type)
    return {"bucket": ref["bucket"], "object_key": ref["object_key"], "sha256": sha}
