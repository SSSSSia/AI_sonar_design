"""知识库治理 API（T3.1–T3.4，宪法 §6）。

路由前缀 /kb（经 gateway /api/v1/kb/** 转发）。端点分组：
- T3.1 文档：POST /documents（上传+同步解析）、GET 列表/详情、DELETE（同步清理 §6 ⑦）
- T3.2 管线：chunk / extract / extractions 预览与裁决 / build 异步任务
- T3.3 资产：formulas / constraints / standards 三库 CRUD+启停、llm-providers 管理
- T3.4 图谱：GET /graph、GET /graph/neighborhood
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from api.authz import require_user, require_writer
from kb import parsers
from storage import kb_store

logger = logging.getLogger("sonarcad.kb")

router = APIRouter(prefix="/kb", dependencies=[Depends(require_user)])


# ----------------------------------------------------------------------
# T3.1 文档上传与解析
# ----------------------------------------------------------------------
@router.post("/documents")
def upload_document(
    file: UploadFile = File(...),
    uploaded_by: str = Depends(require_writer),
) -> dict:
    """上传即建档（§6 ①）→ MinIO 原文件桶 → 同步解析（带位置信息，§6 ②）。

    解析失败：doc 状态=failed + error 如实记录，不中断服务（其他文档不受影响）。
    """
    from app_config import BUCKET_KB_DOCS, KB_UPLOAD_MAX_BYTES
    from storage import object_store

    filename = file.filename or "unnamed"
    try:
        fmt = parsers.detect_format(filename)
    except parsers.UnsupportedFormatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    data = file.file.read()
    if len(data) > KB_UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"文件超过大小上限 {KB_UPLOAD_MAX_BYTES} 字节")
    if not data:
        raise HTTPException(status_code=400, detail="空文件")

    doc = kb_store.create_document(
        filename=filename, fmt=fmt, size_bytes=len(data),
        bucket=BUCKET_KB_DOCS, object_key=f"kb/{filename}", uploaded_by=uploaded_by,
    )
    doc_id = doc["doc_id"]
    object_store.upload_bytes(
        data, BUCKET_KB_DOCS, f"{doc_id}/{filename}",
        content_type=file.content_type or "application/octet-stream",
    )

    try:
        parsed = parsers.parse_document(filename, data)
        kb_store.update_document(
            doc_id,
            status="parsed",
            parsed_units=[{"location": u.location, "text": u.text} for u in parsed.units],
            page_count=parsed.page_count or None,
            section_count=parsed.section_count or None,
            ocr_pending=parsed.ocr_pending,
        )
    except Exception as exc:
        logger.exception("KB 文档解析失败 doc_id=%s（不中断服务，§6 ①）", doc_id)
        kb_store.update_document(doc_id, status="failed", error=f"{type(exc).__name__}: {exc}")
        return {"doc_id": doc_id, "filename": filename, "status": "failed",
                "error": f"{type(exc).__name__}: {exc}"}

    return {
        "doc_id": doc_id, "filename": filename, "format": fmt,
        "status": "parsed", "units": len(parsed.units),
        "page_count": parsed.page_count or None,
        "section_count": parsed.section_count or None,
        "ocr_pending": parsed.ocr_pending,
        "note": "扫描页 OCR 插件待接（D-14），已如实计数" if parsed.ocr_pending else None,
    }


@router.get("/documents")
def list_documents() -> dict:
    return {"documents": kb_store.list_documents()}


@router.get("/documents/{doc_id}")
def get_document(doc_id: str) -> dict:
    doc = kb_store.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    return {"document": doc}


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str, user: str = Depends(require_writer)) -> dict:
    """删除文档并同步清理（§6 ⑦）：MinIO 原件 + chunks/extractions（级联）+ 向量/图谱挂载点。"""
    from storage import object_store

    doc = kb_store.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    cleanup: dict = {"minio": False, "vectors": "skipped", "graph": "skipped"}
    try:
        object_store.remove_object(doc["bucket"], doc["object_key"])
        cleanup["minio"] = True
    except Exception as exc:
        logger.warning("KB 原件删除失败 doc_id=%s: %s", doc_id, exc)
        cleanup["minio"] = f"failed: {exc}"

    # 向量与图谱挂载点清理（T3.2 接入后生效；此处先尝试调用，模块缺失则如实跳过）
    try:
        from kb import index_build

        cleanup["vectors"] = index_build.delete_doc_vectors(doc_id)
    except ImportError:
        pass
    except Exception as exc:
        cleanup["vectors"] = f"failed: {exc}"
    try:
        from storage import neo4j_store

        store = neo4j_store.Neo4jStore()
        if store.available():
            cleanup["graph"] = store.deprecate_by_source_doc(doc_id)
    except Exception as exc:
        cleanup["graph"] = f"failed: {exc}"

    kb_store.delete_document(doc_id)
    return {"doc_id": doc_id, "deleted": True, "cleanup": cleanup}
