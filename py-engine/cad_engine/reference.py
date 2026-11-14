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
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Optional

import yaml

import app_config

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


class CodeSearchService:
    """catalog.yaml 关键词匹配检索（T16.0 起带热更新与三级匹配语义）。"""

    def __init__(self, catalog_dir: Optional[str | Path] = None):
        if catalog_dir:
            self._catalog_dir = Path(catalog_dir)
        else:
            # 受控包内回退：REF_CODE_DIR 未配置时用包内 ref_code/（A0-1）
            self._catalog_dir = Path(app_config.REF_CODE_DIR) if app_config.REF_CODE_DIR else DEFAULT_REF_DIR
        self._loaded = False
        self._fingerprint: Optional[list] = None
        self._catalog: Optional[dict] = None
        self._catalog_version = ""
        self._file_cache: dict[str, tuple[str, str]] = {}  # filename -> (content, sha256)
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
    # A0-2：双信号评分 + 三级匹配（无匹配不注入错域默认文件）
    # ------------------------------------------------------------------
    def select_reference(
        self, design_document: str, top_n: int = 1, min_score: Optional[float] = None,
    ) -> tuple[str, Optional[dict]]:
        """返回 (content, match)。

        match=None 表示未注入任何参考（原因见 self.last_reason，A-14 如实）：
        no_catalog / no_match / weak_filtered / read_failed。
        命中时 match = {ref_id, file, sha256, catalog_version, grade, score}，
        grade ∈ {type_matched, weak_keyword}（weak 默认被过滤，见 REASON_WEAK_FILTERED）。
        """
        if min_score is None:
            min_score = app_config.REF_MATCH_MIN_SCORE
        catalog = self.load_catalog()
        entries = (catalog or {}).get("reference_files") or []
        if not entries:
            self.last_reason = REASON_NO_CATALOG
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

        # 标题命中（权威信号）优先，其后按分排序
        candidates.sort(key=lambda x: (x[1] == GRADE_TYPE_MATCHED, x[0]), reverse=True)

        reason: Optional[str] = None
        for score, grade, entry in candidates[:top_n]:
            if grade == GRADE_WEAK_KEYWORD and not app_config.REF_MATCH_ALLOW_WEAK:
                # 止血保守策略：纯关键词弱匹配默认不注入（防错域），B2 实验后再评估
                reason = REASON_WEAK_FILTERED
                continue
            filename = entry.get("file", "")
            content = self.get_full_file(filename)
            if not content:
                logger.warning("参考条目文件读取失败（不注入）: %s", filename)
                reason = REASON_READ_FAILED
                continue
            ref_id = str(entry.get("id") or Path(filename).stem)
            self.last_reason = None
            logger.info(
                "参考范例命中: %s (grade=%s score=%.3f catalog_v=%s)",
                filename, grade, score, self._catalog_version,
            )
            return content, {
                "ref_id": ref_id,
                "file": filename,
                "sha256": self.content_sha256(filename),
                "catalog_version": self._catalog_version,
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
