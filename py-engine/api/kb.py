"""知识库治理 API（T3.1–T3.4，宪法 §6）。

路由前缀 /kb（经 gateway /api/v1/kb/** 转发）。端点分组：
- T3.1 文档：POST /documents（上传+同步解析）、GET 列表/详情、DELETE（同步清理 §6 ⑦）
- T3.2 管线：chunk / extract / extractions 预览与裁决 / build 异步任务
- T3.3 资产：formulas / constraints / standards 三库 CRUD+启停、llm-providers 管理
- T3.4 图谱：GET /graph、GET /graph/neighborhood
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

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
        import app_config
        from kb.ocr import OCR_MAX_PAGES, make_page_ocr

        parsed = parsers.parse_document(
            filename, data,
            ocr_page=make_page_ocr(),            # OCR 插件（D-14 落地）：未启用/无供应商时为 None
            ocr_max_pages=OCR_MAX_PAGES,         # 页数预算护栏，超出如实 pending
        )
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

    if parsed.ocr_pending:
        note = f"{parsed.ocr_pending} 页扫描件未识别（OCR 失败或超出单文档页数预算），已如实计数"
    elif parsed.ocr_pages:
        note = f"{parsed.ocr_pages} 页扫描件已经 PaddleOCR-VL 识别（D-14 插件）"
    else:
        note = None
    return {
        "doc_id": doc_id, "filename": filename, "format": fmt,
        "status": "parsed", "units": len(parsed.units),
        "page_count": parsed.page_count or None,
        "section_count": parsed.section_count or None,
        "ocr_pages": parsed.ocr_pages,
        "ocr_pending": parsed.ocr_pending,
        "note": note,
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

    # 向量与图谱挂载点清理（模块缺失/不可达则如实标注，§6 ⑦）
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


# ----------------------------------------------------------------------
# T3.2 管线：分块 / 抽取 / 构建
# ----------------------------------------------------------------------
class ChunkRequest(BaseModel):
    size: int | None = None      # 缺省 KB_CHUNK_SIZE
    overlap: int | None = None   # 缺省 KB_CHUNK_OVERLAP


@router.post("/documents/{doc_id}/chunk")
def chunk_document(doc_id: str, req: ChunkRequest | None = None,
                   user: str = Depends(require_writer)) -> dict:
    """解析单元 → 可配置分块（§6 ③，章节优先/重叠可调）→ kb_chunks。"""
    import app_config
    from kb.chunker import chunk_units
    from kb.parsers import ParsedUnit

    doc = kb_store.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    units_raw = doc.get("parsed_units")
    if not units_raw:
        raise HTTPException(status_code=409, detail="文档无解析结果（先完成解析）")
    units = [ParsedUnit(location=u["location"], text=u["text"]) for u in units_raw]
    size = (req.size if req and req.size else app_config.KB_CHUNK_SIZE)
    overlap = (req.overlap if req and req.overlap is not None else app_config.KB_CHUNK_OVERLAP)
    chunks = chunk_units(units, size=size, overlap=overlap)
    n = kb_store.replace_chunks(doc_id, chunks)
    kb_store.update_document(doc_id, status="chunked")
    return {"doc_id": doc_id, "chunks": n, "size": size, "overlap": overlap}


class ExtractRequest(BaseModel):
    entity_types: list[str] | None = None  # 缺省读 kb/settings.yaml entity_types


@router.post("/documents/{doc_id}/extract")
def extract_document(doc_id: str, req: ExtractRequest | None = None,
                     user: str = Depends(require_writer)) -> dict:
    """LLM 本体抽取（§6 ④）——**异步任务**（大文档 chunk 多、LLM 耗时长，同步必超时）。

    返回 task_id；结果（pending）落 kb_extractions，经预览 API 人工确认后才入图。
    """
    from worker.tasks import kb_extract_task

    doc = kb_store.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    if not kb_store.get_chunks(doc_id):
        raise HTTPException(status_code=409, detail="文档无 chunks（先调用分块接口）")
    task = kb_extract_task.delay(doc_id, req.entity_types if req else None)
    return {"doc_id": doc_id, "task_id": task.id, "state": task.state,
            "note": "抽取完成后经 GET /documents/{doc_id}/extractions 预览（pending）"}


@router.get("/extract/{task_id}")
def extract_status(task_id: str) -> dict:
    from worker.celery_app import celery_app

    result = celery_app.AsyncResult(task_id)
    payload: dict = {"task_id": task_id, "state": result.state}
    if result.successful():
        payload["result"] = result.result
    elif result.failed():
        payload["error"] = str(result.result)
    return payload


@router.get("/documents/{doc_id}/extractions")
def list_extractions(doc_id: str, status: str | None = None) -> dict:
    """抽取预览（§6 ④ 人工审）。"""
    if status and status not in ("pending", "confirmed", "rejected"):
        raise HTTPException(status_code=400, detail="status 只允许 pending|confirmed|rejected")
    return {"extractions": kb_store.get_extractions(doc_id, status)}


class ExtractionDecision(BaseModel):
    status: str  # confirmed | rejected


@router.patch("/extractions/{extraction_id}")
def decide_extraction(extraction_id: int, req: ExtractionDecision,
                      user: str = Depends(require_writer)) -> dict:
    """人工裁决抽取条目（§6 ④）：confirmed 才有资格进图谱层。"""
    try:
        row = kb_store.set_extraction_status(extraction_id, req.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"抽取条目不存在: {extraction_id}")
    return {"id": extraction_id, "status": row["status"]}


class BuildRequest(BaseModel):
    doc_ids: list[str] | None = None  # 缺省=所有未 indexed 文档（增量语义，§6 ⑥）


@router.post("/build")
def trigger_build(req: BuildRequest | None = None, user: str = Depends(require_writer)) -> dict:
    """触发异步索引构建（§6 ⑤）：进度经任务状态 + 文档 build_detail 可查。"""
    from worker.tasks import kb_build_task

    task = kb_build_task.delay(req.doc_ids if req else None)
    return {"task_id": task.id, "state": task.state,
            "note": "构建三层（向量/图谱/语料）逐层如实记录，结果见 /kb/build/{task_id}"}


@router.get("/build/{task_id}")
def build_status(task_id: str) -> dict:
    from worker.celery_app import celery_app

    result = celery_app.AsyncResult(task_id)
    payload: dict = {"task_id": task_id, "state": result.state}
    if result.successful():
        payload["result"] = result.result
    elif result.failed():
        payload["error"] = str(result.result)
    return payload


# ----------------------------------------------------------------------
# T3.3 知识资产库：公式 / 约束 / 军标（录入/启停/可信度分层，§6 + 5.14）
# ----------------------------------------------------------------------
@router.get("/registry/{kind}")
def list_registry(kind: str) -> dict:
    if kind not in ("formulas", "constraints", "standards"):
        raise HTTPException(status_code=404, detail=f"未知资产库: {kind}")
    from storage import registry_store

    return {"kind": kind, "rows": registry_store.list_rows(kind)}


class RegistryRow(BaseModel):
    fields: dict


@router.post("/registry/{kind}")
def create_registry_row(kind: str, req: RegistryRow,
                        user: str = Depends(require_writer)) -> dict:
    from solver import standards

    if kind not in ("formulas", "constraints", "standards"):
        raise HTTPException(status_code=404, detail=f"未知资产库: {kind}")
    from storage import registry_store

    try:
        row = registry_store.create_row(kind, req.fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    standards.reset_cache()  # 军标库变更即时生效（A4 热路径缓存）
    return {"row": row}


@router.patch("/registry/{kind}/{row_id}")
def update_registry_row(kind: str, row_id: int, req: RegistryRow,
                        user: str = Depends(require_writer)) -> dict:
    from solver import standards

    if kind not in ("formulas", "constraints", "standards"):
        raise HTTPException(status_code=404, detail=f"未知资产库: {kind}")
    from storage import registry_store

    try:
        row = registry_store.update_row(kind, row_id, req.fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"条目不存在: {row_id}")
    standards.reset_cache()
    return {"row": row}


@router.delete("/registry/{kind}/{row_id}")
def delete_registry_row(kind: str, row_id: int, user: str = Depends(require_writer)) -> dict:
    from solver import standards

    if kind not in ("formulas", "constraints", "standards"):
        raise HTTPException(status_code=404, detail=f"未知资产库: {kind}")
    from storage import registry_store

    deleted = registry_store.delete_row(kind, row_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"条目不存在: {row_id}")
    standards.reset_cache()
    return {"deleted": True, "id": row_id}


# ----------------------------------------------------------------------
# T3.3 LLM 供应商管理（5.16，D-12 数据面；api_key 只写不读——序列化一律脱敏）
# ----------------------------------------------------------------------
@router.get("/llm-providers")
def list_llm_providers() -> dict:
    from storage import registry_store

    return {"providers": registry_store.list_providers()}  # 不含 api_key 字段


class ProviderCreate(BaseModel):
    name: str
    base_url: str
    api_key: str           # 只写不读
    model: str
    priority: int = 0
    timeout_s: int = 300
    enabled: bool = True


@router.post("/llm-providers")
def create_llm_provider(req: ProviderCreate, user: str = Depends(require_writer)) -> dict:
    from llm.adapter import reload_providers
    from storage import registry_store

    row = registry_store.create_provider(
        name=req.name, base_url=req.base_url, api_key=req.api_key, model=req.model,
        priority=req.priority, timeout_s=req.timeout_s, enabled=req.enabled,
    )
    reload_providers()  # D-12 热切换
    return {"provider": row, "reloaded": True}


class ProviderPatch(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    priority: int | None = None
    timeout_s: int | None = None
    enabled: bool | None = None


@router.patch("/llm-providers/{name}")
def update_llm_provider(name: str, req: ProviderPatch,
                        user: str = Depends(require_writer)) -> dict:
    from llm.adapter import reload_providers
    from storage import registry_store

    try:
        row = registry_store.update_provider(name, req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"供应商不存在: {name}")
    reload_providers()
    return {"provider": row, "reloaded": True}


@router.delete("/llm-providers/{name}")
def delete_llm_provider(name: str, user: str = Depends(require_writer)) -> dict:
    from llm.adapter import reload_providers
    from storage import registry_store

    deleted = registry_store.delete_provider(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"供应商不存在: {name}")
    reload_providers()
    return {"deleted": True, "name": name, "reloaded": True}


# ----------------------------------------------------------------------
# T3.4 图谱可视化数据接口（力导向全图 + 邻域子图；节点带来源 doc 可反跳）
# ----------------------------------------------------------------------
def _graph_store():
    from storage.neo4j_store import Neo4jStore

    store = Neo4jStore()
    if not store.available():
        raise HTTPException(status_code=503, detail="Neo4j 不可达，图谱查询未执行（如实降级）")
    return store


@router.get("/graph")
def graph(limit: int = 300) -> dict:
    return _graph_store().graph_sample(limit=min(max(limit, 1), 500))


@router.get("/graph/neighborhood")
def graph_neighborhood(key: str, depth: int = 2, limit: int = 100) -> dict:
    return _graph_store().neighborhood(key=key, depth=depth, limit=min(max(limit, 1), 300))


@router.get("/status")
def kb_status(user: str = Depends(require_user)) -> dict[str, Any]:
    """知识库现状诚实上报（网关可达版 /knowledge/status，T4.4 系统管理页用）。"""
    from knowledge.service import get_knowledge_service
    from storage import kb_store

    svc = get_knowledge_service()
    status: dict[str, Any] = {
        "kb_levels_enabled": svc.enable_kb_levels,
        "graphrag_sdk": svc.sdk is not None,
        "dynamic_corpus_chunks": len(svc._dynamic_sources),
    }
    status.update(kb_store.doc_stats())
    if svc.enable_kb_levels:
        try:
            from kb.vector_store import ChunkVectorStore

            status["milvus_available"] = ChunkVectorStore().available()
        except Exception:
            status["milvus_available"] = False
        try:
            from storage.neo4j_store import Neo4jStore

            status["neo4j_available"] = Neo4jStore().available()
        except Exception:
            status["neo4j_available"] = False
    return status
