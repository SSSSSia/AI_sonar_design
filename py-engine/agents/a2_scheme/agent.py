"""A2 方案生成 Agent（T1.4，IR1 → IR2 系统枢纽）。

模块边界（宪法 §3.1）：产出设计文档 + 带出处参数表；**不解复杂隐式方程、不写 CAD 代码**。
- A-06：每个参数四件套（source_type/source_ref/derivation/trace_id）缺一不得进 IR2；
- A-07：formula 参数数值由 solver 代码计算（本 Agent 不算公式数值），LLM 仅做文档组织与
  案例参数提议（proposed 必须标 estimated + 依据）；
- IR2 校验走 contracts/ir/ir2.schema.json（A-04）。

流程：
  IR1.objectives/retrieved_context
    → LLM 参数提取（retrieved→case / proposed→estimated；LLM 不可用跳过）
    → solver.formulas.evaluate_available（公式参数，代码计算）
    → LLM 设计文档整合（CONSOLIDATION ★迁移；不可用时降级为检索摘录文档并如实标注）
    → IR2
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from agents.a1_requirement.agent import parse_llm_json
from agents.a2_scheme.prompts import CONSOLIDATION_TEMPLATE, EXTRACT_PARAMS_PROMPT
from ir_contract import validate_ir
from solver import formulas

logger = logging.getLogger("sonarcad.a2")


def _new_trace_id() -> str:
    return f"tr-{uuid.uuid4().hex[:12]}"


class A2Agent:
    """A2 方案生成。llm 注入（测试/CLI 可替换）。"""

    def __init__(self, llm: Any = None):
        self._llm = llm

    # ------------------------------------------------------------------
    def run(self, ir1: dict[str, Any]) -> dict[str, Any]:
        objectives = ir1.get("objectives", [])
        contexts = ir1.get("retrieved_context", [])

        known: dict[str, Any] = {}
        for o in objectives:
            if o.get("value") is not None:
                known[o["dimension"]] = o["value"]

        # Step 1: LLM 案例参数提取（不可用→空，如实）
        candidates, llm_ok = self._extract_candidates(known, contexts)
        if not llm_ok:
            logger.warning("A2 LLM 不可用：案例参数提取跳过（降级模式）")

        # Step 2: 公式参数（代码计算，A-07）
        known_all = dict(known)
        for c in candidates:
            known_all.setdefault(c["name"], c["value"])
        formula_params = formulas.evaluate_available(known_all)

        # Step 3: 设计文档（LLM 整合 ★迁移；不可用→降级摘录文档）
        if llm_ok:
            design_doc = self._consolidate(known, contexts, candidates, formula_params)
            doc_degraded = False
        else:
            design_doc = self._fallback_document(known, contexts)
            doc_degraded = True

        # Step 4: 组装 IR2（四件套补全 + 编号 + trace）
        parameters: list[dict[str, Any]] = []
        seq = 1

        def _add(p: dict[str, Any]) -> None:
            nonlocal seq
            parameters.append({
                "id": f"P-{seq:03d}",
                "name": p["name"],
                "value": p["value"],
                "unit": p.get("unit", "-"),
                "source_type": p["source_type"],
                "source_ref": p.get("source_ref", ""),
                "derivation": p.get("derivation", ""),
                "confidence": p.get("confidence", "medium"),
                "trace_id": _new_trace_id(),
            })
            seq += 1

        for c in candidates:
            _add(c)
        for f in formula_params:
            _add(f)
        # 显式目标登记（estimated，依据=用户需求原文；保证目标可追溯进入参数表）
        for o in objectives:
            if o.get("value") is None or o["dimension"] == "原始需求":
                continue
            _add({
                "name": o["dimension"],
                "value": o["value"],
                "unit": "-",
                "source_type": "estimated",
                "source_ref": "IR1.objectives",
                "derivation": "用户需求显式给出（IR1 目标登记）",
                "confidence": "high",
            })

        ir2 = {"design_document": design_doc, "parameters": parameters}
        if doc_degraded:
            # 降级标记进文档正文（A-16：不在参数里伪造出处）
            ir2["design_document"] = (
                "> ⚠ 降级模式：LLM 不可用，本文档为检索结果与目标的直接摘录，"
                "未经工程整合，参数表仅含公式计算值与显式目标。\n\n" + ir2["design_document"]
            )
        return validate_ir("IR2", ir2)

    # ------------------------------------------------------------------
    def _extract_candidates(
        self, known: dict[str, Any], contexts: list[dict[str, str]]
    ) -> tuple[list[dict[str, Any]], bool]:
        """LLM 参数提取。返回 (candidates, llm_ok)。"""
        if self._llm is None:
            return [], False
        ctx_text = "\n\n---\n\n".join(
            f"[{c.get('doc_id', '?')}#{c.get('location', '?')}]\n{c.get('snippet', '')[:3000]}"
            for c in contexts
        ) or "（无检索结果）"
        obj_text = "\n".join(f"- {k}: {v}" for k, v in known.items()) or "（无显式目标）"
        try:
            raw = self._llm.chat(
                [
                    {"role": "system", "content": "你是声呐装备结构设计参数提取专家。仅输出 JSON 数组。"},
                    {"role": "user", "content": EXTRACT_PARAMS_PROMPT.format()
                     + f"\n\n=== 检索知识 ===\n{ctx_text}\n\n=== 设计目标 ===\n{obj_text}"},
                ],
                max_tokens=2048,
                temperature=0.2,
            )
            arr = json.loads(_first_json_array(raw))
            out: list[dict[str, Any]] = []
            for item in arr[:15]:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                if item.get("source") == "proposed":
                    out.append({
                        "name": str(item["name"]),
                        "value": item.get("value"),
                        "unit": str(item.get("unit", "-")),
                        "source_type": "estimated",
                        "source_ref": "A2/llm-proposal",
                        "derivation": str(item.get("rationale", "LLM 提议")),
                        "confidence": "medium",
                    })
                else:  # retrieved：来自知识库的案例参数（不改数）
                    out.append({
                        "name": str(item["name"]),
                        "value": item.get("value"),
                        "unit": str(item.get("unit", "-")),
                        "source_type": "case",
                        "source_ref": str(item.get("rationale", "检索知识")),
                        "derivation": f"检索知识明确给出的案例参数：{item.get('rationale', '')}",
                        "confidence": "high",
                    })
            return out, True
        except Exception as exc:
            logger.warning("A2 参数提取失败: %s", exc)
            return [], False

    # ------------------------------------------------------------------
    def _consolidate(
        self,
        known: dict[str, Any],
        contexts: list[dict[str, str]],
        candidates: list[dict[str, Any]],
        formula_params: list[dict[str, Any]],
    ) -> str:
        ctx_text = "\n\n---\n\n".join(
            f"查询出处 [{c.get('doc_id', '?')}#{c.get('location', '?')}]\n{c.get('snippet', '')[:5000]}"
            for c in contexts
        ) or "（无检索结果）"
        obj_text = "\n".join(f"- {k}: {v}" for k, v in known.items()) or "（无显式目标）"
        param_table = "\n".join(
            f"- {p['name']} = {p['value']} {p['unit']}（{p['source_type']}）"
            for p in candidates + formula_params
        ) or "（暂无）"
        try:
            return self._llm.chat(
                [
                    {"role": "system", "content": "你是资深的水下声学设备结构设计工程师。直接输出 Markdown 设计文档。"},
                    {"role": "user", "content":
                     f"{CONSOLIDATION_TEMPLATE}\n\n=== 用户设计目标 ===\n{obj_text}"
                     f"\n\n=== 检索结果 ===\n{ctx_text}\n\n=== 已登记参数候选（文档须与其一致）===\n{param_table}"},
                ],
                max_tokens=8192,
                temperature=0.3,
            ).strip()
        except Exception as exc:
            logger.warning("A2 设计文档整合失败，降级摘录: %s", exc)
            return self._fallback_document(known, contexts)

    # ------------------------------------------------------------------
    @staticmethod
    def _fallback_document(known: dict[str, Any], contexts: list[dict[str, str]]) -> str:
        """降级文档：目标 + 检索摘录（诚实拼接，非工程整合）。"""
        obj_text = "\n".join(f"- **{k}**: {v}" for k, v in known.items()) or "-（无）"
        parts = [f"# 设计文档（降级模式）\n\n## 设计目标\n{obj_text}\n"]
        for i, c in enumerate(contexts, 1):
            parts.append(
                f"## 检索摘录 {i}（{c.get('doc_id', '?')} #{c.get('location', '?')}）\n\n"
                + (c.get("snippet", "")[:3000])
            )
        if len(parts) == 1:
            parts.append("\n（无检索结果——知识缺口需补充资料）")
        return "\n\n".join(parts)


def _first_json_array(raw: str) -> str:
    """提取文本中第一个 JSON 数组（直接/围栏/首尾方括号）。"""
    import re

    raw = raw.strip()
    for candidate in (raw, *re.findall(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)):
        if candidate.lstrip().startswith("["):
            return candidate
    start, end = raw.find("["), raw.rfind("]")
    if 0 <= start < end:
        return raw[start : end + 1]
    return raw
