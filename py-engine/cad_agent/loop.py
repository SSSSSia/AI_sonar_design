"""微调轮次循环体（T8.2，PHASE-008）。

自 CadAgent agent/loop.py 改造：原版是"UI 解释 LoopAction"的两阶段状态机（本地工具
本地执行）；服务端版改为**同步驱动到收敛**——工具执行经注入的 tool_executor 回调
（T8.3 实现走 tune_store 协议：建行+等插件回传；单测注入 fake）。

与原版的行为差异（如实记录）：
- LLM 交互仅 ReAct 文本协议（react_parser），无原生 tool calling 探测；
- 质量门禁不再强制"每轮必须执行代码"——纯问答轮（无工具调用）合法结束，
  script=None，不触发版本上传；门禁只拦"质量 FAIL 还想收尾"；
- 重复错误检测保留（同签名错误注入"必须换思路"警告，替代原 REPAIR_LOOP 注入）；
- 迭代上限（A-13）、质量门禁逃逸 hatch（连续 3 次阻断后放行失败）保留；
- 首轮编辑清单随 system prompt 注入（每轮新建 Runner，跨轮状态经 API 层上下文传递）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from cad_agent.prompts import TUNE_SYSTEM_PROMPT
from cad_agent.references import (
    QUALITY_FIX_MAP,
    REF_FIRST_ITERATION,
    REF_ITERATION_URGENCY,
    REF_QUALITY_FAILURE,
)
from cad_agent.react_parser import parse_react_tool_calls
from cad_agent.token_budget import trim_messages

logger = logging.getLogger("sonarcad.cad_agent")


@dataclass
class RunResult:
    """一轮微调的收敛结果（API 层据此推进 round 状态/触发上传）。"""

    success: bool
    summary: str                     # 最终助手答复（面板展示）
    iterations: int
    script: str | None = None        # 最后一次成功执行的编辑代码（None=问答轮，无几何变更）
    llm_trace: list[dict] = field(default_factory=list)
    error: str | None = None
    doc_summary: str | None = None   # 最后一次回传的模型状态（供下轮上下文）


class TuneRoundRunner:
    """单轮微调的同步循环。

    llm_chat(messages) -> str：平台 LLM 适配器（ReAct 文本协议）。
    tool_executor(name, args) -> dict：工具执行回调，返回插件结果契约
    {ok, stdout?, quality?: {status: pass|warn|fail, issues?}, doc_summary?, error?}。
    """

    def __init__(
        self,
        *,
        context: str,
        user_prompt: str,
        llm_chat,
        tool_executor,
        max_iterations: int = 8,
        max_context_tokens: int = 24000,
    ) -> None:
        self._context = context
        self._llm_chat = llm_chat
        self._tool_executor = tool_executor
        self._max_iterations = max_iterations
        self._max_tokens = max_context_tokens
        self._iteration = 0
        self._gate_blocks = 0
        self._recent_errors: list[str] = []
        self._last_quality_status: str | None = None  # pass|warn|fail（None=本轮还没执行过编辑）
        self._last_quality_summary = ""
        self._last_code: str | None = None
        self._last_doc_summary: str | None = None
        self._trace: list[dict] = []
        system = TUNE_SYSTEM_PROMPT.replace("{context}", self._round_context())
        self.messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]

    # ------------------------------------------------------------------
    def run(self) -> RunResult:
        """驱动到收敛：LLM ↔ 工具协议往复，直至文字收尾 / 上限 / LLM 失败。"""
        while True:
            if self._iteration >= self._max_iterations:
                return self._finish(success=False, summary="已达到单轮工具调用上限，收束失败。",
                                    error=f"max_iterations({self._max_iterations}) reached")
            self._iteration += 1

            try:
                trimmed = trim_messages(self.messages, self._max_tokens)
                content = self._llm_chat(trimmed)
            except Exception as exc:  # LLM 不可用/超限：如实失败（A-16），不伪造答复
                logger.warning("微调轮 LLM 调用失败：%s", exc)
                return self._finish(success=False, summary=f"LLM 调用失败：{exc}",
                                    error=f"llm_error: {exc}")

            calls = parse_react_tool_calls(content or "")
            self.messages.append({"role": "assistant", "content": content or ""})

            if not calls:
                allowed, reason = self._check_quality_gate()
                if allowed:
                    return self._finish(success=True, summary=content or "")
                if self._gate_blocks >= 3:  # 逃逸 hatch：连续阻断 3 次后放行失败，防死循环
                    return self._finish(
                        success=False,
                        summary=f"质量门禁连续 {self._gate_blocks} 次阻断，本轮收束失败。最后问题：{reason}",
                        error=f"quality_gate_escape: {reason}")
                self._gate_blocks += 1
                self.messages.append({"role": "user", "content": self._gate_block_message(reason)})
                continue

            for call in calls:
                self._trace.append(self._execute_tool(call))
            # 工具结果已进对话历史，回到下一轮 LLM

    # ------------------------------------------------------------------
    def _execute_tool(self, call: dict) -> dict:
        name = call["function"]["name"]
        args_raw = call["function"]["arguments"] or ""
        try:
            args = json.loads(args_raw, strict=False) if args_raw.strip() else {}
        except Exception:
            args = {}
        code = args.get("code") or args.get("input") or ""

        if name != "execute_edit_code":
            result = {"ok": False, "error": f"未知工具 {name}（一期仅支持 execute_edit_code）"}
        elif not code.strip():
            result = {"ok": False, "error": "execute_edit_code 缺少 code 参数"}
        else:
            try:
                result = self._tool_executor(name, args) or {"ok": False, "error": "插件回传为空"}
            except Exception as exc:
                result = {"ok": False, "error": f"工具执行协议异常：{exc}"}

        result_text = self._format_tool_result(result)
        self._track_quality(result)
        result_text = self._annotate_repeated_errors(result_text, result)

        # ReAct 历史：工具结果以 user 消息跟随（CadAgent 同款）
        self.messages.append({"role": "user",
                              "content": f"[Tool Result for {name}]:\n{result_text}"})
        if result.get("ok"):
            self._last_code = code
        if result.get("doc_summary"):
            self._last_doc_summary = result["doc_summary"]

        return {
            "iteration": self._iteration,
            "name": name,
            "code_chars": len(code),
            "ok": bool(result.get("ok")),
            "quality": (result.get("quality") or {}).get("status"),
            "error": result.get("error"),
            "result_preview": result_text[:200],
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _format_tool_result(result: dict) -> str:
        if not result.get("ok"):
            lines = [f"ERROR: {result.get('error', '未知错误')}"]
            if result.get("stdout"):
                lines.append(result["stdout"])
            return "\n".join(lines)
        quality = result.get("quality") or {}
        status = quality.get("status", "warn")
        issues = [str(i) for i in (quality.get("issues") or [])]
        if status == "fail":
            lines = [f"FAIL: {'; '.join(issues) or '质量门禁未通过'}"]
        elif status == "warn":
            lines = [f"OK (with warnings): {'; '.join(issues)}"]
        else:
            lines = ["OK: quality passed"]
        if result.get("stdout"):
            lines.append(result["stdout"])
        if result.get("doc_summary"):
            lines.append(f"DOCUMENT:\n{result['doc_summary']}")
        return "\n".join(lines)

    def _track_quality(self, result: dict) -> None:
        quality = result.get("quality") or {}
        if result.get("ok"):
            self._last_quality_status = quality.get("status", "fail")
            issues = [str(i) for i in (quality.get("issues") or [])]
            self._last_quality_summary = "; ".join(issues)
        else:
            self._last_quality_status = "fail"
            self._last_quality_summary = result.get("error", "")

    def _annotate_repeated_errors(self, result_text: str, result: dict) -> str:
        if not result.get("ok"):
            sig = result_text[:80]
            if any(sig == prev[:80] for prev in self._recent_errors):
                result_text += ("\n\nWARNING: REPEATED ERROR. You MUST change your approach. "
                                "Do NOT resubmit similar code.")
            self._recent_errors.append(sig)
            if len(self._recent_errors) > 5:
                self._recent_errors.pop(0)
        return result_text

    # ------------------------------------------------------------------
    def _check_quality_gate(self) -> tuple[bool, str]:
        """只拦"编辑失败还想收尾"；纯问答轮（未执行过编辑）放行。"""
        if self._last_quality_status is None or self._last_quality_status in ("pass", "warn"):
            return True, ""
        return False, self._last_quality_summary or "最后一次编辑未通过质量门禁"

    def _gate_block_message(self, reason: str) -> str:
        fix_hint = self._quality_fix_suggestion(reason)
        msg = f"CAD QUALITY GATE: {reason}\n"
        if fix_hint:
            msg += f"Fix: {fix_hint}\n"
        msg += (f"\n{REF_QUALITY_FAILURE}\n"
                "You MUST fix the geometry with another execute_edit_code before finishing. "
                "Do NOT respond with a plain summary.")
        if self._iteration >= self._max_iterations - 2:
            msg += REF_ITERATION_URGENCY.format(max_iter=self._max_iterations,
                                                current=self._iteration)
        return msg

    def _quality_fix_suggestion(self, reason: str) -> str:
        for code, hint in QUALITY_FIX_MAP.items():
            if code in reason:
                return hint
        lowered = reason.lower()
        nl_map = {"no solid": "NO_SOLID", "separate solid": "MULTI_SOLID",
                  "compound": "COMPOUND_SHAPE", "invalid": "INVALID_SHAPE",
                  "negative volume": "NEGATIVE_VOLUME", "dimension": "DIMENSION_SUSPICIOUS"}
        for phrase, code in nl_map.items():
            if phrase in lowered and code in QUALITY_FIX_MAP:
                return QUALITY_FIX_MAP[code]
        return ""

    # ------------------------------------------------------------------
    def _round_context(self) -> str:
        """轮次起始上下文：API 层构建的模型/点选/知识摘要 + 首轮编辑清单。"""
        parts = [p for p in (self._context, REF_FIRST_ITERATION) if p]
        return "\n\n".join(parts)

    def _finish(self, *, success: bool, summary: str, error: str | None = None) -> RunResult:
        return RunResult(success=success, summary=summary, iterations=self._iteration,
                         script=self._last_code, llm_trace=self._trace, error=error,
                         doc_summary=self._last_doc_summary)
