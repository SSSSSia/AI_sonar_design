"""T1.3 契约测试（宪法 §9.2）：合法 IR0 → 合法 IR1（mock LLM / fake KB，不依赖外部服务）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agents.a1_requirement.agent import A1Agent, parse_llm_json
from ir_contract import IrContractError, validate_ir


class FakeResult:
    def __init__(self, content: str, refs: list[dict] | None = None):
        self.content = content
        self.refs = refs or []


def fake_kb_batch(results: list[FakeResult] | None = None, fail: bool = False):
    def _fn(queries: list[str]) -> list[FakeResult]:
        if fail:
            raise RuntimeError("kb down")
        if results is not None:
            return results[: len(queries)] + [results[-1]] * max(0, len(queries) - len(results))
        return [
            FakeResult(
                f"关于{q}的知识内容",
                [{"doc_id": "towed_array_cad_knowledge.txt", "location": "段落3"}],
            )
            for q in queries
        ]

    return _fn


def make_llm(payload: dict | str | Exception):
    class L:
        def chat(self, messages, **kwargs):
            if isinstance(payload, Exception):
                raise payload
            return payload if isinstance(payload, str) else __import__("json").dumps(payload, ensure_ascii=False)

    return L()


GOOD_PLAN = {
    "objectives": [
        {"dimension": "声阵规模", "value": "8 阵元", "explicit": True},
        {"dimension": "工作深度", "value": 300, "explicit": True},
        {"dimension": "频段", "value": None, "explicit": False},
    ],
    "missing_items": ["频段未明确"],
    "sub_queries": ["拖曳线列阵总体组成", "阵列段外径尺寸", "阵元间距规范", "尾部文件段设计", "干端设备规格"],
}


class TestContract:
    def test_ir0_validation(self):
        assert validate_ir("IR0", {"raw_text": "设计一个拖曳线列阵"})
        with pytest.raises(IrContractError):
            validate_ir("IR0", {})  # raw_text required

    def test_llm_plan_produces_valid_ir1(self):
        agent = A1Agent(llm=make_llm(GOOD_PLAN), kb_batch=fake_kb_batch())
        ir1 = agent.run({"raw_text": "设计一个 8 阵元拖曳线列阵声呐，工作深度 300 米"})
        validate_ir("IR1", ir1)  # 不抛即合法
        assert ir1["objectives"][0]["dimension"] == "声阵规模"
        assert len(ir1["sub_queries"]) == 5
        assert all(set(r) == {"doc_id", "location", "snippet"} for r in ir1["retrieved_context"])

    def test_llm_down_fallback_still_valid_ir1(self):
        agent = A1Agent(llm=make_llm(RuntimeError("llm down")), kb_batch=fake_kb_batch())
        ir1 = agent.run({"raw_text": "设计拖曳线列阵"})
        validate_ir("IR1", ir1)
        assert len(ir1["sub_queries"]) == 4  # 兜底模板 4 条
        assert ir1["missing_items"]  # 缺失项如实列出
        assert any("LLM 规划不可用" in m for m in ir1["missing_items"])

    def test_llm_bad_format_fallback(self):
        agent = A1Agent(llm=make_llm("不是 JSON 的回答"), kb_batch=fake_kb_batch())
        ir1 = agent.run({"raw_text": "需求"})
        validate_ir("IR1", ir1)

    def test_kb_all_fail_marks_missing(self):
        agent = A1Agent(llm=make_llm(GOOD_PLAN), kb_batch=fake_kb_batch(fail=True))
        ir1 = agent.run({"raw_text": "需求"})
        validate_ir("IR1", ir1)
        assert any("知识库检索无可用结果" in m for m in ir1["missing_items"])

    def test_kb_down_does_not_crash(self):
        def broken(queries):
            raise RuntimeError("kb down")

        agent = A1Agent(llm=make_llm(GOOD_PLAN), kb_batch=broken)
        ir1 = agent.run({"raw_text": "需求"})
        validate_ir("IR1", ir1)
        assert ir1["retrieved_context"]  # 错误如实进 snippet


class TestParseLlmJson:
    def test_plain(self):
        assert parse_llm_json('{"a":1}') == {"a": 1}

    def test_fenced(self):
        assert parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_with_prose(self):
        assert parse_llm_json('好的，结果如下：{"a": 1} 完毕') == {"a": 1}

    def test_garbage(self):
        assert parse_llm_json("完全没有 JSON") is None
