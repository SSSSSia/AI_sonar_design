"""参考代码范例库管理 API（T16.1 B1，契约=ADR-015）。

路由前缀 /kb/references（经 gateway /api/v1/kb/** 转发，零网关改动；表 ref_scripts=biz V21）。
- GET  列表（全版本登记，ref_id 过滤可选）                        require_user
- POST 上传登记新脚本/新版本（multipart）                          require_admin → pending
- PATCH /{row_id}  元数据编辑 + 启停（active↔disabled）           require_admin
- POST /{row_id}/confirm  人工确认入库（pending→active+单活互斥）  require_admin
- POST /import-catalog  文件源幂等导入（hash/内容/状态一致验收载体） require_admin
- GET  /{row_id}/content  正文查看                                require_user

无 DELETE——「回退不删除历史版本/批准证据」（DEV-001 §1.4 B1 回退口径），撤回走状态。
状态机：新登记一律 pending（A5「只产 pending、绝不直写」同构）；pending 激活只能走 confirm。
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from api.authz import require_admin, require_user
from storage import object_store, ref_store

logger = logging.getLogger("sonarcad.refscripts")

router = APIRouter(prefix="/kb/references", dependencies=[Depends(require_user)])

# 范例脚本上传上限（脚本体量小，5MB 足够；独立于 KB 文档 50MB 口径）
MAX_SCRIPT_BYTES = 5 * 1024 * 1024


class PatchRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    domain: str | None = None
    domain_rules: str | None = None
    sonar_types: list[str] | None = None
    keywords: list[str] | None = None
    status: str | None = None  # active|disabled；pending → active 必须走 /confirm


class ImportRequest(BaseModel):
    dir: str | None = None  # 缺省=当前文件源目录（REF_CODE_DIR 或受控包内回退）


def _resolve_row_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=409, detail=str(exc))
    logger.exception("范例库写操作失败: %s", exc)
    return HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


def _validate_ref_id(ref_id: str) -> str:
    rid = (ref_id or "").strip().lower()
    if not re.match(ref_store.REF_ID_RE, rid):
        raise HTTPException(
            status_code=400,
            detail=f"ref_id 须为小写字母/数字/_/- 组成的 slug（如 towed_array_system_twin），收到: {ref_id!r}",
        )
    return rid


# ----------------------------------------------------------------------
# 查询
# ----------------------------------------------------------------------
@router.get("")
def list_references(ref_id: str | None = Query(None)) -> dict[str, Any]:
    return {"rows": ref_store.list_rows(ref_id)}


@router.get("/{row_id}/content")
def get_reference_content(row_id: str) -> dict[str, Any]:
    row = ref_store.get_row(row_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"范例登记不存在: {row_id}")
    try:
        content = object_store.download_text(row["bucket"], row["object_key"])
    except Exception as exc:  # noqa: BLE001 - 对象读取失败如实上报（A-16）
        raise HTTPException(status_code=502, detail=f"范例对象读取失败: {type(exc).__name__}: {exc}") from exc
    return {
        "row_id": row["id"], "ref_id": row["ref_id"], "version": row["version"],
        "file_name": row["file_name"], "content": content,
        "content_sha256": row["content_sha256"],
        "sha256_match": hashlib.sha256(content.encode("utf-8")).hexdigest() == row["content_sha256"],
    }


# ----------------------------------------------------------------------
# 登记（manual_upload）
# ----------------------------------------------------------------------
@router.post("")
def upload_reference(
    file: UploadFile = File(...),
    ref_id: str | None = Form(None),
    title: str | None = Form(None),
    description: str | None = Form(None),
    domain: str | None = Form(None),
    domain_rules: str | None = Form(None),
    sonar_types: list[str] = Form(default=[]),
    keywords: list[str] = Form(default=[]),
    user: str = Depends(require_admin),
) -> dict[str, Any]:
    """上传登记：sha256(文本 utf-8)（与检索文件源同口径）→ MinIO ref-scripts 桶 → pending 待确认。"""
    filename = Path(file.filename or "unnamed").name
    if not filename.endswith(".py"):
        raise HTTPException(status_code=400, detail="范例脚本须为 .py 文件")
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    if len(data) > MAX_SCRIPT_BYTES:
        raise HTTPException(status_code=413, detail=f"脚本超过大小上限 {MAX_SCRIPT_BYTES} 字节")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="脚本须为 UTF-8 文本") from exc

    rid = _validate_ref_id(ref_id or Path(filename).stem)
    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    existing = ref_store.find_by_content(rid, sha)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"ref_id={rid} 下相同内容已登记（版本 {existing['version']}，状态 {existing['status']}）——不重复登记",
        )

    from app_config import BUCKET_REF_SCRIPTS

    version = ref_store.next_version(rid)
    object_key = f"{rid}/v{version}/{filename}"
    object_store.upload_bytes(data, BUCKET_REF_SCRIPTS, object_key, content_type="text/x-python")
    try:
        row = ref_store.insert_row(
            ref_id=rid, version=version, file_name=filename, title=title, description=description,
            domain=domain, domain_rules=domain_rules, sonar_types=list(sonar_types),
            keywords=list(keywords), bucket=BUCKET_REF_SCRIPTS, object_key=object_key,
            content_sha256=sha, status="pending", source_type="manual_upload",
            source_note=f"上传登记（原文件名 {filename}）", created_by=user,
        )
    except Exception as exc:
        if exc.__class__.__name__ == "UniqueViolation":
            raise HTTPException(status_code=409, detail="并发登记冲突（版本号已被占用），请重试") from exc
        raise _resolve_row_error(exc) from exc
    logger.info("范例脚本已登记 %s v%s（pending）by %s", rid, version, user)
    return {"row": row}


# ----------------------------------------------------------------------
# 元数据 + 启停 + 确认
# ----------------------------------------------------------------------
@router.patch("/{row_id}")
def patch_reference(row_id: str, req: PatchRequest, user: str = Depends(require_admin)) -> dict[str, Any]:
    current = ref_store.get_row(row_id)
    if current is None:
        raise HTTPException(status_code=404, detail=f"范例登记不存在: {row_id}")
    if req.status and current["status"] == "pending" and req.status == "active":
        raise HTTPException(status_code=409, detail="pending 版本须经确认端点激活（POST /confirm）")
    try:
        row = ref_store.update_meta(row_id, req.model_dump(exclude_unset=True))
    except (KeyError, ValueError) as exc:
        raise _resolve_row_error(exc) from exc
    _sync_vector_on_transition(current, row)
    return {"row": row}


def _sync_vector_on_transition(before: dict[str, Any], after: dict[str, Any]) -> None:
    """T17.4（ADR-016 §4）：启停迁移的向量同步（flag 门控，best-effort 不阻塞 API）。

    active 化（confirm 语义外=disabled 重新启用）→ embed+upsert；停用 → 删向量
    （active-only：向量集合只反映 active 行）。"""
    import app_config

    if not app_config.REF_VECTOR_ENABLED:
        return
    if before.get("status") == after.get("status"):
        return
    try:
        from cad_engine.ref_vector import delete_ref_vector, sync_ref_vector
        from storage import object_store

        if after["status"] == "active":
            content = object_store.download_text(after["bucket"], after["object_key"])
            ok = sync_ref_vector(after, content)
            if not ok:
                logger.warning("范例向量同步失败（检索纯词法降级，回填脚本可补）: %s", after["ref_id"])
        else:
            delete_ref_vector(after["ref_id"])
    except Exception as exc:  # noqa: BLE001 - 向量同步失败不影响启停结果（A-16 如实日志）
        logger.warning("范例向量迁移同步异常（不阻塞）: %s: %s", after.get("ref_id"), exc)


@router.post("/{row_id}/confirm")
def confirm_reference(row_id: str, user: str = Depends(require_admin)) -> dict[str, Any]:
    """人工确认入库（pending→active）；同 ref_id 其余 active 行自动转 disabled（单活互斥）。"""
    try:
        row = ref_store.confirm_row(row_id)
    except (KeyError, ValueError) as exc:
        raise _resolve_row_error(exc) from exc
    logger.info("范例已确认激活 %s v%s by %s（同 ref_id 旧 active 已停用）", row["ref_id"], row["version"], user)
    # T17.4（ADR-016 §4）：confirm 时 embed（三来源 manual_upload/import/deposit 汇合点；
    # flag 门控 best-effort，失败不阻塞入库——「失败如实降级为纯词法」）
    import app_config

    if app_config.REF_VECTOR_ENABLED:
        try:
            from cad_engine.ref_vector import sync_ref_vector
            from storage import object_store

            content = object_store.download_text(row["bucket"], row["object_key"])
            if not sync_ref_vector(row, content):
                logger.warning("范例向量未写入（检索纯词法降级，回填脚本可补）: %s", row["ref_id"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("范例向量同步异常（不阻塞入库）: %s: %s", row.get("ref_id"), exc)
    return {"row": row}


# ----------------------------------------------------------------------
# 文件源导入（catalog.yaml + *.py → DB，幂等）
# ----------------------------------------------------------------------
@router.post("/import-catalog")
def import_catalog(req: ImportRequest | None = None, user: str = Depends(require_admin)) -> dict[str, Any]:
    """把当前文件源（受控包/外置目录）幂等导入 DB：内容同→跳过（unchanged）、变更→新版本 pending。

    返回逐条 hash/版本/状态对照报告——DEV-001 §1.4 B1「文件迁移后 hash/内容/状态一致」验收载体；
    导入不自动确认（pending→人工 confirm，A5 同构语义）。
    """
    import app_config
    from cad_engine.reference import DEFAULT_REF_DIR

    explicit = req.dir if req is not None else None
    dir_path = Path(explicit) if explicit else (
        Path(app_config.REF_CODE_DIR) if app_config.REF_CODE_DIR else DEFAULT_REF_DIR
    )
    try:
        raw = yaml.safe_load((dir_path / "catalog.yaml").read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("catalog.yaml 顶层不是映射")
        entries = raw.get("reference_files") or []
    except Exception as exc:  # noqa: BLE001 - 文件源不可用/坏 YAML 如实 400
        raise HTTPException(status_code=400, detail=f"文件源目录不可用或 catalog.yaml 无效: {exc}") from exc

    from app_config import BUCKET_REF_SCRIPTS

    catalog_version = str(raw.get("version") or "")
    items: list[dict[str, Any]] = []
    created = unchanged = 0
    for entry in entries:
        file_name = str(entry.get("file") or "")
        ref_id = str(entry.get("id") or Path(file_name).stem)
        item: dict[str, Any] = {"ref_id": ref_id, "file": file_name}
        try:
            rid = _validate_ref_id(ref_id)
            content = (dir_path / file_name).read_text(encoding="utf-8")
            sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
            existing = ref_store.find_by_content(rid, sha)
            if existing is not None:
                item.update({"action": "unchanged", "row_id": existing["id"],
                             "version": existing["version"], "status": existing["status"],
                             "content_sha256": sha})
                unchanged += 1
            else:
                version = ref_store.next_version(rid)
                object_key = f"{rid}/v{version}/{file_name}"
                object_store.upload_text(content, BUCKET_REF_SCRIPTS, object_key)
                row = ref_store.insert_row(
                    ref_id=rid, version=version, file_name=file_name,
                    title=None, description=entry.get("description"),
                    domain=entry.get("domain"), domain_rules=entry.get("domain_rules"),
                    sonar_types=list(entry.get("sonar_types") or []),
                    keywords=list(entry.get("keywords") or []),
                    bucket=BUCKET_REF_SCRIPTS, object_key=object_key, content_sha256=sha,
                    status="pending", source_type="catalog_import",
                    source_note=f"catalog v{catalog_version} @ {dir_path}", created_by=user,
                )
                item.update({"action": "created", "row_id": row["id"], "version": version,
                             "status": row["status"], "content_sha256": sha})
                created += 1
        except Exception as exc:  # noqa: BLE001 - 单条失败不中断批量导入，逐条如实报告
            logger.warning("范例导入失败 ref_id=%s: %s", ref_id, exc)
            item.update({"action": "error", "error": f"{type(exc).__name__}: {exc}"})
        items.append(item)
    logger.info("文件源导入完成 dir=%s created=%s unchanged=%s by %s", dir_path, created, unchanged, user)
    return {"source_dir": str(dir_path), "catalog_version": catalog_version,
            "items": items, "created": created, "unchanged": unchanged}


# ----------------------------------------------------------------------
# B3 候选沉淀（T16.3，ADR-015 v1.1 §9）：真实 gate2 批准 → pending 范例候选
# ----------------------------------------------------------------------
@router.get("/deposit-candidates")
def list_deposit_candidates(limit: int = Query(50, ge=1, le=200),
                            user: str = Depends(require_admin)) -> dict[str, Any]:
    """扫描可沉淀的 gate2 批准会话（判定链+组合校验+质量门+幂等对照，廉价预检不做重放）。"""
    from services import deposit as deposit_svc

    out = deposit_svc.scan_candidates(limit=limit)
    logger.info("沉淀候选扫描 by %s：candidates=%d blocked=%d",
                user, len(out["candidates"]), len(out["blocked"]))
    return out


class DepositRequest(BaseModel):
    session_id: str
    record_id: str


@router.post("/deposit")
def deposit_reference(req: DepositRequest, user: str = Depends(require_admin)) -> dict[str, Any]:
    """执行沉淀：复核 → 真沙箱重放（失败不落行）→ 脱敏 → 桶上传 → pending 行+凭证。

    幂等：同内容已登记 → 200 already=true + 既有行（「重复事件不重复入库」）；
    不可沉淀 → 409 reason 如实（判定链/质量门/重放/脱敏，ADR-015 §9 条 1-4）。
    """
    from services import deposit as deposit_svc

    try:
        out = deposit_svc.deposit_from_record(req.session_id, req.record_id, user)
    except deposit_svc.DepositError as exc:
        detail = {"reason": exc.reason}
        if exc.detail is not None:
            detail["detail"] = exc.detail
        raise HTTPException(status_code=409, detail=detail) from exc
    logger.info("沉淀完成 session=%s record=%s → %s（already=%s）by %s",
                req.session_id, req.record_id, out["ref_id"], out["already"], user)
    return out
