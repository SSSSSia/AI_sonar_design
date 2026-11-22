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

# T17.3（ADR-016 §3）：公共次级标签——10 个白名单 label 各建向量索引太散，统一挂公共
# 标签建单索引（A-10 加法演进：标签=元数据不删，既有按白名单 label 的查询语义零改动）
KNOWLEDGE_ENTITY_LABEL = "KnowledgeEntity"
VECTOR_INDEX_NAME = "kg_entity_desc"


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


def _match_terms(query: str, max_terms: int = 12) -> list[str]:
    """查询词提取：空格词 + 中文 2-gram，去重、长短语优先，限制条数（防 Cypher 参数爆炸）。"""
    seen: list[str] = []
    raw = [t for t in str(query or "").split() if len(t) > 1]
    if any("\u4e00" <= ch <= "\u9fff" for ch in (query or "")):
        raw += [query[i:i + 2] for i in range(len(query) - 1)]
    for t in sorted(set(raw), key=len, reverse=True):
        if t not in seen:
            seen.append(t)
        if len(seen) >= max_terms:
            break
    return seen


def _embedding_text(props: dict[str, Any]) -> str:
    """实体 embedding 材料（T17.3）：name + 描述性正文 + context——实体/约束/案例统一口径。

    描述性正文按 description > expression > params_json 兜底（文档实体有 description，
    约束/案例节点无）；三段全空返回空串（调用方跳过不嵌，A-16 不造空向量）。"""
    name = str(props.get("name") or "").strip()
    body = (props.get("description") or props.get("expression")
            or props.get("params_json") or "")
    parts = [name, str(body or "").strip(), str(props.get("context") or "").strip()]
    return "：".join(p for p in parts if p).strip()


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

    def deprecate_by_source_doc(self, doc_id: str) -> int:
        """文档删除的图谱同步（§6 ⑦）：该文档来源的实体不删、标 deprecated。"""
        with self._get_driver().session() as session:
            result = session.run(
                "MATCH (e) WHERE e.source_doc = $doc_id AND coalesce(e.deprecated, false) = false "
                "SET e.deprecated = true RETURN count(e) AS c",
                doc_id=doc_id,
            )
            record = result.single()
            return record["c"] if record else 0

    def delete_by_source_session(self, session_id: str) -> int:
        """按会话 id 删除其来源节点（e2e teardown 测试卫生，M-2 欠账清偿）。

        与 deprecate_by_source_doc 的生产语义相反（图谱不删实体，A-10）：这里删的是
        **测试会话**写入的节点（session_id 经网关原样透传进 source_session，精确限定），
        目的=同图重跑幂等（A5 判重对同名含 deprecated 全判 duplicate → 回写 400）。
        生产代码不得调用；可用性守卫由调用方/本方法 available() 承担。
        """
        with self._get_driver().session() as session:
            result = session.run(
                "MATCH (e) WHERE e.source_session = $sid DETACH DELETE e RETURN count(e) AS c",
                sid=session_id,
            )
            record = result.single()
            return record["c"] if record else 0

    def find_entities_matching(self, query: str, limit: int = 5,
                               doc_ids: list[str] | None = None,
                               kb_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """检索图谱级（T3.2）：query 关键词与实体名互相包含即命中（非 deprecated 优先）。

        PHASE-006 库范围下推（D6-2/D6-8）：doc_ids/kb_ids 任一提供时按
        `source_doc IN doc_ids OR kb_id IN kb_ids` 过滤（文档类实体走 doc 归属，
        A5 沉淀节点无 source_doc、靠 kb_id 属性）。两者皆 None = 不过滤（全量）。
        """
        terms = _match_terms(query)
        if not terms:
            return []
        conditions = " OR ".join(f"e.name CONTAINS $t{i}" for i in range(len(terms)))
        params: dict[str, Any] = {f"t{i}": t for i, t in enumerate(terms)}
        params["limit"] = limit
        scope = ""
        if doc_ids is not None or kb_ids is not None:
            ors: list[str] = []
            if doc_ids:
                params["scope_docs"] = doc_ids
                ors.append("e.source_doc IN $scope_docs")
            if kb_ids:
                params["scope_kbs"] = kb_ids
                ors.append("e.kb_id IN $scope_kbs")
            scope = " AND (" + (" OR ".join(ors) if ors else "false") + ")"
        with self._get_driver().session() as session:
            return session.run(
                f"MATCH (e) WHERE ({conditions}) AND e.key IS NOT NULL "
                "AND coalesce(e.deprecated, false) = false" + scope +
                " RETURN labels(e)[0] AS label, e.name AS name, "
                "e.description AS description, e.source_doc AS source_doc, e.kb_id AS kb_id "
                "ORDER BY e.deprecated SKIP 0 LIMIT $limit",
                **params,
            ).data()

    # ------------------------------------------------------------------
    # T17.3 图路向量升级（ADR-016 §3）：实体语义检索 + 邻域上下文块 + 写路径 embedding。
    # 老查询 find_entities_matching 保留（语义路不可用时的关键词兜底，独立命名零改动）。
    # ------------------------------------------------------------------
    def ensure_vector_index(self, dim: int = 1024) -> bool:
        """实体向量索引（KnowledgeEntity.description_embedding，cosine）。幂等；失败 False。

        indexConfig 需字面量（Cypher 不接受参数），dim 经 int() 内联。"""
        dim = int(dim)
        try:
            with self._get_driver().session() as session:
                session.run(
                    f"CREATE VECTOR INDEX {VECTOR_INDEX_NAME} IF NOT EXISTS "
                    f"FOR (n:{KNOWLEDGE_ENTITY_LABEL}) ON (n.description_embedding) "
                    "OPTIONS {indexConfig: {`vector.dimensions`: " + str(dim) + ", "
                    "`vector.similarity_function`: 'cosine'}}"
                )
            return True
        except Exception as exc:
            logger.warning("实体向量索引创建失败（语义路将回退关键词）: %s", exc)
            return False

    def embed_entity_keys(self, keys: list[str]) -> int:
        """为指定实体补 description_embedding 并挂公共标签（T17.3，加法元数据）。

        **按 (key, label) 逐节点嵌入**——同名跨标签节点（key=sha1(名) 设计使然）各自用
        本节点材料，不共享向量。best-effort：embedding 供应商不可用/失败 → 返回 0 不抛
        （写入路径不阻塞，存量回填脚本可补）；材料为空/节点缺失跳过（A-16 不造空向量）。"""
        if not keys:
            return 0
        try:
            with self._get_driver().session() as session:
                rows = session.run(
                    "MATCH (n) WHERE n.key IN $keys "
                    "RETURN n.key AS key, labels(n)[0] AS label, properties(n) AS props",
                    keys=list(keys),
                ).data()
            items: list[dict[str, Any]] = []
            for r in rows:
                t = _embedding_text(r.get("props") or {})
                if t:
                    items.append({"key": r["key"], "label": r.get("label") or "", "text": t})
            if not items:
                return 0
            from kb.embeddings import embed_texts

            vectors = embed_texts([it["text"] for it in items])
            if not vectors:
                logger.warning("实体 embedding 供应商不可用（%d 条未嵌入，回填脚本可补）", len(items))
                return 0
            with self._get_driver().session() as session:
                session.run(
                    f"UNWIND $rows AS row MATCH (n {{key: row.key}}) "
                    "WHERE labels(n)[0] = row.label "
                    f"SET n:{KNOWLEDGE_ENTITY_LABEL} SET n.description_embedding = row.vec",
                    rows=[{"key": it["key"], "label": it["label"], "vec": v}
                          for it, v in zip(items, vectors)],
                )
            return len(items)
        except Exception as exc:
            logger.warning("实体 embedding 写入失败（不阻塞主流程，回填脚本可补）: %s", exc)
            return 0

    def backfill_entity_embeddings(self, force: bool = False, batch_size: int = 32) -> dict[str, int]:
        """存量实体 embedding 回填（T17.3）：全量 key 节点挂公共标签 + 非 deprecated 缺失者
        **按 (key, label) 逐节点**批量嵌入（bge-m3 与 kb_chunks 同链）——同名跨标签孪生
        各用本节点材料，不共享向量。幂等（已嵌跳过，force 重算）。

        诚实报告：embedded/skipped_empty/failed——失败层如实计数不伪造成功（A-16）。"""
        from kb.embeddings import embed_texts

        with self._get_driver().session() as session:
            session.run(f"MATCH (n) WHERE n.key IS NOT NULL SET n:{KNOWLEDGE_ENTITY_LABEL}")
            where = "" if force else " AND n.description_embedding IS NULL"
            rows = session.run(
                "MATCH (n) WHERE n.key IS NOT NULL AND coalesce(n.deprecated, false) = false" + where +
                " RETURN n.key AS key, labels(n)[0] AS label, properties(n) AS props ORDER BY key"
            ).data()
        items: list[dict[str, Any]] = []
        skipped_empty = 0
        for r in rows:
            t = _embedding_text(r.get("props") or {})
            if t:
                items.append({"key": r["key"], "label": r.get("label") or "", "text": t})
            else:
                skipped_empty += 1
        embedded = 0
        failed = 0
        for i in range(0, len(items), batch_size):
            chunk = items[i:i + batch_size]
            try:
                vectors = embed_texts([it["text"] for it in chunk])
            except Exception:
                vectors = None
            if not vectors:
                failed += len(chunk)
                continue
            with self._get_driver().session() as session:
                session.run(
                    f"UNWIND $rows AS row MATCH (n {{key: row.key}}) "
                    "WHERE labels(n)[0] = row.label "
                    f"SET n:{KNOWLEDGE_ENTITY_LABEL} SET n.description_embedding = row.vec",
                    rows=[{"key": it["key"], "label": it["label"], "vec": v}
                          for it, v in zip(chunk, vectors)],
                )
            embedded += len(chunk)
        return {"embedded": embedded, "skipped_empty": skipped_empty, "failed": failed}

    def find_entities_semantic(self, query_vector: list[float], limit: int = 5,
                               doc_ids: list[str] | None = None,
                               kb_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """实体语义检索（T17.3，ADR-016 §3）：向量索引 top_k → 逐命中邻域扩展拼 context_block。

        向量索引不可用抛 Neo4jStoreError（调用方回退 CONTAINS 老查询）；
        scope 语义与 find_entities_matching 一致（doc_ids/kb_ids 任一提供即过滤）。"""
        if not self.ensure_vector_index():
            raise Neo4jStoreError("实体向量索引不可用（语义路不可用）")
        params: dict[str, Any] = {
            "vec": [float(x) for x in query_vector],
            "k": max(int(limit) * 4, int(limit)),  # 向量查询先出后滤的余量（deprecated/scope 过滤）
            "limit": int(limit),
        }
        scope = ""
        if doc_ids is not None or kb_ids is not None:
            ors: list[str] = []
            if doc_ids:
                params["scope_docs"] = doc_ids
                ors.append("node.source_doc IN $scope_docs")
            if kb_ids:
                params["scope_kbs"] = kb_ids
                ors.append("node.kb_id IN $scope_kbs")
            scope = " AND (" + (" OR ".join(ors) if ors else "false") + ")"
        with self._get_driver().session() as session:
            rows = session.run(
                f"CALL db.index.vector.queryNodes('{VECTOR_INDEX_NAME}', $k, $vec) "
                "YIELD node, score "
                "WHERE node.key IS NOT NULL AND coalesce(node.deprecated, false) = false" + scope +
                " RETURN node.key AS key, labels(node)[0] AS label, node.name AS name, "
                "node.description AS description, node.source_doc AS source_doc, "
                "node.kb_id AS kb_id, score ORDER BY score LIMIT $limit",
                **params,
            ).data()
        out: list[dict[str, Any]] = []
        for r in rows:
            block = self._entity_context_block(r["key"], r.get("label"), r.get("name"), r.get("description"))
            out.append({**r, "context_block": block})
        return out

    def _entity_context_block(self, key: str, label: Any, name: Any, description: Any) -> str:
        """图上下文块（ADR-016 §3）：命中实体 + 1 跳邻域（复用 neighborhood 变长路径先例，
        deprecated 邻居不呈现）。"""
        base = f"[{label}] {name}" + (f"：{description}" if description else "")
        try:
            nb = self.neighborhood(key, depth=1, limit=12)
        except Exception as exc:
            logger.warning("邻域扩展失败（仅呈现命中实体）: %s", exc)
            return base
        nodes = {n.get("key"): n for n in (nb.get("nodes") or [])}
        parts: list[str] = []
        for e in (nb.get("edges") or []):
            src, dst = nodes.get(e.get("source")), nodes.get(e.get("target"))
            if not src or not dst or src.get("deprecated") or dst.get("deprecated"):
                continue
            parts.append(f"[{src.get('label')}] {src.get('name')} —{e.get('rel_type')}→ "
                         f"[{dst.get('label')}] {dst.get('name')}")
        if not parts:
            return base
        return base + "（关联：" + "；".join(parts[:8]) + "）"

    # ------------------------------------------------------------------
    # 图谱可视化数据接口（T3.4，§5.14）：力导向全图采样 + 邻域子图
    # ------------------------------------------------------------------
    def graph_sample(self, limit: int = 300) -> dict[str, Any]:
        """全图采样：非 deprecated 实体 + 其间关系（限流）。节点带 source_doc 可反查文档。"""
        with self._get_driver().session() as session:
            nodes = session.run(
                "MATCH (e) WHERE e.key IS NOT NULL AND coalesce(e.deprecated, false) = false "
                "RETURN e.key AS key, labels(e)[0] AS label, e.name AS name, "
                "e.source_doc AS source_doc, e.source_session AS source_session LIMIT $limit",
                limit=limit,
            ).data()
            if not nodes:
                return {"nodes": [], "edges": []}
            keys = [n["key"] for n in nodes]
            edges = session.run(
                "MATCH (a)-[r]->(b) WHERE a.key IN $keys AND b.key IN $keys "
                "RETURN a.key AS source, b.key AS target, type(r) AS rel_type LIMIT $limit",
                keys=keys, limit=limit * 3,
            ).data()
        return {"nodes": nodes, "edges": edges}

    def neighborhood(self, key: str, depth: int = 2, limit: int = 100) -> dict[str, Any]:
        """邻域子图：以 key 为中心 BFS（depth 限 1~3）。返回 {center, nodes, edges}。"""
        depth = max(1, min(3, int(depth)))
        with self._get_driver().session() as session:
            center = session.run(
                "MATCH (e {key: $key}) WHERE e.key IS NOT NULL "
                "RETURN e.key AS key, labels(e)[0] AS label, e.name AS name, "
                "e.source_doc AS source_doc, e.source_session AS source_session, "
                "coalesce(e.deprecated, false) AS deprecated",
                key=key,
            ).data()
            if not center:
                return {"center": None, "nodes": [], "edges": []}
            rows = session.run(
                f"MATCH (c {{key: $key}})-[r*1..{depth}]-(n) WHERE n.key IS NOT NULL "
                "RETURN DISTINCT n.key AS key, labels(n)[0] AS label, n.name AS name, "
                "n.source_doc AS source_doc, n.source_session AS source_session, "
                "coalesce(n.deprecated, false) AS deprecated "
                "LIMIT $limit",
                key=key, limit=limit,
            ).data()
            nodes = center + rows
            keys = [n["key"] for n in nodes]
            edges = session.run(
                "MATCH (a)-[r]->(b) WHERE a.key IN $keys AND b.key IN $keys "
                "RETURN a.key AS source, b.key AS target, type(r) AS rel_type LIMIT $limit",
                keys=keys, limit=limit * 3,
            ).data()
        return {"center": center[0], "nodes": nodes, "edges": edges}

    # ------------------------------------------------------------------
    def write_candidates(self, candidates: list[dict[str, Any]], session_id: str,
                         kb_id: str | None = None) -> dict[str, Any]:
        """写入确认候选（kb_id = PHASE-006 D6-8 库归属，source_session 类节点无 source_doc、
        靠该属性参与库范围检索）。返回 {"written": n, "deprecated": m, "details": [...]}。

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
                        stats = self._write_relation(session, payload, session_id, kb_id)
                    elif ctype == "case":
                        stats = self._write_case(session, payload, session_id, kb_id)
                    elif ctype == "constraint":
                        stats = self._write_constraint(session, payload, session_id, kb_id)
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
        # T17.3：本批实体 embedding（加法元数据，best-effort 失败不阻塞回写——A-10 语义不动）
        embed_keys = [k for d in details for k in d.get("embed_keys", [])]
        embeddings = self.embed_entity_keys(list(dict.fromkeys(embed_keys)))
        return {"written": written, "deprecated": deprecated, "embeddings": embeddings,
                "details": details}

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

    def _write_constraint(self, session: Any, payload: dict[str, Any], session_id: str,
             kb_id: str | None = None) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        expression = str(payload.get("expression") or "").strip()
        if not name:
            raise Neo4jStoreError("constraint 候选缺少 name")
        key = _content_key(name, expression)
        session.run(
            "MERGE (c:DesignConstraint {key: $key}) "
            "ON CREATE SET c.name = $name, c.expression = $expression, "
            "c.context = $context, c.source_session = $session, c.kb_id = $kb_id, "
            "c.created_at = datetime(), c.deprecated = false, c:KnowledgeEntity "
            "ON MATCH SET c:KnowledgeEntity",
            key=key, name=name, expression=expression,
            context=str(payload.get("context") or ""), session=session_id, kb_id=kb_id,
        )
        deprecated = self._deprecate_same_name(session, "DesignConstraint", name, key)
        return {"key": key, "label": "DesignConstraint", "deprecated": deprecated,
                "embed_keys": [key]}

    def _write_case(self, session: Any, payload: dict[str, Any], session_id: str,
             kb_id: str | None = None) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        params = payload.get("params") or []
        if not name or not params:
            raise Neo4jStoreError("case 候选缺少 name 或 params")
        params_json = json.dumps(params, ensure_ascii=False)
        key = _content_key(name, params_json)
        session.run(
            "MERGE (c:DesignCase {key: $key}) "
            "ON CREATE SET c.name = $name, c.params_json = $params, "
            "c.context = $context, c.source_session = $session, c.kb_id = $kb_id, "
            "c.created_at = datetime(), c.deprecated = false, c:KnowledgeEntity "
            "ON MATCH SET c:KnowledgeEntity",
            key=key, name=name, params=params_json,
            context=str(payload.get("context") or ""), session=session_id, kb_id=kb_id,
        )
        deprecated = self._deprecate_same_name(session, "DesignCase", name, key)
        return {"key": key, "label": "DesignCase", "deprecated": deprecated,
                "embed_keys": [key]}

    def _write_relation(self, session: Any, payload: dict[str, Any], session_id: str,
                        kb_id: str | None = None) -> dict[str, Any]:
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
            "ON CREATE SET a.name = $src_name, a.source_session = $session, a.kb_id = $kb_id, a.created_at = datetime(), a:KnowledgeEntity "
            f"MERGE (b:`{dst_label}` {{key: $dst_key}}) "
            "ON CREATE SET b.name = $dst_name, b.source_session = $session, b.kb_id = $kb_id, b.created_at = datetime(), b:KnowledgeEntity "
            f"MERGE (a)-[r:`{rel_type}`]->(b) "
            "ON CREATE SET r.context = $context, r.source_session = $session, r.kb_id = $kb_id",
            src_key=src_key, src_name=src_name, dst_key=dst_key, dst_name=dst_name,
            context=str(payload.get("context") or ""), session=session_id, kb_id=kb_id,
        )
        return {"key": f"{src_key}->{dst_key}", "label": src_label, "relation": rel_type,
                "deprecated": 0, "embed_keys": [src_key, dst_key]}

    # ------------------------------------------------------------------
    # GraphML 快照（T11.7，P2-3 承接）：在线全图导出，落 MinIO knowledge-dumps 桶
    # ------------------------------------------------------------------
    def graphml_snapshot(self) -> dict[str, Any]:
        """全图导出为 GraphML 字符串（key 节点全量 + 其间关系）。

        契约对齐 apoc.export.graphml 常规形态：节点用 labels 特殊键、关系用 label 键、
        属性带 type 声明——yEd/Gephi 可直读，apoc.import.graphml 可回灌（回灌演练属
        ADR-005 生产落地项，本期只承诺快照可导出可读）。备份是快照语义：deprecated
        节点同样入快照（恢复后历史可查，A-12 精神）。"""
        from xml.sax.saxutils import escape, quoteattr

        with self._get_driver().session() as session:
            nodes = session.run(
                "MATCH (e) WHERE e.key IS NOT NULL "
                "RETURN e.key AS key, labels(e)[0] AS label, e.name AS name, "
                "e.source_doc AS source_doc, e.source_session AS source_session, "
                "coalesce(e.deprecated, false) AS deprecated"
            ).data()
            edges = session.run(
                "MATCH (a)-[r]->(b) WHERE a.key IS NOT NULL AND b.key IS NOT NULL "
                "RETURN a.key AS source, labels(a)[0] AS slabel, b.key AS target, "
                "labels(b)[0] AS tlabel, type(r) AS rel_type"
            ).data()
        # node id = 「label:key」命名空间（同 key 可跨 label 存在；同 (label,key) 重复才是真冲突）
        if len({(n["label"], n["key"]) for n in nodes}) != len(nodes):
            raise Neo4jStoreError("存在重复 (label,key)，GraphML 节点 id 冲突——先治理再快照")

        def nid(label: Any, key: Any) -> str:
            return f"{label}:{key}"

        def node_xml(n: dict[str, Any]) -> str:
            props = "".join(
                f'<data key="k{key}">{escape("" if n[key] is None else str(n[key]))}</data>'
                for key in ("key", "name", "source_doc", "source_session", "deprecated")
            )
            return (f'    <node id={quoteattr(nid(n["label"], n["key"]))}>'
                    f'<data key="labels">{escape(str(n["label"]))}</data>{props}</node>')

        body = "\n".join(node_xml(n) for n in nodes)
        rels = "\n".join(
            f'    <edge id="e{i}" source={quoteattr(nid(e["slabel"], e["source"]))} '
            f'target={quoteattr(nid(e["tlabel"], e["target"]))}>'
            f'<data key="label">{escape(str(e["rel_type"]))}</data></edge>'
            for i, e in enumerate(edges)
        )
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">\n'
            '  <key id="labels" for="node" attr.name="labels" attr.type="string"/>\n'
            '  <key id="label" for="edge" attr.name="label" attr.type="string"/>\n'
            '  <key id="kkey" for="node" attr.name="key" attr.type="string"/>\n'
            '  <key id="kname" for="node" attr.name="name" attr.type="string"/>\n'
            '  <key id="ksd" for="node" attr.name="source_doc" attr.type="string"/>\n'
            '  <key id="kss" for="node" attr.name="source_session" attr.type="string"/>\n'
            '  <key id="kdep" for="node" attr.name="deprecated" attr.type="boolean"/>\n'
            '  <graph id="knowledge" edgedefault="directed">\n'
            f"{body}\n{rels}\n  </graph>\n</graphml>\n"
        )
        return {"xml": xml, "nodes": len(nodes), "edges": len(edges)}
