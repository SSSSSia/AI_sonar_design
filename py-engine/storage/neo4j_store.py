"""Neo4j 图谱存储（T2.3 A5 知识回写）。

边界（宪法 A-10）：本模块只被「知识审阅关人工确认后」的回写路径调用——
write_candidates 只接收确认名单内的候选，**未确认候选在代码路径上不可能入图**。

幂等策略：实体节点以 key=sha1(归一化名+内容摘要) 为 MERGE 键；同名不同内容的新知识
入库时，旧版本节点标 deprecated: true 并记 superseded_by（图谱不删实体，A-10）。
Neo4j 不可达时抛 Neo4jStoreError，由调用方如实上报（IR5 保持 pending 可重试，A-16）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from app_config import NEO4J_PASSWORD, NEO4J_TIMEOUT_S, NEO4J_URI, NEO4J_USER

logger = logging.getLogger("sonarcad.neo4j")

# 本体标签白名单（对齐 knowledge/kb/settings.yaml entity_types；未知类型兜底 Entity）
ALLOWED_LABELS = {
    "SonarSystem", "StructuralModule", "Component", "DesignParameter",
    "PerformanceIndicator", "DesignConstraint", "Algorithm",
    "CADModelingStandard", "DesignCase", "Entity",
}

# 关系候选默认兜底标签
ENTITY_LABEL = "Entity"


class Neo4jStoreError(RuntimeError):
    """Neo4j 连接或回写失败（调用方如实上报，绝不伪造成功）。"""


def _normalize(s: Any) -> str:
    return "".join(str(s or "").split()).lower()


def _content_key(*parts: Any) -> str:
    raw = "|".join(_normalize(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _safe_label(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "", str(label or ""))
    return cleaned if cleaned in ALLOWED_LABELS else ENTITY_LABEL


def _safe_rel_type(rel: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", str(rel or "").upper()).strip("_")
    return cleaned or "RELATED_TO"


class Neo4jStore:
    """neo4j driver 薄封装。driver 惰性创建；测试可注入 fake。"""

    def __init__(self, uri: str | None = None, user: str | None = None,
                 password: str | None = None, driver: Any = None):
        self._uri = uri or NEO4J_URI
        self._user = user or NEO4J_USER
        self._password = password if password is not None else NEO4J_PASSWORD
        self._driver = driver  # 注入入口（单测）

    # ------------------------------------------------------------------
    def _get_driver(self) -> Any:
        if self._driver is None:
            from neo4j import GraphDatabase

            auth = (self._user, self._password)
            try:
                # driver 6.x：connection_timeout；5.x 改名前为 connection_timeout_s
                self._driver = GraphDatabase.driver(
                    self._uri, auth=auth, connection_timeout=NEO4J_TIMEOUT_S,
                )
            except TypeError:
                self._driver = GraphDatabase.driver(
                    self._uri, auth=auth, connection_timeout_s=NEO4J_TIMEOUT_S,
                )
        return self._driver

    def available(self) -> bool:
        """连通性探测（快速失败）。"""
        try:
            self._get_driver().verify_connectivity()
            return True
        except Exception as exc:
            logger.warning("Neo4j 不可达: %s", exc)
            return False

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    # ------------------------------------------------------------------
    def find_duplicates(self, name: str) -> list[dict[str, Any]]:
        """图谱查重：同名实体（含已 deprecated 的历史版本）。"""
        with self._get_driver().session() as session:
            records = session.run(
                "MATCH (e) WHERE e.name = $name AND e.key IS NOT NULL "
                "RETURN labels(e)[0] AS label, e.key AS key, coalesce(e.deprecated, false) AS deprecated "
                "LIMIT 10",
                name=name,
            ).data()
        return records

    # ------------------------------------------------------------------
    def write_candidates(self, candidates: list[dict[str, Any]], session_id: str) -> dict[str, Any]:
        """写入确认候选。返回 {"written": n, "deprecated": m, "details": [...]}。

        任一条失败抛 Neo4jStoreError（说明已成功条数，不静默半写）。
        """
        written = 0
        deprecated = 0
        details: list[dict[str, Any]] = []
        try:
            with self._get_driver().session() as session:
                for i, cand in enumerate(candidates):
                    ctype = cand.get("type")
                    payload = cand.get("payload") or {}
                    if ctype == "relation":
                        stats = self._write_relation(session, payload, session_id)
                    elif ctype == "case":
                        stats = self._write_case(session, payload, session_id)
                    elif ctype == "constraint":
                        stats = self._write_constraint(session, payload, session_id)
                    else:
                        raise Neo4jStoreError(f"未知候选类型 {ctype!r}（第 {i + 1} 条）")
                    written += 1
                    deprecated += stats.get("deprecated", 0)
                    details.append({"index": i, "type": ctype, **stats})
        except Neo4jStoreError:
            raise
        except Exception as exc:
            raise Neo4jStoreError(
                f"Neo4j 回写中断：已成功 {written} 条后失败——{exc}"
            ) from exc
        return {"written": written, "deprecated": deprecated, "details": details}

    # ------------------------------------------------------------------
    def _deprecate_same_name(self, session: Any, label: str, name: str, new_key: str) -> int:
        """同名旧版本节点标 deprecated（不删实体，A-10）。"""
        result = session.run(
            f"MATCH (e:`{label}`) WHERE e.name = $name AND e.key <> $new_key "
            "AND coalesce(e.deprecated, false) = false "
            "SET e.deprecated = true, e.superseded_by = $new_key RETURN count(e) AS c",
            name=name, new_key=new_key,
        )
        record = result.single()
        return record["c"] if record else 0

    def _write_constraint(self, session: Any, payload: dict[str, Any], session_id: str) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        expression = str(payload.get("expression") or "").strip()
        if not name:
            raise Neo4jStoreError("constraint 候选缺少 name")
        key = _content_key(name, expression)
        session.run(
            "MERGE (c:DesignConstraint {key: $key}) "
            "ON CREATE SET c.name = $name, c.expression = $expression, "
            "c.context = $context, c.source_session = $session, c.created_at = datetime(), "
            "c.deprecated = false",
            key=key, name=name, expression=expression,
            context=str(payload.get("context") or ""), session=session_id,
        )
        deprecated = self._deprecate_same_name(session, "DesignConstraint", name, key)
        return {"key": key, "label": "DesignConstraint", "deprecated": deprecated}

    def _write_case(self, session: Any, payload: dict[str, Any], session_id: str) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        params = payload.get("params") or []
        if not name or not params:
            raise Neo4jStoreError("case 候选缺少 name 或 params")
        params_json = json.dumps(params, ensure_ascii=False)
        key = _content_key(name, params_json)
        session.run(
            "MERGE (c:DesignCase {key: $key}) "
            "ON CREATE SET c.name = $name, c.params_json = $params, "
            "c.context = $context, c.source_session = $session, c.created_at = datetime(), "
            "c.deprecated = false",
            key=key, name=name, params=params_json,
            context=str(payload.get("context") or ""), session=session_id,
        )
        deprecated = self._deprecate_same_name(session, "DesignCase", name, key)
        return {"key": key, "label": "DesignCase", "deprecated": deprecated}

    def _write_relation(self, session: Any, payload: dict[str, Any], session_id: str) -> dict[str, Any]:
        src, dst = payload.get("from") or {}, payload.get("to") or {}
        src_name, dst_name = str(src.get("name") or "").strip(), str(dst.get("name") or "").strip()
        if not src_name or not dst_name:
            raise Neo4jStoreError("relation 候选缺少 from/to 实体名")
        src_label = _safe_label(src.get("type"))
        dst_label = _safe_label(dst.get("type"))
        rel_type = _safe_rel_type(payload.get("relation"))
        src_key = _content_key(src_name)
        dst_key = _content_key(dst_name)
        session.run(
            f"MERGE (a:`{src_label}` {{key: $src_key}}) "
            "ON CREATE SET a.name = $src_name, a.source_session = $session, a.created_at = datetime() "
            f"MERGE (b:`{dst_label}` {{key: $dst_key}}) "
            "ON CREATE SET b.name = $dst_name, b.source_session = $session, b.created_at = datetime() "
            f"MERGE (a)-[r:`{rel_type}`]->(b) "
            "ON CREATE SET r.context = $context, r.source_session = $session",
            src_key=src_key, src_name=src_name, dst_key=dst_key, dst_name=dst_name,
            context=str(payload.get("context") or ""), session=session_id,
        )
        return {"key": f"{src_key}->{dst_key}", "label": src_label, "relation": rel_type, "deprecated": 0}
