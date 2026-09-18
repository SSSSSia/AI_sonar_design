"""首样入库（T3.3 DoD）：towed_array_cad_knowledge.txt 全流程 + 三库播种。

流程：上传建档 → 解析 → 分块 → LLM 抽取 → 自动确认（首样为官方种子文档）→ 异步构建
→ 播种 kb_formulas（声学约束公式节）+ kb_constraints（硬约束节）→ 检索验证。

用法（py-engine 目录）：
    python -m knowledge.first_sample [--skip-extract]
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("sonarcad.first_sample")


def _kb_file() -> Path:
    import app_config

    return Path(app_config.KNOWLEDGE_KB_DIR) / "input" / "towed_array_cad_knowledge.txt"


def _seed_registries(kb_text: str) -> dict[str, int]:
    """确定性解析 KB 文件：声学约束公式 → kb_formulas（复用 solver REGISTRY 表达式）；
    硬约束节 → kb_constraints。幂等（key/name 冲突即更新）。"""
    from solver.formulas import REGISTRY
    from storage import registry_store

    formula_count = 0
    for spec in REGISTRY.values():
        registry_store.create_row("formulas", {
            "key": spec.key,
            "name": spec.name,
            "expr": spec.expr,
            "symbols": list(spec.symbols),
            "unit": spec.unit,
            "source_ref": spec.source_ref,
            "enabled": True,
            "confidence": "high",
        })
        formula_count += 1

    constraint_count = 0
    section = re.search(r"### 硬约束（[^）]*）\n(.*?)\n###", kb_text, re.DOTALL)
    if section:
        for m in re.finditer(r"^\d+\.\s*\*\*(.+?)\*\*[：:](.+)$", section.group(1), re.MULTILINE):
            registry_store.create_row("constraints", {
                "name": m.group(1).strip(),
                "expression": m.group(2).strip()[:500],
                "applies_to": "",
                "severity_hint": "error",
                "source_ref": "towed_array_cad_knowledge.txt#设计约束/硬约束",
                "enabled": True,
                "confidence": "high",
            })
            constraint_count += 1
    return {"formulas": formula_count, "constraints": constraint_count}


def run(skip_extract: bool = False) -> dict:
    import app_config
    from kb.chunker import chunk_units
    from kb.extractor import ExtractionUnavailable, extract_from_chunks
    from kb.parsers import parse_document
    from storage import kb_store, registry_store
    from storage.object_store import upload_bytes

    path = _kb_file()
    data = path.read_bytes()
    logger.info("首样文件: %s（%d 字节）", path, len(data))

    # ① 建档 + MinIO 原件
    doc = kb_store.create_document(
        filename=path.name, fmt="txt", size_bytes=len(data),
        bucket=app_config.BUCKET_KB_DOCS, object_key=f"first-sample/{path.name}",
        uploaded_by="first-sample",
    )
    doc_id = doc["doc_id"]
    upload_bytes(data, app_config.BUCKET_KB_DOCS, f"{doc_id}/{path.name}", "text/plain")

    # ② 解析 + 分块
    parsed = parse_document(path.name, data)
    kb_store.update_document(
        doc_id, status="parsed",
        parsed_units=[{"location": u.location, "text": u.text} for u in parsed.units],
        section_count=parsed.section_count or None,
    )
    chunks = chunk_units(parsed.units, size=app_config.KB_CHUNK_SIZE,
                         overlap=app_config.KB_CHUNK_OVERLAP)
    kb_store.replace_chunks(doc_id, chunks)
    kb_store.update_document(doc_id, status="chunked")
    logger.info("解析 %d 单元 → %d chunks", len(parsed.units), len(chunks))

    # ③ 抽取 + 自动确认（首样为官方种子文档，可信）
    extraction_note = "skipped"
    if not skip_extract:
        try:
            from llm.adapter import llm_adapter

            items = extract_from_chunks(chunks, llm_adapter)
            kb_store.replace_extractions(doc_id, items)
            for row in kb_store.get_extractions(doc_id, "pending"):
                kb_store.set_extraction_status(row["id"], "confirmed")
            extraction_note = f"{len(items)} 条（已自动确认——官方种子文档）"
            logger.info("抽取 %d 条并确认", len(items))
        except ExtractionUnavailable as exc:
            logger.warning("抽取跳过（不影响向量/语料层）: %s", exc)
            extraction_note = f"skipped: {exc}"

    # ④ 构建（三层）
    from kb.index_build import build_documents

    build = build_documents([doc_id])

    # ⑤ 三库播种
    kb_text = data.decode("utf-8", errors="replace")
    seeded = _seed_registries(kb_text)
    from solver import standards as std_mod

    std_mod.reset_cache()
    logger.info("三库播种: %s", seeded)

    result = {
        "doc_id": doc_id,
        "chunks": len(chunks),
        "extractions": extraction_note,
        "build": build,
        "seeded": seeded,
    }
    logger.info("首样入库完成: %s", json.dumps(result, ensure_ascii=False, default=str))
    return result


def verify(query: str = "声呐方程 指向性指数") -> dict:
    """检索验证：期望命中 graph 或 vector 级（首样 DoD）。"""
    from knowledge.service import get_knowledge_service

    svc = get_knowledge_service()
    result = svc.query(query)
    return {"query": query, "level": result.level, "degraded": result.degraded_from,
            "refs": result.refs, "content_head": result.content[:200]}


if __name__ == "__main__":
    skip = "--skip-extract" in sys.argv
    out = run(skip_extract=skip)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
