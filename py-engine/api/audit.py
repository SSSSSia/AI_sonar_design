"""py-engine 审计中间件规则表（T13.1，§9.4）。

按 (method, path 正则) 匹配需留痕的端点：KB 写端点 + 导出端点（§9.4 口径中
py-engine 承担的操作域）。登录/用户/项目/会话/裁决留痕由 Java 各服务 AuditRecorder
插桩（同一张 audit_logs 表）。E1 参数变更/微调/IR1 修订不在此列——已有
cad_param_updates / cad_tune_* / ir_snapshots 专表留痕（A-12 溯源体系，PHASE-013 §2.1）。

detail 刻意不落请求体（llm-providers body 含 api_key 等，宪法 §10），只落查询参数摘要
与响应码；action 命名 `域.动作`。AUDIT_LOG_ENABLED=0 可整体关闭（逃生门）。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger("sonarcad.audit")

ENABLED = os.environ.get("AUDIT_LOG_ENABLED", "1").strip().lower() not in ("0", "false", "no")

# (method, path 正则, action, resource_type, resource_id 捕获组名或 None)
_RULES: list[tuple[str, re.Pattern[str], str, str | None, str | None]] = [
    # KB 文档域
    ("POST", re.compile(r"^/api/v1/kb/documents$"), "kb.upload", "kb_document", None),
    ("DELETE", re.compile(r"^/api/v1/kb/documents/(?P<rid>[^/]+)$"), "kb.delete_document", "kb_document", "rid"),
    ("POST", re.compile(r"^/api/v1/kb/documents/(?P<rid>[^/]+)/chunk$"), "kb.chunk", "kb_document", "rid"),
    ("POST", re.compile(r"^/api/v1/kb/documents/(?P<rid>[^/]+)/extract$"), "kb.extract", "kb_document", "rid"),
    ("PATCH", re.compile(r"^/api/v1/kb/extractions/(?P<rid>[^/]+)$"), "kb.extraction_decide", "kb_extraction", "rid"),
    ("POST", re.compile(r"^/api/v1/kb/build$"), "kb.build", None, None),
    # 多库归属域
    ("POST", re.compile(r"^/api/v1/kb/bases$"), "kb.create_base", "kb_base", None),
    ("PATCH", re.compile(r"^/api/v1/kb/bases/(?P<rid>[^/]+)$"), "kb.update_base", "kb_base", "rid"),
    ("DELETE", re.compile(r"^/api/v1/kb/bases/(?P<rid>[^/]+)$"), "kb.delete_base", "kb_base", "rid"),
    ("POST", re.compile(r"^/api/v1/kb/bases/(?P<rid>[^/]+)/docs$"), "kb.base_add_docs", "kb_base", "rid"),
    ("DELETE", re.compile(r"^/api/v1/kb/bases/(?P<rid>[^/]+)/docs$"), "kb.base_remove_docs", "kb_base", "rid"),
    # 三库 registry 域（batch/import 路由更长，先于 {kind} 单段规则判断无冲突：段数不同）
    ("POST", re.compile(r"^/api/v1/kb/registry/(?P<kind>[a-z_]+)$"), "kb.registry_create", "kb_registry", None),
    ("PATCH", re.compile(r"^/api/v1/kb/registry/(?P<kind>[a-z_]+)/(?P<rid>[^/]+)$"), "kb.registry_update", "kb_registry", "rid"),
    ("DELETE", re.compile(r"^/api/v1/kb/registry/(?P<kind>[a-z_]+)/(?P<rid>[^/]+)$"), "kb.registry_delete", "kb_registry", "rid"),
    ("POST", re.compile(r"^/api/v1/kb/registry/(?P<kind>[a-z_]+)/batch$"), "kb.registry_batch", "kb_registry", None),
    ("POST", re.compile(r"^/api/v1/kb/registry/(?P<kind>[a-z_]+)/import$"), "kb.registry_import", "kb_registry", None),
    # LLM 供应商域（管理面；detail 不落 body——含 api_key）
    ("POST", re.compile(r"^/api/v1/kb/llm-providers$"), "kb.create_provider", "llm_provider", None),
    ("PATCH", re.compile(r"^/api/v1/kb/llm-providers/(?P<rid>[^/]+)$"), "kb.update_provider", "llm_provider", "rid"),
    ("DELETE", re.compile(r"^/api/v1/kb/llm-providers/(?P<rid>[^/]+)$"), "kb.delete_provider", "llm_provider", "rid"),
    # 图谱快照（T11.7 备份链路的人工/定时触发留痕）
    ("POST", re.compile(r"^/api/v1/kb/graph/snapshot$"), "kb.graph_snapshot", None, None),
    # 导出（§9.4 明列「导出操作」；GET 但语义为交付物产出）
    ("GET", re.compile(r"^/api/v1/cad/sessions/(?P<rid>[^/]+)/export/docx$"), "export.docx", "session", "rid"),
]

_MATCH_CACHE: dict[tuple[str, str], tuple[str, str | None, str | None, dict[str, str]] | None] = {}


def match_rule(method: str, path: str) -> tuple[str, str | None, str | None, dict[str, str]] | None:
    """命中返回 (action, resource_type, resource_id, groups)，未命中 None（带缓存）。"""
    key = (method, path)
    if key in _MATCH_CACHE:
        return _MATCH_CACHE[key]
    hit: tuple[str, str | None, str | None, dict[str, str]] | None = None
    for m, pattern, action, rtype, group in _RULES:
        if m != method:
            continue
        matched = pattern.match(path)
        if matched:
            rid = matched.group(group) if group else None
            hit = (action, rtype, rid, matched.groupdict())
            break
    if len(_MATCH_CACHE) > 4096:
        _MATCH_CACHE.clear()
    _MATCH_CACHE[key] = hit
    return hit


def record_request(
    method: str,
    path: str,
    headers: Any,
    client_host: str | None,
    query_params: dict[str, str],
    status: int,
) -> None:
    """命中规则则落一行审计（同步 psycopg 调用方负责丢线程池）。永不抛出——审计是旁路。"""
    try:
        hit = match_rule(method, path)
        if hit is None:
            return
        action, rtype, rid, _groups = hit
        from storage import audit_store

        audit_store.append(
            user_id=_header(headers, "x-user-id"),
            username=None,  # py 侧信任头无用户名；查询端点 LEFT JOIN users 补齐
            action=action,
            resource_type=rtype,
            resource_id=rid,
            detail={"status": status, **({"query": query_params} if query_params else {})},
            ip=client_host,
        )
    except Exception as exc:  # noqa: BLE001 - 审计失败绝不阻断业务响应
        logger.warning("审计落库失败 %s %s（旁路忽略）: %s", method, path, exc)


def _header(headers: Any, name: str) -> str | None:
    """兼容 starlette Headers 与裸 (bytes, bytes) 序列。"""
    try:
        value = headers.get(name)
    except AttributeError:
        value = next((v.decode("latin-1") for k, v in headers if k.decode("latin-1").lower() == name), None)
    return value or None
