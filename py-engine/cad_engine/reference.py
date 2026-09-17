# -*- coding: utf-8 -*-
"""参考范例检索服务（T1.5，迁移自旧仓库 code_search.py ★直接迁移 + 路径适配）。

基于 ref_code/catalog.yaml 的关键词匹配；catalog 缺失时回退第一个 .py 文件。
添加新声呐类型只需在 catalog.yaml 追加条目，无需修改本文件。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("sonarcad.refcode")

DEFAULT_REF_DIR = Path(__file__).resolve().parent / "ref_code"


class CodeSearchService:
    """catalog.yaml 关键词匹配检索（迁移自旧 CodeSearchService，接口保持一致）。"""

    def __init__(self, catalog_dir: Optional[str | Path] = None):
        self._catalog_dir = Path(catalog_dir) if catalog_dir else DEFAULT_REF_DIR
        self._catalog: Optional[dict] = None
        self._file_cache: dict[str, Optional[str]] = {}

    # ------------------------------------------------------------------
    def load_catalog(self) -> Optional[dict]:
        if self._catalog is not None:
            return self._catalog
        path = self._catalog_dir / "catalog.yaml"
        try:
            self._catalog = yaml.safe_load(path.read_text(encoding="utf-8"))
            return self._catalog
        except Exception as exc:
            logger.warning("catalog.yaml 加载失败: %s", exc)
            return self._catalog

    def get_full_file(self, filename: str) -> Optional[str]:
        if filename not in self._file_cache:
            try:
                self._file_cache[filename] = (self._catalog_dir / filename).read_text(encoding="utf-8")
            except Exception:
                self._file_cache[filename] = None
        return self._file_cache[filename]

    # ------------------------------------------------------------------
    # 双信号评分选参考文件（迁移自旧 codegen_stage._select_reference_files）
    # ------------------------------------------------------------------
    def select_reference(self, design_document: str, top_n: int = 1, min_score: float = 0.05):
        """返回 [(filename, full_content, match_info)]；无 catalog 时回退默认文件。"""
        catalog = self.load_catalog()
        entries = (catalog or {}).get("reference_files") or []

        title = self._extract_title(design_document)
        tokens = [t for t in design_document[:2000].lower().split() if len(t) > 1]
        if any("\u4e00" <= ch <= "\u9fff" for ch in design_document[:2000]):
            # 中文补 2-gram（旧版按空格分词对中文无效，这里做最小增强）
            tokens += [design_document[i : i + 2] for i in range(len(design_document[:2000]) - 1)]

        candidates = []
        for entry in entries:
            filename = entry.get("file", "")
            keywords = entry.get("keywords", []) + entry.get("sonar_types", [])
            type_score = 0.0
            if title:
                tl = title.lower()
                type_score = 1.0 if any(
                    st.lower() in tl or tl in st.lower() for st in entry.get("sonar_types", [])
                ) else 0.0
            keyword_score = self._score(tokens, keywords)
            combined = 0.5 * type_score + 0.5 * keyword_score
            if combined >= min_score:
                candidates.append((combined, entry.get("sonar_types", []), filename))

        candidates.sort(key=lambda x: x[0], reverse=True)
        for score, types, filename in candidates[:top_n]:
            content = self.get_full_file(filename)
            if content:
                return content, {"filename": filename, "score": score, "type_matched": bool(types)}
            # 无 catalog 时回退：第一个 .py 文件
        for py in sorted(self._catalog_dir.glob("*.py")):
            content = py.read_text(encoding="utf-8")
            logger.info("参考文件回退加载: %s", py.name)
            return content, None
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
