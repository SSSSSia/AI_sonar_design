# -*- coding: utf-8 -*-
"""T6.3 py-engine 侧单测（PHASE-006）：A1Agent kb_ids 透传 + session_meta 解析 + 留痕结构。

fake kb_batch 断言第二参数；session_meta 走真库只读（design_sessions 无该行/NULL → None）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json

from agents.a1_requirement.agent import A1Agent
from storage import session_meta


class FakeResult:
    def __init__(self, content: str, refs: list[dict] | None = None):
        self.content = content
        self.refs = refs or []


GOOD_PLAN = {
    "objectives": [{"dimension": "声阵规模", "value": "8 阵元", "explicit": True}],
    "missing_items": [],
    "sub_queries": ["拖曳线列阵总体组成", "阵列段外径尺寸", "阵元间距规范", "尾部文件段设计"],
}


def make_llm(payload):
    class L:
        def chat(self, messages, **kwargs):
            return json.dumps(payload, ensure_ascii=False)

    return L()


def captured_kb_batch(calls: list):
    """记录 (queries, kb_ids_arg) 的 fake batch——签名与 GraphRAGService.query_batch 对齐。"""

    def _fn(queries, kb_ids=None):
        calls.append((list(queries), kb_ids))
        return [
            FakeResult(f"关于{q}的知识", [{"doc_id": "doc-1", "location": "第1页", "kb_id": "kb-9"}])
            for q in queries
        ]

    return _fn


IR0 = {"raw_text": "设计一个拖曳线列阵声呐，8 阵元"}


class TestA1AgentKbIds:
    def test_manual_kb_ids_passed_to_batch(self):
        calls: list = []
        agent = A1Agent(llm=make_llm(GOOD_PLAN), kb_batch=captured_kb_batch(calls))
        ir1 = agent.run(IR0, kb_ids=["kb-a"])
        assert ir1["retrieved_context"]
        assert all(c[1] == ["kb-a"] for c in calls)  # 每次批查都带选库范围

    def test_none_kb_ids_keeps_unscoped_semantics(self):
        calls: list = []
        agent = A1Agent(llm=make_llm(GOOD_PLAN), kb_batch=captured_kb_batch(calls))
        agent.run(IR0, kb_ids=None)
        assert all(c[1] is None for c in calls)  # 未限定：不过滤（现状语义）

    def test_refs_kb_id_lands_in_retrieved_context(self):
        calls: list = []
        agent = A1Agent(llm=make_llm(GOOD_PLAN), kb_batch=captured_kb_batch(calls))
        ir1 = agent.run(IR0, kb_ids=["kb-9"])
        assert all(r.get("kb_id") == "kb-9" for r in ir1["retrieved_context"])


class TestSessionMeta:
    def test_missing_session_returns_none(self):
        assert session_meta.get_kb_ids("no-such-session-000") is None

    def test_null_kb_ids_returns_none(self):
        """存量会话（kb_ids NULL）= None → Agent 模式，向后兼容。"""
        import psycopg

        from app_config import DATABASE_URL

        with psycopg.connect(DATABASE_URL.replace("+psycopg", "")) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO design_sessions (id, project_id, pipeline_status, title, kb_ids) "
                "SELECT '00000000-0000-0000-0000-100000000001', id, 'pending', 'ut', NULL "
                "FROM projects LIMIT 1"
            )
        try:
            assert session_meta.get_kb_ids("00000000-0000-0000-0000-100000000001") is None
        finally:
            with psycopg.connect(DATABASE_URL.replace("+psycopg", "")) as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM design_sessions WHERE id = '00000000-0000-0000-0000-100000000001'")

    def test_json_array_roundtrip(self):
        import psycopg

        from app_config import DATABASE_URL

        kb_ids_json = json.dumps(["0eb22589-055c-4453-921a-42206e2e6948"])
        with psycopg.connect(DATABASE_URL.replace("+psycopg", "")) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO design_sessions (id, project_id, pipeline_status, title, kb_ids) "
                "SELECT '00000000-0000-0000-0000-100000000002', id, 'pending', 'ut', %s "
                "FROM projects LIMIT 1",
                (kb_ids_json,),
            )
        try:
            assert session_meta.get_kb_ids("00000000-0000-0000-0000-100000000002") == [
                "0eb22589-055c-4453-921a-42206e2e6948"
            ]
        finally:
            with psycopg.connect(DATABASE_URL.replace("+psycopg", "")) as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM design_sessions WHERE id = '00000000-0000-0000-0000-100000000002'")

    def test_non_array_json_returns_none(self):
        """jsonb 列写入即校验，坏 JSON 不可能落库；此处验证"合法 JSON 非数组"容错 → None。"""
        import psycopg

        from app_config import DATABASE_URL

        with psycopg.connect(DATABASE_URL.replace("+psycopg", "")) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO design_sessions (id, project_id, pipeline_status, title, kb_ids) "
                "SELECT '00000000-0000-0000-0000-100000000003', id, 'pending', 'ut', '\"kb-as-string\"' "
                "FROM projects LIMIT 1"
            )
        try:
            assert session_meta.get_kb_ids("00000000-0000-0000-0000-100000000003") is None
        finally:
            with psycopg.connect(DATABASE_URL.replace("+psycopg", "")) as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM design_sessions WHERE id = '00000000-0000-0000-0000-100000000003'")
