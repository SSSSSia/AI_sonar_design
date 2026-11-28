# -*- coding: utf-8 -*-
"""T16.4 尾巴——codegen 缓存语义修复测试（仅「首轮生成且沙箱一次通过」的脚本进缓存）。

背景（T16.4 deposit 实战缺陷）：旧语义「脚本非空即写」（写入点在 validate/sandbox 之前），
失败脚本进缓存（TTL 30d）→gate 驳回→A3 rework 同 IR2 指纹同键命中同一坏脚本，
循环无法换新采样（三轮产物逐字节相同实证）。修复后语义：
- 缓存 miss + 首轮生成沙箱一次通过 → 写缓存恰一次；
- 首轮失败经修复循环救回的产物（_fix 产物）不缓存；
- 全轮失败抛 RuntimeError 不缓存；
- 命中分支跳过 _generate 且不回写。

run() 全链 mock 形态照 test_t182 _patch_agent_chain（active_provider 必 mock——
run() 唯一真实 I/O，底座不在线时连接重试挂死）。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agents.a3_cad.agent import A3Agent

STEP_REF = {"bucket": "step-models", "object_key": "s/a3/t/m.step", "sha256": "a" * 64}
SCRIPT_REF = {"bucket": "cad-scripts", "object_key": "s/a3/t/m.py", "sha256": "b" * 64}
INSPECTED = {"ok": True, "overall": {"bbox_mm": [10, 10, 10], "volume_mm3": 1.0,
                                     "part_count": 1}, "parts": [], "meta": {}}


def _sb(ok: bool, error: str = "") -> dict[str, Any]:
    return {"ok": ok, "error": error, "step_files": ["/tmp/fake.step"] if ok else [],
            "workdir": Path("/tmp"), "auto_exported": ok}


def _patch_chain(monkeypatch, sandbox_results: list[dict], cached: dict | None, calls: dict):
    """缓存读写记录器 + sandbox 结果序列 + 其余全链 mock（照 t182）。"""

    def _no_provider():
        raise RuntimeError("offline test：无供应商")

    monkeypatch.setattr("llm.adapter.active_provider", _no_provider)
    monkeypatch.setattr("cad_engine.validators.validate_chain",
                        lambda script, domain=None: (script, True, [], []))

    def fake_sandbox(code, time_limit_s=300):
        calls["sandbox_seq"].append(ok := sandbox_results.pop(0)["ok"] if sandbox_results else True)
        return _sb(ok, "" if ok else "gp_VectorWithNullMagnitude")

    monkeypatch.setattr("cad_engine.sandbox.run_in_subprocess", fake_sandbox)
    monkeypatch.setattr("agents.a3_cad.agent._upload_artifacts",
                        lambda sid, step, script: (dict(STEP_REF), dict(SCRIPT_REF)))
    monkeypatch.setattr("storage.cache.codegen_cache_key", lambda *a, **k: "k")

    def fake_get(layer, key):
        return cached

    def fake_set(key, value, ttl_s):
        calls["set"].append(json.loads(json.dumps(value, default=str)))
        return True

    monkeypatch.setattr("storage.cache.get_json", fake_get)
    monkeypatch.setattr("storage.cache.set_json", fake_set)


def _run_agent(monkeypatch, calls) -> dict[str, Any]:
    agent = A3Agent(llm=SimpleNamespace(
        chat=lambda messages, **k: (calls.__setitem__("generate", calls["generate"] + 1)
                                    or "GENERATED_SCRIPT")))
    agent._ref_service = SimpleNamespace(
        select_reference=lambda doc: (None, None), last_reason="no_match")
    agent._fix = lambda code, err, domain=None: (calls.__setitem__("fix", calls["fix"] + 1)
                                                 or "FIXED_SCRIPT")  # type: ignore[method-assign]
    agent._inspect_step = lambda path: dict(INSPECTED)  # type: ignore[method-assign]
    ir2 = {"parameters": [], "design_document": "侧扫声呐拖鱼设计"}
    return agent.run("sess-t164", ir2)


class TestCodegenCacheSemantics:
    def test_miss_first_pass_caches_once(self, monkeypatch):
        """缓存 miss + 首轮生成沙箱一次通过 → set_json 恰一次（envelope=script+gen_meta）。"""
        calls = {"set": [], "generate": 0, "fix": 0, "sandbox_seq": []}
        _patch_chain(monkeypatch, [_sb(True)], cached=None, calls=calls)
        agent = A3Agent(llm=SimpleNamespace(chat=lambda m, **k: "GENERATED_SCRIPT"))
        agent._ref_service = SimpleNamespace(select_reference=lambda d: (None, None),
                                             last_reason="no_match")
        agent._inspect_step = lambda path: dict(INSPECTED)  # type: ignore[method-assign]
        agent.run("s", {"parameters": [], "design_document": "d"})
        assert len(calls["set"]) == 1
        assert calls["set"][0]["script"] == "GENERATED_SCRIPT"
        assert calls["set"][0]["gen_meta"]["generation_source"] == "fresh_llm"

    def test_fix_product_not_cached(self, monkeypatch):
        """首轮 fail 经修复循环救回 → set_json 零调用（_fix 产物不缓存）；流程继续成功。"""
        calls = {"set": [], "generate": 0, "fix": 0, "sandbox_seq": []}
        _patch_chain(monkeypatch, [_sb(False), _sb(True)], cached=None, calls=calls)
        agent = A3Agent(llm=SimpleNamespace(chat=lambda m, **k: "GENERATED_SCRIPT"))
        agent._ref_service = SimpleNamespace(select_reference=lambda d: (None, None),
                                             last_reason="no_match")
        agent._fix = lambda code, err, domain=None: "FIXED_SCRIPT"  # type: ignore[method-assign]
        agent._inspect_step = lambda path: dict(INSPECTED)  # type: ignore[method-assign]
        ir3 = agent.run("s", {"parameters": [], "design_document": "d"})
        assert calls["set"] == []  # 修复产物不缓存（T16.4 教训核心断言）
        assert ir3["cadquery_script"] == "FIXED_SCRIPT"

    def test_all_fail_raises_without_cache(self, monkeypatch):
        """3 轮修复全失败 → RuntimeError 且 set_json 零调用（失败脚本不进缓存）。"""
        calls = {"set": [], "generate": 0, "fix": 0, "sandbox_seq": []}
        _patch_chain(monkeypatch, [_sb(False)] * 5, cached=None, calls=calls)
        agent = A3Agent(llm=SimpleNamespace(chat=lambda m, **k: "GENERATED_SCRIPT"))
        agent._ref_service = SimpleNamespace(select_reference=lambda d: (None, None),
                                             last_reason="no_match")
        agent._fix = lambda code, err, domain=None: "FIXED_SCRIPT"  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="3 轮修复后仍失败"):
            agent.run("s", {"parameters": [], "design_document": "d"})
        assert calls["set"] == []

    def test_hit_skips_generate_and_no_write(self, monkeypatch):
        """缓存命中 → 跳过 _generate、script=缓存脚本、set_json 零调用（命中端语义不变）。"""
        calls = {"set": [], "generate": 0, "fix": 0, "sandbox_seq": []}
        cached = {"script": "CACHED_SCRIPT", "gen_meta": {"generation_source": "fresh_llm",
                                                          "prompt_version": "3"}}
        _patch_chain(monkeypatch, [_sb(True)], cached=cached, calls=calls)

        gen_calls = {"n": 0}

        def _gen(doc, params, ref_code, domain=None):
            gen_calls["n"] += 1
            return "GENERATED_SCRIPT"

        agent = A3Agent(llm=SimpleNamespace(chat=lambda m, **k: "x"))
        agent._ref_service = SimpleNamespace(select_reference=lambda d: (None, None),
                                             last_reason="no_match")
        agent._generate = _gen  # type: ignore[method-assign]
        agent._inspect_step = lambda path: dict(INSPECTED)  # type: ignore[method-assign]
        ir3 = agent.run("s", {"parameters": [], "design_document": "d"})
        assert gen_calls["n"] == 0  # 命中跳过生成
        assert ir3["cadquery_script"] == "CACHED_SCRIPT"
        assert calls["set"] == []  # 命中分支不回写
        assert ir3["reference_used"]["used"] is False  # gen_meta 回读（本缓存无范例 ref→used False）
