# -*- coding: utf-8 -*-
"""MinIO 对象快照脚本（T11.7）：在 py-engine 容器内执行（复用 minio SDK 与内网凭据）。

download 模式：全桶对象下载 → /tmp/minio-snapshot.tar.gz（宿主经 docker cp 取走）。
restore 模式：--restore 从 tar 包回灌全部对象（幂等：put_object 覆盖同键）。
注意：容器内无 deploy/ 目录——backup.sh 经 docker cp 注入本脚本到 /app/ 下执行。
"""

from __future__ import annotations

import io
import os
import sys
import tarfile


def _client():
    from minio import Minio
    from app_config import MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY

    return Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=False)


def _buckets():
    from app_config import (BUCKET_CAD_SCRIPTS, BUCKET_CAD_TUNES, BUCKET_KB_DOCS,
                            BUCKET_KNOWLEDGE_DUMPS, BUCKET_STEP_MODELS)
    return [BUCKET_STEP_MODELS, BUCKET_CAD_SCRIPTS, BUCKET_KB_DOCS, BUCKET_CAD_TUNES,
            BUCKET_KNOWLEDGE_DUMPS]


def download() -> int:
    c = _client()
    total = 0
    with tarfile.open("/tmp/minio-snapshot.tar.gz", "w:gz") as tar:
        for bucket in _buckets():
            if not c.bucket_exists(bucket):
                continue
            for obj in c.list_objects(bucket, recursive=True):
                data = c.get_object(bucket, obj.object_name).read()
                info = tarfile.TarInfo(name=f"{bucket}/{obj.object_name}")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
                total += 1
    print(f"minio snapshot: {total} objects", file=sys.stderr)
    return total


def restore(tar_path: str) -> int:
    c = _client()
    total = 0
    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            bucket, _, key = member.name.partition("/")
            data = tar.extractfile(member).read()
            c.put_object(bucket, key, io.BytesIO(data), length=len(data))
            total += 1
    print(f"minio restore: {total} objects", file=sys.stderr)
    return total


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--restore":
        restore(sys.argv[2])
    else:
        download()