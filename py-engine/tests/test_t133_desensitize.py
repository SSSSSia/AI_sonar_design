# -*- coding: utf-8 -*-
"""T13.3 单测：涉密语义脱敏——占位与回填（§9.3 完整形态机制层）。

规则级 mask/restore 往返 + wire 级断言（chat() 出站含占位符、响应回填为原值）。
无真实网络（httpx.stream 打桩，沿 T11.6 harness）。
"""

import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm import adapter as adapter_mod
from llm.desensitize import Desensitizer, make_desensitizer


class _FakeResp:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        pass

    def iter_lines(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def _one_provider(monkeypatch):
    from llm.adapter import ProviderConfig

    provider = ProviderConfig(name="vllm-ok", base_url="http://healthy.internal/v1",
                              api_key="sk-h", model="m", timeout_s=5)
    monkeypatch.setattr(adapter_mod._store, "_providers", [provider])
    monkeypatch.setattr(adapter_mod._store, "_cached_at", time.time())
    monkeypatch.setattr(adapter_mod, "LLM_MAX_RETRIES", 1)
    monkeypatch.setattr(adapter_mod, "_FAILOVER_ENABLED", False)
    monkeypatch.setattr("app_config.LLM_CACHE_ENABLED", False)
    return provider


# ----------------------------------------------------------------------
# 规则级：mask / restore
# ----------------------------------------------------------------------
def test_mask_restore_roundtrip_explicit_and_identity():
    d = Desensitizer(extra_items=["深海一号项目"])
    text = "深海一号项目的型号：XW-07，负责人：张三，由 24 个阵元组成"
    masked = d.mask(text)
    assert "深海一号项目" not in masked and "[SENS_1]" in masked
    assert "XW-07" not in masked and "[SENS_2]" in masked
    assert "张三" not in masked and "[SENS_3]" in masked
    assert d.restore(masked) == text  # 往返一致


def test_identity_single_letter_prefix_not_masked():
    """规则级边界：单字母前缀编号（A-12 决策编号/F-35 形态）不误捕；GJB 1234 军标引用不捕。"""
    d = Desensitizer()
    text = "依据决策 A-12 与军标 GJB 1234 的要求"
    masked = d.mask(text)
    assert "A-12" in masked and "GJB 1234" in masked
    # ≥2 字母前缀编号形态会被身份规则捕获（规则级口径）
    masked2 = d.mask("型号 XW-07 方案")
    assert "XW-07" not in masked2
    assert d.restore(masked2) == "型号 XW-07 方案"


def test_same_value_reuses_placeholder():
    d = Desensitizer(extra_items=["XX工程"])
    masked = d.mask("XX工程第一阶段。XX工程第二阶段")
    assert masked.count("[SENS_1]") == 2
    assert d.restore(masked) == "XX工程第一阶段。XX工程第二阶段"


def test_numeric_items_default_off(monkeypatch):
    monkeypatch.setenv("SANITIZE_SEMANTIC_NUMERIC", "0")
    d = Desensitizer(numeric_items=["1024"])
    assert "1024" in d.mask("频率 1024 Hz")
    monkeypatch.setenv("SANITIZE_SEMANTIC_NUMERIC", "1")
    d2 = Desensitizer(numeric_items=["1024"])
    masked = d2.mask("频率 1024 Hz")
    assert "1024" not in masked and d2.restore(masked) == "频率 1024 Hz"


def test_semantic_disabled_direct_pass(monkeypatch):
    monkeypatch.setenv("SANITIZE_SEMANTIC", "0")
    assert make_desensitizer() is None  # 关闭且无注册项 → 直通
    d = make_desensitizer(desensitize_items=["机密甲"])
    assert d is not None  # 显式注册仍生效（调用方确定性意图）
    assert "机密甲" not in d.mask("含机密甲文本")


def test_mask_messages_preserves_non_string():
    d = Desensitizer(extra_items=["XW-07"])
    msgs = [{"role": "user", "content": "型号 XW-07 方案", "temperature": 0.3, "refs": [1, 2]}]
    masked = d.mask_messages(msgs)
    assert masked[0]["content"] == "型号 [SENS_1] 方案"
    assert masked[0]["temperature"] == 0.3 and masked[0]["refs"] == [1, 2]


# ----------------------------------------------------------------------
# wire 级：chat() 出站 mask、响应 restore
# ----------------------------------------------------------------------
def test_chat_mask_outbound_and_restore_response(monkeypatch, _one_provider):
    captured: list[dict] = []

    def fake_stream(method, url, **kwargs):
        captured.append(kwargs["json"])
        # LLM 回显占位符 + 引用编号（响应侧应回填原值）
        return _FakeResp(['data: {"choices":[{"delta":{"content":"型号 [SENS_1] 由 XW-07 单位研制"}}]}', "data: [DONE]"])

    monkeypatch.setattr(adapter_mod.httpx, "stream", fake_stream)
    result = adapter_mod.llm_adapter.chat(
        [{"role": "user", "content": "为型号 XW-07 生成说明，研制单位：XW-07 研究所"}],
        desensitize_items=["XW-07"],
    )
    # wire 级：出站请求体不含原值
    assert "XW-07" not in str(captured[0])
    assert "[SENS_" in str(captured[0])
    # 响应侧：占位符按映射回填为原值
    assert result == "型号 XW-07 由 XW-07 单位研制"


def test_chat_identity_autoscan_default_on(monkeypatch, _one_provider):
    captured: list[dict] = []

    def fake_stream(method, url, **kwargs):
        captured.append(kwargs["json"])
        return _FakeResp(['data: {"choices":[{"delta":{"content":"负责人为 [SENS_1]"}}]}', "data: [DONE]"])

    monkeypatch.setattr(adapter_mod.httpx, "stream", fake_stream)
    result = adapter_mod.llm_adapter.chat(
        [{"role": "user", "content": "方案编制，负责人：李四"}],
    )  # 不传 desensitize_items——默认身份自动扫描生效
    assert "李四" not in str(captured[0])
    assert result == "负责人为 李四"


def test_chat_semantic_off_no_masking(monkeypatch, _one_provider):
    monkeypatch.setenv("SANITIZE_SEMANTIC", "0")
    captured: list[dict] = []

    def fake_stream(method, url, **kwargs):
        captured.append(kwargs["json"])
        return _FakeResp(['data: {"choices":[{"delta":{"content":"ok"}}]}', "data: [DONE]"])

    monkeypatch.setattr(adapter_mod.httpx, "stream", fake_stream)
    adapter_mod.llm_adapter.chat([{"role": "user", "content": "负责人：李四"}])
    assert "李四" in str(captured[0])  # 关闭 → 出站原样
