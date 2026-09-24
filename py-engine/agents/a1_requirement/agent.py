"""A1 需求解析 Agent（T1.3）。

模块边界（宪法 §3.1 / P2 §2.3）：拆解需求、规划 4~8 条子查询、并行检索；
**不推导几何参数、不赋值**（几何在 A2/A3）。
LLM 白名单用途：需求理解与子查询规划（A-07）。

流程（迁移自旧 knowledge_stage ★拆用）：
  IR0.raw_text
    → LLM 一次调用产出 objectives/missing_items/sub_queries（失败走关键词兜底，如实降级）
    → 并行检索（knowledge 三级降级服务，A-14）
    → IR1（契约校验，A-04）
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Optional

from agents.a1_requirement.prompts import (
    FALLBACK_QUERIES_TEMPLATE,
    PLAN_PROMPT,
    RETRIEVAL_DIMENSIONS,
    SYSTEM_PROMPT,
)
from ir_contract import validate_ir

logger = logging.getLogger("sonarcad.a1")


def parse_llm_json(raw: str) -> Optional[dict]:
    """从 LLM 输出提取 JSON 对象（直接解析 → markdown 围栏 → 首尾大括号）。"""
    raw = raw.strip()
    for candidate in (
        raw,
        *re.findall(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL),
    ):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    start, end = raw.find("{"), raw.rfind("}")
    if 0 <= start < end:
        try:
            obj = json.loads(raw[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


class A1Agent:
    """A1 需求解析。llm.chat / kb_batch 依赖注入（独立调试与测试可替换）。"""

    def __init__(
        self,
        llm: Any = None,
        kb_batch: Optional[Callable[[list[str]], list[Any]]] = None,
        max_workers: int = 8,
    ):
        self._llm = llm
        self._kb_batch = kb_batch
        self._max_workers = max_workers

    # ------------------------------------------------------------------
    def run(self, ir0: dict[str, Any], kb_ids: list[str] | None = None) -> dict[str, Any]:
        """IR0 → IR1（已过契约校验）。

        kb_ids（PHASE-006）：会话选定知识库；None = 未限定（全库/Agent 路由语义由上层定）。
        传给 kb_batch 时恒为列表（None 转全量语义 []，保持检索接口单形）。
        """
        raw_text = ir0["raw_text"]

        plan = self._plan(raw_text)
        sub_queries = plan["sub_queries"]
        logger.info("A1 规划 %s 条子查询（llm=%s）", len(sub_queries), plan["source"])

        retrieved = self._retrieve(sub_queries, kb_ids if kb_ids is not None else None)

        ir1: dict[str, Any] = {
            "objectives": plan["objectives"],
            "missing_items": plan["missing_items"],
            "sub_queries": sub_queries,
            "retrieved_context": retrieved,
        }
        # 检索全败如实补充 missing（A-16）
        if retrieved and all(r["doc_id"] == "" for r in retrieved):
            ir1["missing_items"].append("知识库检索无可用结果，相关知识缺失（如实标注）")
        return validate_ir("IR1", ir1)

    # ------------------------------------------------------------------
    # Step 1: 需求理解 + 查询规划（LLM，白名单用途）
    # ------------------------------------------------------------------
    def _plan(self, raw_text: str) -> dict[str, Any]:
        prompt = PLAN_PROMPT.format(dimensions="\n".join(f"- {d}" for d in RETRIEVAL_DIMENSIONS))
        if self._llm is not None:
            try:
                raw = self._llm.chat(
                    [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"用户需求：{raw_text}\n\n{prompt}"},
                    ],
                    max_tokens=1024,
                    temperature=0.2,
                )
                obj = parse_llm_json(raw)
                objectives = [
                    {
                        "dimension": str(o["dimension"]),
                        "value": o.get("value"),
                        "explicit": bool(o.get("explicit", True)),
                    }
                    for o in obj.get("objectives", [])
                    if isinstance(o, dict) and o.get("dimension")
                ] if obj else []
                sub_queries = [
                    str(q).strip() for q in (obj or {}).get("sub_queries", []) if str(q).strip()
                ][:8]
                missing = [str(m) for m in (obj or {}).get("missing_items", []) if str(m).strip()]
                if 4 <= len(sub_queries) <= 8:
                    return {
                        "objectives": objectives,
                        "missing_items": missing,
                        "sub_queries": sub_queries,
                        "source": "llm",
                    }
                logger.warning("A1 LLM 规划子查询数量 %s 越界，使用兜底", len(sub_queries))
            except Exception as exc:
                logger.warning("A1 LLM 规划失败，使用兜底: %s", exc)
        return self._fallback_plan(raw_text)

    @staticmethod
    def _fallback_plan(raw_text: str) -> dict[str, Any]:
        """兜底（LLM 不可用/格式异常）：关键词模板查询；目标只登记原始需求本身，缺失维度如实全列。"""
        req = raw_text.strip()
        return {
            "objectives": [{"dimension": "原始需求", "value": req, "explicit": True}],
            # 降级原因只说一遍（首项），其余为纯维度名——避免界面整段重复
            "missing_items": ["LLM 规划不可用（降级解析），以下维度未结构化解析，需后续补充", *RETRIEVAL_DIMENSIONS],
            "sub_queries": [t.format(req=req) for t in FALLBACK_QUERIES_TEMPLATE],
            "source": "fallback",
        }

    # ------------------------------------------------------------------
    # Step 2: 并行检索（kb_batch 注入；降级到串行，复刻旧执行策略）
    # ------------------------------------------------------------------
    def _retrieve(self, sub_queries: list[str], kb_ids: list[str] | None = None) -> list[dict[str, Any]]:
        if not sub_queries or self._kb_batch is None:
            return []

        def _one(q: str) -> dict[str, Any]:
            try:
                r = self._kb_single(q, kb_ids)
                return {
                    "doc_id": r["doc_id"],
                    "location": r["location"],
                    "snippet": r["snippet"][:2000],
                    **({"kb_id": r["kb_id"]} if r.get("kb_id") else {}),
                    **({"kind": r["kind"]} if r.get("kind") else {}),
                }
            except Exception as exc:
                logger.warning("A1 子查询失败 q=%s: %s", q[:40], exc)
                return {"doc_id": "", "location": "", "snippet": f"[查询错误] {exc}"}

        if len(sub_queries) == 1:
            return [_one(sub_queries[0])]
        try:
            with ThreadPoolExecutor(max_workers=min(len(sub_queries), self._max_workers)) as pool:
                futures = {pool.submit(_one, q): q for q in sub_queries}
                return [f.result(timeout=180) for f in as_completed(futures)]
        except Exception as exc:
            logger.warning("A1 并行检索失败，降级串行: %s", exc)
            return [_one(q) for q in sub_queries]

    def _kb_single(self, query: str, kb_ids: list[str] | None = None) -> dict[str, str]:
        """单条检索。批量接口可用时走批（保持返回对齐），否则逐条。"""
        if self._kb_batch is None:
            raise RuntimeError("kb 不可用")
        if kb_ids is not None and kb_ids:
            results = self._kb_batch([query], kb_ids)
        elif kb_ids is not None:
            # 空列表 = "全部库"范围语义（knowledge.query 的 scoped 分支）
            results = self._kb_batch([query], [])
        else:
            results = self._kb_batch([query])
        result = results[0]
        refs = getattr(result, "refs", []) or []
        ref = refs[0] if refs else {}
        out = {
            "doc_id": ref.get("doc_id", "knowledge-base"),
            "location": ref.get("location", "unknown"),
            "snippet": result.content,
        }
        if ref.get("kb_id"):
            out["kb_id"] = ref["kb_id"]
        if ref.get("kind"):
            out["kind"] = ref["kind"]
        return out
