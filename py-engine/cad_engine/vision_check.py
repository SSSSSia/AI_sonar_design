# -*- coding: utf-8 -*-
"""CAD 视觉校验旁路（T18.2，PHASE-018；契约=ADR-017）。

A3 Step4 落盘后的可选步骤：STEP → 三视图渲染（复用 render.py，子进程纪律）→
VLM 结构化判词（复用 llm/vision.py describe_images，一次重试）→ IR3 vision_check
字段。定位=**gate2 人工裁决的参考信号**：不进 A4/CSR 分母（A-07）、不进修复重试
计数（A-13）；判词全文落 IR3 verdict_text 人工可核对（A-16）；渲染/VLM 失败如实记
skipped 不造「通过」结论。

CAD_VISION_CHECK 默认关（app_config，布尔模式同 REF_*）；关闭时 A3 不调用本模块，
IR3 不含 vision_check 字段（旧形态逐字节一致=回滚保证）。
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import app_config as _cfg
from cad_engine import render
from llm import vision as llm_vision
from storage import object_store

logger = logging.getLogger("sonarcad.vision_check")

CHECK_PROMPT = (
    "这是由 CAD 脚本生成的声呐装备三维模型的组合三视图（左 iso / 中 side / 右 top）。"
    "以审阅参考视角检查是否存在明显几何异常：结构悬空或断裂、阵列数量与常规构型明显不符、"
    "形状比例明显失真、部件穿透或被轴横穿等。只输出 JSON，不要 markdown 代码块，格式："
    '{"findings": [{"item": "简短异常名", "severity": "warning 或 info", "detail": "位置与现象"}], '
    '"overall_note": "一句话总评"}；未见明显异常则 findings 为空数组。'
)

_VIEWS_PNG_KEY_TPL = "{session_id}/a3/{token}/views.png"  # 会话域留档（step-models 桶）


def parse_verdict(text: str) -> Optional[list[dict[str, Any]]]:
    """VLM 判词全文 → findings 列表；解析失败返回 None（全文仍落 verdict_text，A-16）。

    容忍 markdown 围栏与前后杂文（截取首个 { 到末个 }）；findings 非列表视为解析失败
    不造结构；空列表=判词未见明显异常（合法解析结果，不等于「通过」）。
    """
    if not text:
        return None
    cleaned = re.sub(r"```[a-zA-Z]*", "", str(text))
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start:end + 1])
    except (ValueError, TypeError):
        return None
    findings = data.get("findings") if isinstance(data, dict) else None
    if not isinstance(findings, list):
        return None
    out: list[dict[str, Any]] = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        item = str(f.get("item") or "").strip()
        detail = str(f.get("detail") or "").strip()
        if not item and not detail:
            continue
        severity = "warning" if str(f.get("severity") or "").strip() == "warning" else "info"
        out.append({"item": item or "未命名疑点", "severity": severity, "detail": detail})
    return out


def run_vision_check(session_id: str, step_path: str | Path) -> dict[str, Any]:
    """旁路主入口（**永不抛**）：渲染→views.png 留档→VLM 判词→结构化。

    返回 IR3 vision_check 形态 {"status": "checked"|"skipped", ...}；任一步失败如实
    降级（A-16），已得字段保留（材料留档）；调用方（A3 挂点）不因本模块任何异常失败。
    """
    token = f"{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    try:
        rendered = render.render_step_views(step_path)
    except Exception as exc:  # render 声明不抛，防御性兜底（A-16）
        rendered = {"ok": False, "png": None, "error": f"render exception: {exc}"}
    if not rendered.get("ok") or not rendered.get("png"):
        detail = str(rendered.get("error") or "unknown")[:200]
        logger.info("vision_check skipped（渲染失败）: %s", detail)
        return {"status": "skipped", "detail": f"render_failed: {detail}"}

    png: bytes = rendered["png"]
    out: dict[str, Any] = {
        "status": "checked",
        "model": _cfg.LLM_VISION_MODEL,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    try:
        png_key = _VIEWS_PNG_KEY_TPL.format(session_id=session_id, token=token)
        out["views_png"] = object_store.upload_bytes(
            png, _cfg.BUCKET_STEP_MODELS, png_key, "image/png")
    except Exception as exc:  # 留档缺位不阻断判词（判词信号优先）
        logger.warning("vision_check views.png 上传失败（留档缺位）: %s", exc)

    text: Optional[str] = None
    for _attempt in range(2):  # Qwen3-VL 瞬态超时实测存在（T17.5），重试一次
        text = llm_vision.describe_images([png], CHECK_PROMPT)
        if text:
            break
    if not text:
        out["status"] = "skipped"
        suffix = "，views.png 留档已上传" if out.get("views_png") else ""
        out["detail"] = f"vlm_failed: 判词两次尝试均失败（供应商/超时）{suffix}"
        logger.info("vision_check skipped（VLM 失败）: %s", out["detail"])
        return out

    out["verdict_text"] = text
    findings = parse_verdict(text)
    if findings is not None:
        out["findings"] = findings
    logger.info(
        "vision_check 完成: findings=%s views_png=%s",
        len(findings) if findings is not None else "parse_failed",
        bool(out.get("views_png")),
    )
    return out
