"""LLM 本体抽取（T3.2，宪法 §6 ④）。

逐 chunk 抽实体/关系，类型**必须落在本体白名单**内（settings.yaml entity_types），
越界类型清洗丢弃——脏类型不得进抽取预览。抽取结果一律 pending，人工确认后才入图。
LLM 不可用时抛 ExtractionUnavailable，由调用方如实跳过（不伪造抽取，A-16）。
"""

from __future__ import annotations

import logging
from typing import Any

from agents.a1_requirement.agent import parse_llm_json

logger = logging.getLogger("sonarcad.kb.extract")

# 本体类型白名单（与 knowledge/kb/settings.yaml entity_types 对齐；settings 缺失时兜底）
DEFAULT_ENTITY_TYPES = [
    "SonarSystem", "StructuralModule", "Component", "DesignParameter",
    "PerformanceIndicator", "DesignConstraint", "Algorithm", "CADModelingStandard",
]

MAX_ITEMS_PER_CHUNK = 12


class ExtractionUnavailable(RuntimeError):
    """LLM 不可用（调用方如实跳过抽取层）。"""


def load_entity_types(kb_dir: str | None = None) -> list[str]:
    """从 kb/settings.yaml 读本体类型；读不到用 DEFAULT_ENTITY_TYPES。"""
    from pathlib import Path

    import app_config

    path = Path(kb_dir or app_config.KNOWLEDGE_KB_DIR) / "settings.yaml"
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        types = data.get("entity_types") or []
        return [str(t) for t in types] or list(DEFAULT_ENTITY_TYPES)
    except Exception:
        return list(DEFAULT_ENTITY_TYPES)


def build_prompt(chunk_text: str, entity_types: list[str]) -> str:
    types = "、".join(entity_types)
    return (
        "你是声呐装备知识图谱构建专家。从以下知识片段中抽取实体与关系，"
        f"实体类型只能取：{types}。仅输出 JSON 对象：\n"
        '{"entities": [{"type": "<本体类型>", "name": "<实体名>", '
        '"description": "<一句话描述>"}],\n'
        ' "relations": [{"source": "<实体名>", "target": "<实体名>", '
        '"relation": "<英文大写关系动词>", "description": "<一句话>"}]}\n'
        "要求：只用片段中明确出现的事实；最多 12 条；不确定的不要编。\n\n"
        f"=== 知识片段 ===\n{chunk_text[:3000]}"
    )


def _clean(data: Any, entity_types: list[str], chunk_index: int) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    out: list[dict[str, Any]] = []
    for e in (data.get("entities") or [])[:MAX_ITEMS_PER_CHUNK]:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or "").strip()[:128]
        etype = str(e.get("type") or "").strip()
        if not name or etype not in entity_types:
            continue  # 越界类型/无名实体一律丢弃
        out.append({
            "kind": "entity", "etype": etype, "name": name, "chunk_index": chunk_index,
            "payload": {"description": str(e.get("description") or "")[:300]},
        })
    allowed_names = {o["name"] for o in out}
    for r in (data.get("relations") or [])[:MAX_ITEMS_PER_CHUNK]:
        if not isinstance(r, dict):
            continue
        src, dst = str(r.get("source") or "").strip()[:128], str(r.get("target") or "").strip()[:128]
        rel = str(r.get("relation") or "").strip()[:32]
        if not src or not dst or not rel:
            continue
        if src not in allowed_names or dst not in allowed_names:
            continue  # 关系两端必须为本片段已抽实体（防编造悬挂关系）
        out.append({
            "kind": "relation", "etype": rel.upper(), "name": f"{src}->{dst}",
            "chunk_index": chunk_index,
            "payload": {"source": src, "target": dst,
                        "description": str(r.get("description") or "")[:300]},
        })
    return out


def extract_from_chunks(
    chunks: list[dict[str, Any]], llm: Any, entity_types: list[str] | None = None
) -> list[dict[str, Any]]:
    """逐 chunk 抽取并清洗。llm 缺失/调用失败抛 ExtractionUnavailable（调用方如实处理）。"""
    if llm is None:
        raise ExtractionUnavailable("LLM 未注入")
    types = entity_types or load_entity_types()
    out: list[dict[str, Any]] = []
    for chunk in chunks:
        raw = llm.chat(
            [
                {"role": "system", "content": "你是知识图谱构建专家。仅输出 JSON 对象。"},
                {"role": "user", "content": build_prompt(chunk["text"], types)},
            ],
            max_tokens=2048,
            temperature=0.1,
        )
        data = parse_llm_json(raw) if isinstance(raw, str) else None
        if data is None:
            logger.warning("chunk %s 抽取输出不可解析，跳过该 chunk", chunk.get("chunk_index"))
            continue
        out += _clean(data, types, chunk.get("chunk_index"))
    return out
