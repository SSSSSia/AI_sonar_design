# -*- coding: utf-8 -*-
"""T8.2 cad_agent 服务端大脑单测（PHASE-008）：ReAct 解析 / token 裁剪 / TuneRoundRunner 全状态机。

不打库不打 LLM：llm_chat 与 tool_executor 均为 fake 注入（脚本化响应序列）。
"""

from __future__ import annotations

import pytest

from cad_agent import prompts
from cad_agent.loop import TuneRoundRunner
from cad_agent.react_parser import parse_react_tool_calls
from cad_agent.token_budget import estimate_tokens, trim_messages


# ----------------------------------------------------------------------
# react_parser（CadAgent 迁移件冒烟）
# ----------------------------------------------------------------------
def test_parse_paired_tag_with_fence():
    text = '计划先加法兰。\n<tool name="execute_edit_code">\n```json\n{"code": "result = model", "description": "x"}\n```\n</tool>'
    calls = parse_react_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "execute_edit_code"
    assert '"result = model"' in calls[0]["function"]["arguments"]


def test_parse_unclosed_tag_and_plain_text():
    # 模型生成中断：未闭合标签也要能取到工具调用
    calls = parse_react_tool_calls('<tool name="execute_edit_code">\n{"code": "result = model.cut(x)"')
    assert calls and calls[0]["function"]["name"] == "execute_edit_code"
    assert parse_react_tool_calls("直接文字答复，不需要改几何。") == []


# ----------------------------------------------------------------------
# token_budget
# ----------------------------------------------------------------------
def test_trim_keeps_short_history():
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    assert trim_messages(msgs, max_tokens=1000) == msgs


def test_trim_deletes_assistant_tool_pairs_atomically():
    big = "x" * 4000
    msgs = [{"role": "system", "content": "sys"}]
    for _ in range(6):
        msgs.append({"role": "assistant", "content": f'<tool name="execute_edit_code">{big}'})
        msgs.append({"role": "user", "content": f"[Tool Result for execute_edit_code]:\n{big}"})
    msgs.append({"role": "user", "content": "final"})
    out = trim_messages(msgs, max_tokens=600)
    assert out[0]["role"] == "system"
    assert out[-1]["content"] == "final"
    # 不留孤儿：assistant(<tool) 后必须紧跟 user 结果或为收尾
    for i, m in enumerate(out[:-1]):
        if m["role"] == "assistant" and "<tool" in (m.get("content") or ""):
            assert out[i + 1]["role"] == "user"


def test_estimate_tokens_mixed():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("声呐阵列设计") >= 3


# ----------------------------------------------------------------------
# TuneRoundRunner（fake LLM + fake 执行器）
# ----------------------------------------------------------------------
def _make_llm(responses: list[str], seen: list[list[dict]]):
    def llm(messages):
        seen.append([dict(m) for m in messages])
        return responses.pop(0)

    return llm


def _make_executor(results: list[dict], received: list[tuple]):
    def exe(name, args):
        received.append((name, args))
        return results.pop(0)

    return exe


def _runner(llm, exe, max_iterations=8):
    return TuneRoundRunner(
        context="BASELINE MODEL VERSION: v3\nGEOMETRY SUMMARY: bbox 100x20x20",
        user_prompt="在基座上加一圈法兰",
        llm_chat=llm,
        tool_executor=exe,
        max_iterations=max_iterations,
        max_context_tokens=24000,
    )


TOOL_CALL = ('<tool name="execute_edit_code">\n'
             '{"code": "result = safe_fuse(model, ring)", "description": "add flange"}\n'
             '</tool>')


def test_runner_happy_path():
    seen, received = [], []
    llm = _make_llm([TOOL_CALL, "已在基座加一圈法兰环，外径 130mm。"], seen)
    exe = _make_executor([{"ok": True, "quality": {"status": "pass"},
                           "doc_summary": "bbox 130x130x40"}], received)
    result = _runner(llm, exe).run()
    assert result.success and result.iterations == 2
    assert result.script == "result = safe_fuse(model, ring)"
    assert result.doc_summary == "bbox 130x130x40"
    assert result.error is None
    assert received == [("execute_edit_code", {"code": "result = safe_fuse(model, ring)",
                                               "description": "add flange"})]
    assert result.llm_trace[0]["ok"] is True and result.llm_trace[0]["quality"] == "pass"
    # 首轮 system prompt 带编辑清单；工具结果以 user 消息进历史
    assert "FIRST EDIT CHECKLIST" in seen[0][0]["content"]
    assert any("[Tool Result for execute_edit_code]" in m["content"] for m in seen[1])


def test_runner_question_round_no_tool():
    seen, received = [], []
    llm = _make_llm(["当前模型总长 100mm（见几何摘要）。"], seen)
    result = _runner(llm, _make_executor([], received)).run()
    assert result.success and result.script is None
    assert received == []


def test_runner_quality_fail_blocks_then_fixes():
    seen, received = [], []
    llm = _make_llm([TOOL_CALL, "好了。", TOOL_CALL, "法兰已加，质量通过。"], seen)
    exe = _make_executor([
        {"ok": True, "quality": {"status": "fail", "issues": ["MULTI_SOLID: two separate solids"]}},
        {"ok": True, "quality": {"status": "pass"}, "doc_summary": "bbox 130x130x40"},
    ], received)
    result = _runner(llm, exe).run()
    assert result.success
    # 一次门禁阻断消息进历史（含修复指引），随后修复成功
    gate_msgs = [m for m in _flatten(seen) if "CAD QUALITY GATE" in m.get("content", "")]
    assert gate_msgs and "MULTI_SOLID" in gate_msgs[0]["content"]


def _flatten(seen):
    flat = {}
    for snap in seen:
        for i, m in enumerate(snap):
            flat[(i, m["content"][:40])] = m
    return flat.values()


def test_runner_escape_hatch_after_three_blocks():
    seen, received = [], []
    llm = _make_llm([TOOL_CALL] + ["我觉得可以了。"] * 5, seen)
    exe = _make_executor([{"ok": True, "quality": {"status": "fail", "issues": ["INVALID_SHAPE: bad boolean"]}}] * 1
                         + [], received)
    result = _runner(llm, exe).run()
    assert not result.success
    assert result.error and result.error.startswith("quality_gate_escape")
    assert "INVALID_SHAPE" in result.summary or "质量门禁" in result.summary


def test_runner_repeated_error_annotation():
    seen, received = [], []
    llm = _make_llm([TOOL_CALL, TOOL_CALL, "放弃描述。"], seen)
    err = {"ok": False, "error": "ValueError: shapes do not overlap"}
    exe = _make_executor([err, dict(err)], received)
    result = _runner(llm, exe).run()
    assert not result.success  # 质量失败收尾被拦 → 逃逸失败
    second = [m for m in _flatten(seen)
              if "[Tool Result" in m.get("content", "") and "REPEATED ERROR" in m.get("content", "")]
    assert second, "重复错误应注入换思路警告"


def test_runner_llm_failure_is_honest():
    seen, received = [], []

    def boom(messages):
        raise RuntimeError("provider down")

    result = _runner(boom, _make_executor([], received)).run()
    assert not result.success
    assert result.error and result.error.startswith("llm_error")
    assert "provider down" in result.summary


def test_runner_max_iterations_bounded():
    seen, received = [], []
    llm = _make_llm([TOOL_CALL] * 20, seen)
    exe = _make_executor([{"ok": True, "quality": {"status": "pass"}}] * 20, received)
    result = _runner(llm, exe, max_iterations=4).run()
    assert not result.success
    assert "max_iterations(4)" in result.error
    assert len(received) == 4  # A-13 有界终止


def test_runner_unknown_tool_and_empty_code():
    seen, received = [], []
    llm = _make_llm([
        '<tool name="undo_last">\n{}\n</tool>',
        '<tool name="execute_edit_code">\n{"description": "没写 code"}\n</tool>',
        TOOL_CALL,
        "完成。",
    ], seen)
    exe = _make_executor([{"ok": True, "quality": {"status": "pass"}, "doc_summary": "bbox ok"}], received)
    result = _runner(llm, exe).run()
    assert result.success
    # 未知工具与缺 code 都在协议层被拦，不落到执行器
    assert len(received) == 1
    assert result.script == "result = safe_fuse(model, ring)"


# ----------------------------------------------------------------------
# build_round_context
# ----------------------------------------------------------------------
def test_build_round_context_assembles_parts():
    ctx = prompts.build_round_context(
        baseline_version=4,
        geometry_summary="bbox 100x20x20, volume 40000mm3",
        selection={"kind": "Face", "area_mm2": 120.5},
        kb_digest="- 拖曳阵基段孔距 0.5m（来源：xxx.doc §2）",
        doc_summary="bbox 130x130x40",
    )
    assert "BASELINE MODEL VERSION: v4" in ctx
    assert "GEOMETRY SUMMARY" in ctx
    assert "USER SELECTION" in ctx and "Face" in ctx
    assert "DOMAIN KNOWLEDGE DIGEST" in ctx
    assert "CURRENT DOCUMENT STATE" in ctx
    minimal = prompts.build_round_context(baseline_version=1, geometry_summary=None,
                                          selection=None, kb_digest=None)
    assert minimal == "BASELINE MODEL VERSION: v1"
