# -*- coding: utf-8 -*-
"""T18.5 会话图片输入单测（PHASE-018；契约=ADR-017 + ir0.schema T18.5 加法演进）。

- 附件上传端点（api/attachments.py）：image/* 校验/大小护栏/内容寻址引用形态；
- A1 附图预读（agents/a1_requirement）：attachments→prompt 注入预读段；无 attachments→
  IR0 旧形态逐字节（回滚保证硬断言）；下载/VLM 失败如实降级不阻塞（A-16）。
契约 schema 本身的合法/反例在 test_ir_contracts.py（同 commit 纪律）。
沿 test_t35_ocr/test_t175 函数级 mock 先例，全离线（MinIO/VLM 全 fake）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app_config
from agents.a1_requirement.agent import A1Agent
from agents.a1_requirement.prompts import READING_PROMPT
from llm import vision as llm_vision

ATTACH_REF = {"bucket": "session-attachments", "object_key": "0123456789abcdef/ref.png",
              "sha256": "ab" * 32}
PNG_A = b"\x89PNG-a"
PNG_B = b"\x89PNG-b"
CAPTION = "整体为圆柱形拖曳体，尾部锥形收敛，顶部有吊环。"


# ------------------------------------------------------------------------------
# 上传端点（TestClient + fake object_store）
class TestUploadEndpoint:
    def _client(self):
        from fastapi.testclient import TestClient

        from api.main import app

        return TestClient(app)

    def _patch_store(self, monkeypatch, calls=None):
        def fake(data, bucket, key, content_type=""):
            if calls is not None:
                calls.append((bucket, key, content_type))
            return {"bucket": bucket, "object_key": key, "sha256": "d" * 64}

        monkeypatch.setattr("storage.object_store.upload_bytes", fake)

    def test_success_reference_form(self, monkeypatch):
        calls: list = []
        self._patch_store(monkeypatch, calls)
        resp = self._client().post(
            "/api/v1/attachments/upload",
            files={"file": ("ref.png", PNG_A, "image/png")},
            headers={"X-User-Id": "u1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["bucket"] == "session-attachments"
        assert body["object_key"].endswith("ref.png")
        assert len(body["object_key"].split("/")[0]) == 16  # sha256 前 16 位内容寻址前缀
        assert body["sha256"]
        assert calls and calls[0][0] == "session-attachments" and calls[0][2] == "image/png"

    def test_non_image_415(self, monkeypatch):
        self._patch_store(monkeypatch)
        resp = self._client().post(
            "/api/v1/attachments/upload",
            files={"file": ("x.py", b"print(1)", "text/x-python")},
            headers={"X-User-Id": "u1"},
        )
        assert resp.status_code == 415

    def test_empty_422(self, monkeypatch):
        self._patch_store(monkeypatch)
        resp = self._client().post(
            "/api/v1/attachments/upload",
            files={"file": ("x.png", b"", "image/png")},
            headers={"X-User-Id": "u1"},
        )
        assert resp.status_code == 422

    def test_oversize_413(self, monkeypatch):
        self._patch_store(monkeypatch)
        big = b"\x89" + b"0" * (5 * 1024 * 1024)
        resp = self._client().post(
            "/api/v1/attachments/upload",
            files={"file": ("big.png", big, "image/png")},
            headers={"X-User-Id": "u1"},
        )
        assert resp.status_code == 413

    def test_missing_user_401(self, monkeypatch):
        self._patch_store(monkeypatch)
        resp = self._client().post(
            "/api/v1/attachments/upload",
            files={"file": ("x.png", PNG_A, "image/png")},
        )
        assert resp.status_code == 401


# ------------------------------------------------------------------------------
# A1 附图预读
def _capture_llm():
    """fake LLM：A1Agent 要求 llm.chat(...) 接口（t151 _CaptureLLM 同款对象形态）。"""
    captured = SimpleNamespace(messages=[])

    class _LLM:
        def chat(self, messages, **kwargs):
            captured.messages.append(messages)
            return ('{"objectives": [], "missing_items": [], '
                    '"sub_queries": ["q1 尺寸", "q2 布局", "q3 材料", "q4 装配"]}')

    return _LLM(), captured


class TestA1Attachments:
    def test_attachments_inject_reading(self, monkeypatch):
        """attachments→下载→VLM 预读→user content 注入「用户附图」段。"""
        downloads: list = []
        monkeypatch.setattr("storage.object_store.download_bytes",
                            lambda bucket, key: downloads.append((bucket, key)) or PNG_A)
        vlm_calls: list = []

        def fake_describe(imgs, prompt, model=None):
            vlm_calls.append((list(imgs), prompt))
            return CAPTION

        monkeypatch.setattr(llm_vision, "describe_images", fake_describe)
        chat, captured = _capture_llm()
        agent = A1Agent(llm=chat)
        agent.run({"raw_text": "参考该图设计拖曳体", "attachments": [dict(ATTACH_REF)]})
        assert downloads == [("session-attachments", "0123456789abcdef/ref.png")]
        assert vlm_calls and vlm_calls[0][1] == READING_PROMPT and vlm_calls[0][0] == [PNG_A]
        user = captured.messages[0][1]["content"]
        assert "用户附图" in user and CAPTION in user
        assert "用户需求：参考该图设计拖曳体" in user

    def test_multi_images_single_vlm_call(self, monkeypatch):
        """多图一次 describe_images 调用（png 列表整体传入）。"""
        monkeypatch.setattr("storage.object_store.download_bytes",
                            lambda b, k: PNG_A if k.endswith("a.png") else PNG_B)
        seen: list = []

        def fake_describe(imgs, prompt, model=None):
            seen.append(list(imgs))
            return CAPTION

        monkeypatch.setattr(llm_vision, "describe_images", fake_describe)
        chat, _ = _capture_llm()
        agent = A1Agent(llm=chat)
        agent.run({"raw_text": "x", "attachments": [
            dict(ATTACH_REF, object_key="0123456789abcdef/a.png"),
            dict(ATTACH_REF, object_key="0123456789abcdef/b.png"),
        ]})
        assert seen == [[PNG_A, PNG_B]]

    def test_vlm_none_no_injection(self, monkeypatch):
        """VLM 失败→None→不注入（user content 无附图段），管线不阻塞。"""
        monkeypatch.setattr("storage.object_store.download_bytes", lambda b, k: PNG_A)
        monkeypatch.setattr(llm_vision, "describe_images", lambda imgs, prompt, model=None: None)
        chat, captured = _capture_llm()
        agent = A1Agent(llm=chat)
        ir1 = agent.run({"raw_text": "需求文本", "attachments": [dict(ATTACH_REF)]})
        assert "用户附图" not in captured.messages[0][1]["content"]
        assert ir1["sub_queries"]

    def test_download_fail_skips_image(self, monkeypatch):
        """单张下载失败跳过该张，其余仍注入（材料缺位如实）。"""
        def fake_download(bucket, key):
            if "bad" in key:
                raise RuntimeError("minio miss")
            return PNG_B

        monkeypatch.setattr("storage.object_store.download_bytes", fake_download)
        seen: list = []
        monkeypatch.setattr(llm_vision, "describe_images",
                            lambda imgs, prompt, model=None: seen.append(list(imgs)) or CAPTION)
        chat, captured = _capture_llm()
        agent = A1Agent(llm=chat)
        agent.run({"raw_text": "x", "attachments": [
            dict(ATTACH_REF, object_key="0123456789abcdef/bad.png"),
            dict(ATTACH_REF, object_key="0123456789abcdef/good.png"),
        ]})
        assert seen == [[PNG_B]]
        assert "用户附图" in captured.messages[0][1]["content"]

    def test_no_attachments_byte_identical(self, monkeypatch):
        """关键回滚断言：无 attachments→不触 MinIO/VLM，user content 与旧形态逐字节一致。"""
        touched: list = []
        monkeypatch.setattr("storage.object_store.download_bytes",
                            lambda b, k: touched.append(1) or PNG_A)
        monkeypatch.setattr(llm_vision, "describe_images",
                            lambda imgs, prompt, model=None: touched.append(2) or CAPTION)
        chat, captured = _capture_llm()
        agent = A1Agent(llm=chat)
        agent.run({"raw_text": "需求文本"})
        assert touched == []
        from agents.a1_requirement.prompts import PLAN_PROMPT, RETRIEVAL_DIMENSIONS, SYSTEM_PROMPT
        expected = (f"用户需求：需求文本\n\n"
                    + PLAN_PROMPT.format(dimensions="\n".join(f"- {d}" for d in RETRIEVAL_DIMENSIONS)))
        assert captured.messages[0][1]["content"] == expected
        assert captured.messages[0][0]["content"] == SYSTEM_PROMPT

    def test_empty_list_zero_behavior(self, monkeypatch):
        touched: list = []
        monkeypatch.setattr("storage.object_store.download_bytes",
                            lambda b, k: touched.append(1) or PNG_A)
        monkeypatch.setattr(llm_vision, "describe_images",
                            lambda imgs, prompt, model=None: touched.append(2) or CAPTION)
        chat, _ = _capture_llm()
        agent = A1Agent(llm=chat)
        agent.run({"raw_text": "x", "attachments": []})
        assert touched == []

    def test_all_downloads_fail_no_vlm_call(self, monkeypatch):
        """全部下载失败→无 VLM 调用→不注入（A-16 不造预读）。"""
        def boom(bucket, key):
            raise RuntimeError("minio down")

        monkeypatch.setattr("storage.object_store.download_bytes", boom)
        vlm_touched: list = []
        monkeypatch.setattr(llm_vision, "describe_images",
                            lambda imgs, prompt, model=None: vlm_touched.append(1) or CAPTION)
        chat, captured = _capture_llm()
        agent = A1Agent(llm=chat)
        agent.run({"raw_text": "x", "attachments": [dict(ATTACH_REF)]})
        assert vlm_touched == []
        assert "用户附图" not in captured.messages[0][1]["content"]


# ------------------------------------------------------------------------------
# app_config 桶常量与 schema 常量一致性（防手误）
def test_bucket_constant_matches_schema_whitelist() -> None:
    import json
    from pathlib import Path

    schema = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "ir" / "ir0.schema.json")
                        .read_text(encoding="utf-8"))
    const = schema["properties"]["attachments"]["items"]["properties"]["bucket"]["const"]
    assert const == app_config.BUCKET_SESSION_ATTACHMENTS
