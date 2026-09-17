"""A5 知识沉淀 Agent（T2.3，IR1 + IR2 + IR4 → IR5）。

模块边界（宪法 §3.1 / A-10）：**只产出 pending 候选，绝不直接写图谱**——
回写必须经知识审阅关人工确认（workflow 裁决 → py-engine /knowledge/writeback）。

候选来源：
  1. LLM 整理（A-07 白名单：沉淀候选整理；输入仅 IR2/IR4 结构化事实，禁止虚构）；
  2. LLM 不可用/解析失败 → 确定性兜底（A-13）：通过校验的 formula 参数→constraint 候选、
     case 参数组→case 候选。

去重（dedup_status）：KB 文本比对（现态）+ Neo4j 查重（可达时）+ 批内同名；
duplicate 候选附 dedup_refs 证据供审阅人裁决。输出经 ir5.schema.json 契约校验（A-04）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agents.a1_requirement.agent import parse_llm_json
from agents.a5_knowledge.prompts import CANDIDATE_EXTRACTION_PROMPT
from ir_contract import validate_ir

logger = logging.getLogger("sonarcad.a5")

MAX_CANDIDATES = 8


class A5Agent:
    """A5 知识沉淀。llm/kb/graph 注入（测试/CLI 可替换）。

    kb: 提供 find_name_matches(name) -> list[dict]（GraphRAGService）；
    graph: 提供 find_duplicates(name) -> list[dict]（Neo4jStore，可为 None）。
    """

    def __init__(self, llm: Any = None, kb: Any = None, graph: Any = None):
        self._llm = llm
        self._kb = kb
        self._graph = graph

    # ------------------------------------------------------------------
    def run(
        self,
        ir1: dict[str, Any] | None,
        ir2: dict[str, Any],
        ir4: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params = ir2.get("parameters", [])
        error_param_ids = self._error_param_ids(ir4)

        candidates, llm_ok = self._draft_candidates(ir1, ir2, ir4)
        if not candidates:
            candidates = self._fallback_candidates(params, error_param_ids)
            if candidates:
                logger.info("A5 使用确定性兜底候选（%d 条）", len(candidates))
        if not llm_ok and not candidates:
            logger.warning("A5：LLM 不可用且无结构化事实可兜底，产出空候选（如实）")
        candidates = candidates[:MAX_CANDIDATES]
        candidates = self._dedup(candidates)

        ir5 = {"candidates": candidates, "writeback_status": "pending"}
        return validate_ir("IR5", ir5)

    # ------------------------------------------------------------------
    @staticmethod
    def _error_param_ids(ir4: dict[str, Any] | None) -> set[str]:
        """被 error 级问题点名的参数不沉淀（A-10：不写未经验证的决策）。"""
        ids: set[str] = set()
        for issue in (ir4 or {}).get("issues", []):
            if issue.get("severity") == "error":
                ids.update(issue.get("related_params", []))
        return ids

    # ------------------------------------------------------------------
    def _draft_candidates(
        self,
        ir1: dict[str, Any] | None,
        ir2: dict[str, Any],
        ir4: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """LLM 整理候选。返回 (candidates, llm_ok)；候选 payload 的 params 值回核对 IR2 原表。"""
        if self._llm is None:
            return [], False
        param_table = "\n".join(
            f"- {p['id']} | {p['name']} = {p['value']} {p['unit']} | {p['source_type']}"
            for p in ir2.get("parameters", [])
        ) or "（无参数）"
        validation = (
            f"overall={ir4.get('overall')}; "
            + "; ".join(
                f"[{i.get('severity')}] {i.get('constraint_id')}: {i.get('message', '')[:120]}"
                for i in (ir4 or {}).get("issues", [])[:10]
            )
            if ir4 else "（无校验报告）"
        )
        objectives = "\n".join(
            f"- {o.get('dimension')}: {o.get('value')}" for o in (ir1 or {}).get("objectives", [])
        ) or "（无目标记录）"
        try:
            raw = self._llm.chat(
                [
                    {"role": "system", "content": "你是声呐装备设计知识整理专家。仅输出 JSON 对象。"},
                    {"role": "user", "content":
                        f"{CANDIDATE_EXTRACTION_PROMPT}\n\n=== 设计目标 ===\n{objectives}"
                        f"\n\n=== 参数表（id|名称=值 单位|来源）===\n{param_table}"
                        f"\n\n=== 校验结论 ===\n{validation}"},
                ],
                max_tokens=2048,
                temperature=0.2,
            )
            data = parse_llm_json(raw)
            if not data or not isinstance(data.get("candidates"), list):
                raise ValueError("LLM 输出缺少 candidates 数组")
            out = self._sanitize(data["candidates"], ir2)
            return out, True
        except Exception as exc:
            logger.warning("A5 LLM 候选整理失败，转兜底: %s", exc)
            return [], False

    def _sanitize(self, items: Any, ir2: dict[str, Any]) -> list[dict[str, Any]]:
        """LLM 候选清洗：类型白名单 + payload 结构校验 + case 数值与 IR2 原表核对。"""
        by_name = {str(p.get("name")): p for p in ir2.get("parameters", [])}
        out: list[dict[str, Any]] = []
        for item in items[:MAX_CANDIDATES]:
            if not isinstance(item, dict) or item.get("type") not in ("relation", "case", "constraint"):
                continue
            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue
            if item["type"] == "case":
                checked: list[dict[str, Any]] = []
                for p in payload.get("params", [])[:20]:
                    ref = by_name.get(str(p.get("name")))
                    if ref is None:
                        continue  # 原表没有的参数一律丢弃（禁止虚构）
                    checked.append({"name": ref["name"], "value": ref["value"], "unit": ref.get("unit", "-")})
                if not checked:
                    continue
                payload = {**payload, "params": checked}
            if item["type"] == "relation":
                if not (payload.get("from") or {}).get("name") or not (payload.get("to") or {}).get("name"):
                    continue
            if item["type"] == "constraint" and not str(payload.get("name") or "").strip():
                continue
            payload = self._attach_source_traces(payload, by_name)
            out.append({"type": item["type"], "payload": payload, "dedup_status": "new"})
        return out

    @staticmethod
    def _attach_source_traces(payload: dict[str, Any], by_name: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """候选回填来源参数的 trace_id（溯源链 A5 assign 行的继承依据，T2.4）。"""
        names: list[str] = []
        if payload.get("name"):
            names.append(str(payload["name"]))
        for p in payload.get("params", []) or []:
            if p.get("name"):
                names.append(str(p["name"]))
        for side in ("from", "to"):
            if (payload.get(side) or {}).get("name"):
                names.append(str(payload[side]["name"]))
        traces: list[str] = []
        for n in names:
            ref = by_name.get(n)
            if ref and ref.get("trace_id") and ref["trace_id"] not in traces:
                traces.append(ref["trace_id"])
        if traces:
            payload = {**payload, "source_trace_ids": traces}
        return payload

    # ------------------------------------------------------------------
    def _fallback_candidates(
        self, params: list[dict[str, Any]], error_param_ids: set[str]
    ) -> list[dict[str, Any]]:
        """确定性兜底：公式参数→constraint；case 参数组→case。仅取未被 error 点名的参数。"""
        candidates: list[dict[str, Any]] = []
        for p in params:
            if p.get("source_type") == "formula" and p["id"] not in error_param_ids:
                candidates.append({
                    "type": "constraint",
                    "payload": {
                        "name": f"{p['name']}（设计约束经验）",
                        "expression": f"{p['name']} = {p['value']} {p['unit']}",
                        "context": f"依据 {p.get('source_ref')}；{p.get('derivation', '')[:200]}",
                        "source_trace_ids": [p.get("trace_id", "")] if p.get("trace_id") else [],
                    },
                    "dedup_status": "new",
                })
        case_params = [
            {"name": p["name"], "value": p["value"], "unit": p.get("unit", "-")}
            for p in params
            if p.get("source_type") == "case" and p["id"] not in error_param_ids
        ]
        if case_params:
            case_traces = [
                p.get("trace_id") for p in params
                if p.get("source_type") == "case" and p["id"] not in error_param_ids and p.get("trace_id")
            ]
            candidates.append({
                "type": "case",
                "payload": {
                    "name": "设计会话案例参数组（审阅关通过）",
                    "params": case_params,
                    "context": "经人工审阅关通过的设计会话中采纳的案例参数取值",
                    "source_trace_ids": [t for t in case_traces if t],
                },
                "dedup_status": "new",
            })
        return candidates

    # ------------------------------------------------------------------
    def _dedup(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """三级去重：批内同名 → KB 文本 → Neo4j（可达时）。duplicate 附证据。"""
        seen: set[str] = set()
        for cand in candidates:
            name = str((cand.get("payload") or {}).get("name") or "")
            normalized = name.replace(" ", "").lower()
            refs: list[dict[str, str]] = []
            if normalized in seen:
                refs.append({"doc_id": "batch", "location": "本批次同名候选"})
            seen.add(normalized)

            if self._kb is not None:
                try:
                    refs += self._kb.find_name_matches(name)
                except Exception as exc:
                    logger.warning("A5 KB 去重失败（忽略，不影响候选）: %s", exc)

            if self._graph is not None:
                try:
                    for rec in self._graph.find_duplicates(name):
                        refs.append({
                            "doc_id": "neo4j",
                            "location": f"{rec.get('label', '?')}:{rec.get('key', '?')}",
                        })
                except Exception as exc:
                    logger.warning("A5 图谱去重失败（忽略，仅文本级去重）: %s", exc)

            if refs:
                cand["dedup_status"] = "duplicate"
                cand["payload"] = {**cand["payload"], "dedup_refs": refs}
        return candidates
