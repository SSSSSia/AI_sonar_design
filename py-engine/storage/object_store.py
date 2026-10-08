"""MinIO 对象存储封装（T1.5，D-10）。

桶：step-models（STEP 产物）/ cad-scripts（生成脚本）/ kb-docs（知识库原文件，T3.1）。
IR 中只存引用 {bucket, object_key[, sha256]}（P3 §8.3：不直接传递大对象）；
T14.5 起 artifact 上传附带内容 SHA-256——历史产物不可覆盖、可独立核对（审查问题 4a）。
"""

from __future__ import annotations

import hashlib
import io
import logging
from pathlib import Path

from minio import Minio

from app_config import (
    BUCKET_CAD_SCRIPTS,
    BUCKET_CAD_TUNES,
    BUCKET_KB_DOCS,
    BUCKET_KNOWLEDGE_DUMPS,
    BUCKET_STEP_MODELS,
    MINIO_ACCESS_KEY,
    MINIO_ENDPOINT,
    MINIO_SECRET_KEY,
)

logger = logging.getLogger("sonarcad.objectstore")

_client: Minio | None = None
_buckets_ready = False


def _get_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=False,
        )
    return _client


def _ensure_buckets() -> None:
    global _buckets_ready
    if _buckets_ready:
        return
    c = _get_client()
    for bucket in (BUCKET_STEP_MODELS, BUCKET_CAD_SCRIPTS, BUCKET_KB_DOCS, BUCKET_CAD_TUNES, BUCKET_KNOWLEDGE_DUMPS):
        if not c.bucket_exists(bucket):
            c.make_bucket(bucket)
            logger.info("MinIO 桶已创建: %s", bucket)
    _buckets_ready = True


def upload_artifact(local_path: Path, bucket: str, object_key: str) -> dict[str, str]:
    """上传文件，返回 IR 文件引用 {bucket, object_key, sha256}（T14.5 增内容哈希）。"""
    _ensure_buckets()
    c = _get_client()
    c.fput_object(bucket, object_key, str(local_path))
    sha = hashlib.sha256(local_path.read_bytes()).hexdigest()
    logger.info("产物已上传 %s/%s (%s 字节, sha256=%s)",
                bucket, object_key, local_path.stat().st_size, sha[:12])
    return {"bucket": bucket, "object_key": object_key, "sha256": sha}


def upload_bytes(data: bytes, bucket: str, object_key: str, content_type: str = "application/octet-stream") -> dict[str, str]:
    """上传字节内容（T3.1 KB 文档原文件）。返回引用附内容哈希（T14.5）。"""
    _ensure_buckets()
    c = _get_client()
    c.put_object(bucket, object_key, io.BytesIO(data), length=len(data), content_type=content_type)
    return {"bucket": bucket, "object_key": object_key, "sha256": hashlib.sha256(data).hexdigest()}


def upload_text(text: str, bucket: str, object_key: str) -> dict[str, str]:
    return upload_bytes(text.encode("utf-8"), bucket, object_key, content_type="text/plain")


def remove_object(bucket: str, object_key: str) -> None:
    """删除对象（T3.1 文档删除同步清理，§6 ⑦）。对象不存在时静默（幂等）。"""
    _ensure_buckets()
    _get_client().remove_object(bucket, object_key)
