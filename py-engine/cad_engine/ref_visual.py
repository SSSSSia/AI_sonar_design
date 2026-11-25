# -*- coding: utf-8 -*-
"""范例库视觉通道编排（T17.5，PHASE-017；契约=ADR-016 §5，caption-then-embed）。

步骤链（全部 best-effort，逐步骤如实 stage 返回——A-16 不伪造成功）：
  沙箱重放 .py 出 STEP（run_in_subprocess，deposit 链同款）
  → render_step_views 三子图 PNG（子进程纪律）
  → upload_bytes MinIO ref-scripts 桶 {ref_id}/v{version}/views/views.png（可回看）
  → describe_images 中文结构化 caption（GLM-4.5V/Qwen2.5-VL，llm/vision.py）
  → embed_texts（bge-m3 既有链）→ Milvus ref_visuals 集合
    （doc_id=ref_id / location=v{version} / **text=caption 全文——人工可核对**，A-16）

查询侧：用户上传装备图 → 同款 caption → 同空间检索 → active 过滤（get_active_rows
ref_id 集合，调用方承担）。全局开关 app_config.REF_VISUAL_ENABLED **默认关**（参考域
纪律同 T17.4；flag 在 API 钩子/端点层，本模块函数无条件执行供回填脚本直调）。
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("sonarcad.refcode.visual")

CAPTION_PROMPT = (
    "这是同一个声呐装备三维模型的组合三视图（iso/side/top）。用中文描述该装备："
    "整体形态与安装形式、主要组成部分、显著几何特征（圆柱/锥/板/阵列/环等）。"
    "只输出描述正文，不要客套或分析过程。"
)


def _store() -> Any:
    from kb.vector_store import ChunkVectorStore
    import app_config

    return ChunkVectorStore(collection=app_config.KB_REF_VISUAL_COLLECTION)


def sync_ref_visual(row: dict[str, Any], content: str) -> dict[str, Any]:
    """全链视觉同步。返回逐步骤诚实 dict：
    {ok, stage: sandbox|render|upload|caption|embed|done, detail, caption, png_key}。
    任一步失败即返回（ok=False + stage + detail），不抛不阻塞入库。"""
    ref_id = str(row.get("ref_id"))
    version = row.get("version")
    from cad_engine.render import render_step_views, views_object_key
    from cad_engine.sandbox import run_in_subprocess

    # ① 沙箱重放出 STEP（范例行只存 .py；STEP 是渲染输入）
    replay = run_in_subprocess(content, time_limit_s=120)
    if not replay.get("ok") or not replay.get("step_files"):
        return {"ok": False, "stage": "sandbox",
                "detail": (replay.get("error") or "未产出 STEP")[:200],
                "caption": "", "png_key": ""}
    step_file = replay["step_files"][0]

    # ② 三视图渲染（子进程）
    rendered = render_step_views(step_file)
    if not rendered.get("ok"):
        return {"ok": False, "stage": "render", "detail": rendered.get("error", "")[:200],
                "caption": "", "png_key": ""}

    # ③ MinIO 留档（可回看）
    from storage import object_store

    png_key = views_object_key(ref_id, version)
    try:
        object_store.upload_bytes(rendered["png"], "ref-scripts", png_key, "image/png")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "upload", "detail": str(exc)[:200],
                "caption": "", "png_key": png_key}

    # ④ VLM 中文 caption（人工可核对：caption 全文落 Milvus text 字段并随查询回显）
    from llm.vision import describe_images

    caption = describe_images([rendered["png"]], CAPTION_PROMPT)
    if not caption:
        return {"ok": False, "stage": "caption", "detail": "VLM 未返回描述",
                "caption": "", "png_key": png_key}

    # ⑤ embedding → Milvus ref_visuals
    try:
        from kb.embeddings import embed_texts

        vectors = embed_texts([caption])
        if not vectors:
            return {"ok": False, "stage": "embed", "detail": "embedding 供应商不可用",
                    "caption": caption[:100], "png_key": png_key}
        written = _store().upsert_chunks(
            ref_id,
            [{"chunk_index": 0, "location": f"v{version}", "text": caption[:16000]}],
            [vectors[0]],
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "embed", "detail": str(exc)[:200],
                "caption": caption[:100], "png_key": png_key}
    logger.info("范例视觉同步完成: %s v%s (%d 行) caption=%s…",
                ref_id, version, written, caption[:40])
    return {"ok": True, "stage": "done", "detail": f"{written} 行",
            "caption": caption[:100], "png_key": png_key}


def delete_ref_visual(ref_id: str) -> bool:
    """停用清视觉向量（best-effort；PNG 留 MinIO 不删=历史可回看）。"""
    try:
        deleted = _store().delete_doc(str(ref_id))
        logger.info("范例视觉向量已删除: %s (%d 行)", ref_id, deleted)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("范例视觉向量删除失败: %s: %s", ref_id, exc)
        return False


def search_visual_by_image(png: bytes, top_k: int = 5) -> dict[str, tuple[int, float, str]]:
    """用户装备图 → caption → 同空间检索。{ref_id: (rank, score, caption)}；失败空 dict。"""
    if not png:
        return {}
    try:
        from llm.vision import describe_images

        caption = describe_images([png], CAPTION_PROMPT)
        if not caption:
            logger.warning("查询图 caption 失败（视觉检索降级）")
            return {}
        from kb.embeddings import embed_texts

        vectors = embed_texts([caption])
        if not vectors:
            return {}
        hits = _store().search(vectors[0], top_k=top_k)
        out: dict[str, tuple[int, float, str]] = {}
        for rank, h in enumerate(hits, start=1):
            ref_id = str(h.get("doc_id") or "")
            if ref_id and ref_id not in out:
                out[ref_id] = (rank, float(h.get("score") or 0.0),
                               str(h.get("text") or ""))
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("视觉检索失败（如实降级）: %s", exc)
        return {}
