# -*- coding: utf-8 -*-
"""参考范例检索服务（T1.5 迁移自旧仓库 code_search.py；T16.0 A0-1/A0-2 范例止血改造）。

基于 ref_code/catalog.yaml 的双信号匹配（标题命中 sonar_types + 关键词 token 命中率）。

T16.0（PHASE-016，DEV-001 §1.2）语义：
- A0-1 外置与刷新：`REF_CODE_DIR` 配置外置目录（空=受控包内 cad_engine/ref_code/ 回退）；
  每次选取前比对目录指纹（catalog.yaml + *.py 的 name/mtime/size），变化则整体原子
  reload——catalog 版本与文件 sha256 在同一快照内固化，防目录/脚本/版本读取不一致；
  坏目录/坏 YAML/缺文件显式记录并进入"无索引"降级（不注入），下次访问自动恢复重读。
- A0-2 匹配语义：三级 grade（type_matched=标题命中 / weak_keyword=纯关键词达阈值 /
  none）；**无匹配不注入任何默认文件**——旧版"catalog 缺失时回退第一个 .py"是错域
  静默注入根因，已删除。弱匹配默认不注入（REF_MATCH_ALLOW_WEAK=1 可开，B2 实验后再评估）。

T16.1（B1 最小管理，ADR-015）双源：`REF_SOURCE=file（默认）|db`——db 模式从 ref_scripts 表
装载 active 行（管理 API 启停/确认直接影响检索），快照指纹热更新对齐 A0-1；匹配/评分/弱过滤
逻辑零改动（"表数据源与文件回退遵守相同领域策略"）。DB 不可用 → 诚实降级
（reason=db_unavailable）**绝不静默回退文件源**（生成来源必须可追溯，A-14/A-16）。
db 模式 match.catalog_version = "ref-scripts@v{version}"，自然进入 A0-3 缓存指纹与
A0-4 reference_used（IR3 schema 零改动）。REF_SOURCE 为进程级 env，切换需重启 worker。
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Optional

import yaml

import app_config
from storage import object_store, ref_store

logger = logging.getLogger("sonarcad.refcode")

DEFAULT_REF_DIR = Path(__file__).resolve().parent / "ref_code"

# 匹配分级（A0-2）：grade 取值
GRADE_TYPE_MATCHED = "type_matched"    # 标题命中 sonar_types（权威信号）
GRADE_WEAK_KEYWORD = "weak_keyword"    # 纯关键词达阈值（标题未命中）
# 未注入原因（last_reason；A0-4 契约的降级原因来源）
REASON_NO_CATALOG = "no_catalog"       # 目录缺失/坏 YAML/空索引
REASON_NO_MATCH = "no_match"           # 有索引但无候选达阈值
REASON_WEAK_FILTERED = "weak_filtered" # 仅弱匹配候选且未开 REF_MATCH_ALLOW_WEAK
REASON_READ_FAILED = "read_failed"     # 命中条目对应文件读取失败
REASON_DB_UNAVAILABLE = "db_unavailable"  # db 源不可达/读取失败（T16.1，诚实降级不回退文件源）


class CodeSearchService:
    """catalog.yaml / ref_scripts 双源关键词匹配检索（T16.0 热更新与三级匹配 + T16.1 双源）。"""

    def __init__(self, catalog_dir: Optional[str | Path] = None):
        if catalog_dir:
            self._catalog_dir = Path(catalog_dir)
        else:
            # 受控包内回退：REF_CODE_DIR 未配置时用包内 ref_code/（A0-1）
            self._catalog_dir = Path(app_config.REF_CODE_DIR) if app_config.REF_CODE_DIR else DEFAULT_REF_DIR
        self._source = "db" if app_config.REF_SOURCE == "db" else "file"
        self._loaded = False
        # file 源状态（A0-1）
        self._fingerprint: Optional[list] = None
        self._catalog: Optional[dict] = None
        self._catalog_version = ""
        self._file_cache: dict[str, tuple[str, str]] = {}  # filename -> (content, sha256)
        # db 源状态（T16.1，ADR-015）
        self._db_fingerprint: Optional[list] = None
        self._db_entries: Optional[list[dict]] = None
        self._db_content_cache: dict[str, tuple[str, str]] = {}  # file_name -> (content, sha256)
        self._db_error = False
        # 最近一次 select_reference 未注入原因（None=已注入）；供调用方/A0-4 契约取用
        self.last_reason: Optional[str] = None

    # ------------------------------------------------------------------
    # A0-1：目录指纹与热更新（新增/修改/删除均触发整体原子 reload）
    # ------------------------------------------------------------------
    def _dir_fingerprint(self) -> Optional[list]:
        """catalog.yaml + *.py 的 (name, mtime_ns, size) 排序序列；目录不可达返回 None。"""
        try:
            entries = []
            for p in sorted(self._catalog_dir.iterdir()):
                if p.name == "catalog.yaml" or p.suffix == ".py":
                    st = p.stat()
                    entries.append((p.name, st.st_mtime_ns, st.st_size))
            return entries
        except OSError as exc:
            logger.warning("参考目录不可达（进入无索引降级，目录恢复后自动重读）: %s (%s)", self._catalog_dir, exc)
            return None

    def _ensure_fresh(self) -> None:
        """指纹变化则整体失效并重载（版本与内容同快照，防读取不一致）。"""
        fp = self._dir_fingerprint()
        if self._loaded and fp == self._fingerprint:
            return
        changed = self._loaded
        self._loaded = True
        self._fingerprint = fp
        self._catalog = None
        self._catalog_version = ""
        self._file_cache = {}
        if changed:
            logger.info("参考目录变更，整体重载 catalog: %s", self._catalog_dir)

    def load_catalog(self) -> Optional[dict]:
        self._ensure_fresh()
        if self._catalog is not None:
            return self._catalog
        path = self._catalog_dir / "catalog.yaml"
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("catalog.yaml 顶层不是映射")
            self._catalog = raw
            # 版本记录对应实际读取内容：version 与各文件 sha256 在本次快照内固化（A0-1 验收）
            self._catalog_version = str(raw.get("version") or "")
            return self._catalog
        except Exception as exc:
            logger.warning("catalog.yaml 加载失败（无索引降级，修复后下次访问自动恢复）: %s", exc)
            return None

    def get_full_file(self, filename: str) -> Optional[str]:
        """返回参考脚本正文；内容与 sha256 同快照缓存（A0-1）。"""
        if filename not in self._file_cache:
            try:
                content = (self._catalog_dir / filename).read_text(encoding="utf-8")
                self._file_cache[filename] = (content, hashlib.sha256(content.encode("utf-8")).hexdigest())
            except Exception:
                self._file_cache[filename] = ("", "")
        content, _sha = self._file_cache[filename]
        return content or None

    def content_sha256(self, filename: str) -> str:
        """已缓存文件的 sha256（未读过的文件返回空串；随 get_full_file 同快照）。"""
        return self._file_cache.get(filename, ("", ""))[1]

    # ------------------------------------------------------------------
    # T16.1（ADR-015）：db 源装载（ref_scripts active 行），热更新对齐 A0-1 语义
    # ------------------------------------------------------------------
    def _ensure_fresh_db(self) -> None:
        """快照指纹变化则整体重载 active 行；DB 不可用 → 诚实降级（不回退文件源）。"""
        try:
            fp = ref_store.snapshot_fingerprint()
        except Exception as exc:  # noqa: BLE001 - DB 不可达：降级不注入，恢复后自动重读
            logger.warning("范例 DB 源不可达（诚实降级不注入，恢复后下次访问自动重读）: %s", exc)
            self._db_error = True
            self._db_entries = None
            return
        self._db_error = False
        if self._loaded and fp == self._db_fingerprint:
            return
        try:
            rows = ref_store.get_active_rows()
        except Exception as exc:  # noqa: BLE001
            logger.warning("范例 DB 源 active 行读取失败（诚实降级不注入）: %s", exc)
            self._db_error = True
            self._db_entries = None
            self._db_fingerprint = None  # 下次强制重试
            return
        if self._loaded:
            logger.info("范例 DB 源变更，整体重载 active 行（%s 行）", len(rows))
        self._loaded = True
        self._db_fingerprint = fp
        entries: list[dict] = []
        cache: dict[str, tuple[str, str]] = {}
        for r in rows:
            file_name = r["file_name"]
            entries.append({
                "id": r["ref_id"],
                "file": file_name,
                "domain": str(r.get("domain") or ""),
                "domain_rules": r.get("domain_rules"),
                "sonar_types": r.get("sonar_types") or [],
                "keywords": r.get("keywords") or [],
                "description": r.get("description"),
                # 版本记录对应实际读取内容（A0-1 验收语义的 db 侧）：进缓存指纹与 reference_used
                "catalog_version": f"ref-scripts@v{r['version']}",
            })
            try:
                content = object_store.download_text(r["bucket"], r["object_key"])
            except Exception as exc:  # noqa: BLE001 - 对象读取失败：命中该条时不注入（read_failed 同款）
                logger.warning("范例对象读取失败 %s/%s（命中时不注入）: %s", r["bucket"], r["object_key"], exc)
                cache[file_name] = ("", r["content_sha256"])
                continue
            sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if sha != r["content_sha256"]:
                logger.warning(
                    "范例内容哈希不符（登记 %s…，实际 %s…）——按读取失败处理: %s",
                    r["content_sha256"][:12], sha[:12], file_name,
                )
                cache[file_name] = ("", sha)
            else:
                cache[file_name] = (content, sha)
        self._db_entries = entries
        self._db_content_cache = cache

    # ------------------------------------------------------------------
    # 双源统一钩子：匹配内核（评分/弱过滤/无匹配不注入）零改动，仅换装载与内容读取
    # ------------------------------------------------------------------
    def _entries(self) -> Optional[list[dict]]:
        """候选条目；None/[] = 索引不可用（file: 目录/坏 YAML；db: DB 不可用）。"""
        if self._source == "db":
            self._ensure_fresh_db()
            return self._db_entries
        self._ensure_fresh()
        catalog = self.load_catalog()
        if catalog is None:
            return None
        return catalog.get("reference_files") or []

    def _content_and_sha(self, filename: str) -> tuple[Optional[str], str]:
        """按源读取正文与 sha256（同快照；读不到返回 (None, sha或"")）。"""
        if self._source == "db":
            content, sha = self._db_content_cache.get(filename, ("", ""))
            return (content or None), sha
        content = self.get_full_file(filename)
        if not content:
            return None, ""
        return content, self.content_sha256(filename)

    # ------------------------------------------------------------------
    # A0-2：双信号评分 + 三级匹配（无匹配不注入错域默认文件）
    # ------------------------------------------------------------------
    def select_reference(
        self, design_document: str, top_n: int = 1, min_score: Optional[float] = None,
    ) -> tuple[str, Optional[dict]]:
        """返回 (content, match)。

        match=None 表示未注入任何参考（原因见 self.last_reason，A-14 如实）：
        no_catalog / no_match / weak_filtered / read_failed / db_unavailable（T16.1 db 源）。
        命中时 match = {ref_id, file, sha256, catalog_version, grade, score, domain}，
        grade ∈ {type_matched, weak_keyword}（weak 默认被过滤，见 REASON_WEAK_FILTERED）；
        db 源的 catalog_version = ref-scripts@v{version}（ADR-015）。
        """
        if min_score is None:
            min_score = app_config.REF_MATCH_MIN_SCORE
        entries = self._entries()
        if not entries:
            self.last_reason = (
                REASON_DB_UNAVAILABLE if (self._source == "db" and self._db_error) else REASON_NO_CATALOG
            )
            return "", None

        title = self._extract_title(design_document)
        tokens = [t for t in design_document[:2000].lower().split() if len(t) > 1]
        if any("\u4e00" <= ch <= "\u9fff" for ch in design_document[:2000]):
            # 中文补 2-gram（旧版按空格分词对中文无效，这里做最小增强）
            tokens += [design_document[i : i + 2] for i in range(len(design_document[:2000]) - 1)]

        candidates: list[tuple[float, str, dict]] = []
        for entry in entries:
            filename = entry.get("file", "")
            if not filename:
                continue
            keywords = entry.get("keywords", []) + entry.get("sonar_types", [])
            type_matched = False
            if title:
                tl = title.lower()
                type_matched = any(
                    st.lower() in tl or tl in st.lower() for st in entry.get("sonar_types", [])
                )
            keyword_score = self._score(tokens, keywords)
            combined = 0.5 * (1.0 if type_matched else 0.0) + 0.5 * keyword_score
            if combined >= min_score:
                grade = GRADE_TYPE_MATCHED if type_matched else GRADE_WEAK_KEYWORD
                candidates.append((combined, grade, entry))

        # T17.4（ADR-016 §4）：向量名次合成（app_config.REF_VECTOR_ENABLED，默认关=零执行
        # 与纯词法逐字节一致）。grade 判定/无匹配不注入/weak 过滤三锚全不动——RRF 只作
        # 同 grade 内次级排序（type_matched 权威优先仍居首位）；向量命中但词法低于阈值的
        # entry 以 weak_keyword 加入候选（subject to REF_MATCH_ALLOW_WEAK，向量只扩召回
        # 不改判分）。读侧 active 兜底：向量名次对当前 entries 的 ref_id 集合过滤。
        vector_ranks: dict[str, tuple[int, float]] = {}
        if app_config.REF_VECTOR_ENABLED:
            from cad_engine.ref_vector import search_ref_vectors

            vector_ranks = search_ref_vectors(design_document[:2000])
            if vector_ranks:
                known = {str(e.get("id") or Path(e.get("file", "")).stem) for e in entries}
                vector_ranks = {k: v for k, v in vector_ranks.items() if k in known}
                seen_ids = {str(e.get("id") or Path(e.get("file", "")).stem)
                            for _, _, e in candidates}
                for ref_id, (_rank, _vs) in vector_ranks.items():
                    if ref_id in seen_ids:
                        continue  # 已是词法候选
                    entry = next((e for e in entries
                                  if str(e.get("id") or Path(e.get("file", "")).stem) == ref_id), None)
                    if entry is not None:
                        # 向量扩召回：词法未达阈值 → weak_keyword（默认被 weak 过滤，语义不破）
                        candidates.append((0.0, GRADE_WEAK_KEYWORD, entry))
                        seen_ids.add(ref_id)

        def _rrf(entry_id: str, lexical_rank: int) -> float:
            k = 60
            s = 1.0 / (k + lexical_rank)
            vr = vector_ranks.get(entry_id)
            if vr is not None:
                s += 1.0 / (k + vr[0])
            return s

        # 先按词法内部序定名次（combined 分），再合成 RRF；标题命中（权威信号）仍居首位
        candidates.sort(key=lambda x: (x[1] == GRADE_TYPE_MATCHED, x[0]), reverse=True)
        if vector_ranks:
            fused: list[tuple[float, str, dict, float]] = []
            for lex_rank, (score, grade, entry) in enumerate(candidates, start=1):
                entry_id = str(entry.get("id") or Path(entry.get("file", "")).stem)
                fused.append((score, grade, entry, _rrf(entry_id, lex_rank)))
            fused.sort(key=lambda x: (x[1] == GRADE_TYPE_MATCHED, x[3], x[0]), reverse=True)
            candidates = [(s, g, e) for s, g, e, _ in fused]
            logger.info("范例向量名次参与融合: %s", {k: v[0] for k, v in vector_ranks.items()})

        reason: Optional[str] = None
        for score, grade, entry in candidates[:top_n]:
            if grade == GRADE_WEAK_KEYWORD and not app_config.REF_MATCH_ALLOW_WEAK:
                # 止血保守策略：纯关键词弱匹配默认不注入（防错域），B2 实验后再评估
                reason = REASON_WEAK_FILTERED
                continue
            filename = entry.get("file", "")
            content, sha = self._content_and_sha(filename)
            if not content:
                logger.warning("参考条目内容读取失败（不注入）: %s", filename)
                reason = REASON_READ_FAILED
                continue
            ref_id = str(entry.get("id") or Path(filename).stem)
            catalog_version = str(entry.get("catalog_version") or self._catalog_version)
            self.last_reason = None
            logger.info(
                "参考范例命中: %s (grade=%s score=%.3f catalog_v=%s source=%s)",
                filename, grade, score, catalog_version, self._source,
            )
            return content, {
                "ref_id": ref_id,
                "file": filename,
                "sha256": sha,
                "catalog_version": catalog_version,
                "grade": grade,
                "score": round(score, 4),
                "domain": str(entry.get("domain") or ""),
            }
        self.last_reason = reason or REASON_NO_MATCH
        logger.info("未注入参考范例（reason=%s），本次生成走无领域范例模式", self.last_reason)
        return "", None

    # ------------------------------------------------------------------
    @staticmethod
    def _extract_title(doc: str) -> str:
        for line in doc.splitlines():
            if line.strip().startswith("#"):
                title = line.lstrip("#").strip()
                for suffix in ("CAD 设计文档", "CAD设计文档", "设计文档"):
                    if title.endswith(suffix):
                        title = title[: -len(suffix)].strip()
                return title
        return ""

    @staticmethod
    def _score(query_tokens: list[str], target_keywords: list[str]) -> float:
        if not query_tokens:
            return 0.0
        targets = [k.lower() for k in target_keywords]
        matches = sum(1 for qt in query_tokens if any(qt in tk or tk in qt for tk in targets))
        return matches / len(query_tokens)


def strip_markdown(raw: str) -> str:
    """剥 LLM 输出的 markdown 围栏（迁移自旧 llm.strip_markdown 行为）。"""
    raw = raw.strip()
    fence = re.match(r"^```(?:python)?\s*\n(.*?)\n```\s*$", raw, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return raw
