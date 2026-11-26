# -*- coding: utf-8 -*-
"""T18.2 CAD 视觉校验旁路单测（PHASE-018；契约=ADR-017）。

- vision_check.parse_verdict：判词 JSON 解析（围栏/杂文容错，失败 None 不造结构 A-16）；
- vision_check.run_vision_check：checked/skipped 双态与逐级失败降级（渲染/VLM/上传）；
- ir3.schema vision_check 加法演进（additionalProperties:false 同步纪律兜底）；
- A3Agent 挂点：CAD_VISION_CHECK 默认关=零调用+IR3 无字段（旧形态逐字节）；开态落位；
- A4Agent._check_vision：warning 呈现不进 CSR/coverage 分母（A-07/A-16）。
沿 test_t175_ref_visual 函数级 mock 与 test_t73_a3_geometry 夹具先例；全离线无真连。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app_config
from agents.a3_cad.agent import A3Agent
from agents.a4_validation.agent import A4Agent
from cad_engine import vision_check
from ir_contract import IrContractError, validate_ir
from tests.test_t73_a3_geometry import INSPECTED, SCRIPT_REF, STEP_REF

FAKE_PNG = b"\x89PNG-fake"

BASE_IR3: dict[str, Any] = {
    "cadquery_script": "x = 1\n",
    "step_file": dict(STEP_REF),
    "features": [],
    "geometry_summary": {"source": "extraction_failed", "detail": "跳过"},
    "reference_used": {"used": False},
}
VC_CHECKED: dict[str, Any] = {
    "status": "checked",
    "verdict_text": '{"findings": [{"item": "阵列悬空", "severity": "warning", "detail": "第 3 段悬空"}], "overall_note": "存疑"}',
    "findings": [{"item": "阵列悬空", "severity": "warning", "detail": "第 3 段悬空"}],
    "model": "Qwen/Qwen3-VL-32B-Instruct",
    "checked_at": "2026-11-26T12:00:00+0800",
    "views_png": {"bucket": "step-models", "object_key": "s1/a3/tok/views.png"},
}
VC_SKIPPED: dict[str, Any] = {"status": "skipped", "detail": "render_failed: timeout"}


# ------------------------------------------------------------------------------
# parse_verdict 纯函数
class TestParseVerdict:
    def test_plain_json(self):
        text = '{"findings": [{"item": "穿透", "severity": "warning", "detail": "轴横穿"}], "overall_note": "n"}'
        out = vision_check.parse_verdict(text)
        assert out == [{"item": "穿透", "severity": "warning", "detail": "轴横穿"}]

    def test_markdown_fence(self):
        text = '```json\n{"findings": [], "overall_note": "未见明显异常"}\n```'
        assert vision_check.parse_verdict(text) == []

    def test_prose_around_json(self):
        text = '判词如下：{"findings": [{"item": "失真", "severity": "info", "detail": "d"}]} 以上。'
        out = vision_check.parse_verdict(text)
        assert out and out[0]["item"] == "失真" and out[0]["severity"] == "info"

    def test_severity_normalized(self):
        """severity 非 warning 一律归 info（schema 枚举二值，不造第三态）。"""
        text = '{"findings": [{"item": "x", "severity": "error", "detail": "d"}]}'
        assert vision_check.parse_verdict(text)[0]["severity"] == "info"

    def test_empty_findings_is_valid(self):
        """空 findings=合法解析结果（未见明显异常），不是 None。"""
        assert vision_check.parse_verdict('{"findings": [], "overall_note": "ok"}') == []

    def test_findings_not_list_none(self):
        assert vision_check.parse_verdict('{"findings": "无"}') is None

    def test_garbage_none(self):
        assert vision_check.parse_verdict("我看这图不错，没什么问题。") is None
        assert vision_check.parse_verdict("") is None

    def test_malformed_json_none(self):
        assert vision_check.parse_verdict('{"findings": [{"item": ') is None


# ------------------------------------------------------------------------------
# run_vision_check 链路（逐级 fake，全离线）
def _patch_render(monkeypatch, result):
    monkeypatch.setattr("cad_engine.render.render_step_views", lambda step: result)


def _patch_upload(monkeypatch, calls=None, exc=None):
    def fake(data, bucket, key, ct=""):
        if calls is not None:
            calls.append((bucket, key))
        if exc is not None:
            raise exc
        return {"bucket": bucket, "object_key": key, "sha256": "d" * 64}

    monkeypatch.setattr("storage.object_store.upload_bytes", fake)


def _patch_vlm(monkeypatch, results):
    """results=str 恒返该文；list=按次返回（含 None 模拟瞬态失败）。"""
    seq = [results] if isinstance(results, str) else list(results)
    calls = {"n": 0}

    def fake(imgs, prompt, model=None):
        calls["n"] += 1
        return seq.pop(0) if seq else None

    monkeypatch.setattr("llm.vision.describe_images", fake)
    return calls


GOOD_VERDICT = ('好的，判词如下：{"findings": [{"item": "悬空", "severity": "warning", "detail": "翼板悬空"}], '
                '"overall_note": "存在疑点"}')


class TestRunVisionCheck:
    def test_checked_full(self, monkeypatch):
        _patch_render(monkeypatch, {"ok": True, "png": FAKE_PNG, "error": ""})
        _patch_upload(monkeypatch)
        _patch_vlm(monkeypatch, GOOD_VERDICT)
        out = vision_check.run_vision_check("sess-1", "/tmp/x.step")
        assert out["status"] == "checked"
        assert "悬空" in out["verdict_text"]
        assert out["findings"][0]["item"] == "悬空"
        assert out["model"] == app_config.LLM_VISION_MODEL
        assert out["checked_at"]
        assert out["views_png"]["bucket"] == app_config.BUCKET_STEP_MODELS
        assert out["views_png"]["object_key"].endswith("views.png")
        assert out["views_png"]["object_key"].startswith("sess-1/a3/")

    def test_render_fail_skipped(self, monkeypatch):
        _patch_render(monkeypatch, {"ok": False, "png": None, "error": "tessellate timeout"})
        out = vision_check.run_vision_check("s", "/tmp/x.step")
        assert out == {"status": "skipped", "detail": "render_failed: tessellate timeout"}
        assert set(out) == {"status", "detail"}  # 渲染失败不造后续字段（A-16）

    def test_render_exception_skipped(self, monkeypatch):
        def boom(step):
            raise RuntimeError("spawn failed")

        monkeypatch.setattr("cad_engine.render.render_step_views", boom)
        out = vision_check.run_vision_check("s", "/tmp/x.step")
        assert out["status"] == "skipped" and out["detail"].startswith("render_failed")

    def test_vlm_retry_once_then_fail(self, monkeypatch):
        """两次尝试均 None → skipped+vlm_failed；views_png 留档保留（材料在判词缺位）。"""
        _patch_render(monkeypatch, {"ok": True, "png": FAKE_PNG, "error": ""})
        _patch_upload(monkeypatch)
        calls = _patch_vlm(monkeypatch, [None, None])
        out = vision_check.run_vision_check("s", "/tmp/x.step")
        assert calls["n"] == 2
        assert out["status"] == "skipped"
        assert out["detail"].startswith("vlm_failed")
        assert out["views_png"]["object_key"].endswith("views.png")

    def test_vlm_transient_then_success(self, monkeypatch):
        _patch_render(monkeypatch, {"ok": True, "png": FAKE_PNG, "error": ""})
        _patch_upload(monkeypatch)
        calls = _patch_vlm(monkeypatch, [None, GOOD_VERDICT])
        out = vision_check.run_vision_check("s", "/tmp/x.step")
        assert calls["n"] == 2 and out["status"] == "checked"

    def test_upload_fail_does_not_block(self, monkeypatch):
        _patch_render(monkeypatch, {"ok": True, "png": FAKE_PNG, "error": ""})
        _patch_upload(monkeypatch, exc=RuntimeError("minio down"))
        _patch_vlm(monkeypatch, GOOD_VERDICT)
        out = vision_check.run_vision_check("s", "/tmp/x.step")
        assert out["status"] == "checked" and "views_png" not in out

    def test_verdict_unparseable_keeps_text(self, monkeypatch):
        """判词非 JSON → findings 省略但 verdict_text 全文在（A-16 人工可核对）。"""
        _patch_render(monkeypatch, {"ok": True, "png": FAKE_PNG, "error": ""})
        _patch_upload(monkeypatch)
        _patch_vlm(monkeypatch, "整体未见明显异常，阵列规整。")
        out = vision_check.run_vision_check("s", "/tmp/x.step")
        assert out["status"] == "checked"
        assert "findings" not in out
        assert "未见明显异常" in out["verdict_text"]


# ------------------------------------------------------------------------------
# ir3.schema vision_check 加法演进
class TestSchemaAdditive:
    def test_legacy_payload_still_valid(self):
        assert validate_ir("IR3", dict(BASE_IR3))

    def test_checked_form_valid(self):
        payload = dict(BASE_IR3, vision_check=dict(VC_CHECKED))
        assert validate_ir("IR3", payload)

    def test_skipped_form_valid(self):
        payload = dict(BASE_IR3, vision_check=dict(VC_SKIPPED))
        assert validate_ir("IR3", payload)

    def test_checked_requires_verdict_text(self):
        bad = {"status": "checked", "model": "m"}
        with pytest.raises(IrContractError):
            validate_ir("IR3", dict(BASE_IR3, vision_check=bad))

    def test_skipped_requires_detail(self):
        with pytest.raises(IrContractError):
            validate_ir("IR3", dict(BASE_IR3, vision_check={"status": "skipped"}))

    def test_unknown_key_rejected(self):
        bad = dict(VC_SKIPPED, extra="x")
        with pytest.raises(IrContractError):
            validate_ir("IR3", dict(BASE_IR3, vision_check=bad))

    def test_finding_requires_three_keys(self):
        bad = dict(VC_CHECKED, findings=[{"item": "x", "severity": "warning"}])
        with pytest.raises(IrContractError):
            validate_ir("IR3", dict(BASE_IR3, vision_check=bad))


# ------------------------------------------------------------------------------
# A3 挂点（run() 全链 mock，照 t73 实例属性覆盖先例）
def _patch_agent_chain(monkeypatch, vision_calls):
    """mock run() Step1~Step4：范例未命中/缓存未命中/fake 生成/沙箱 ok/上传 fake。
    active_provider 也须 mock——run() 唯一真实 I/O（llm_providers 查库），底座不在线时
    连接重试会挂死数分钟（2026-11-26 实测 260s/例），全离线纪律必须覆盖它。"""
    def _no_provider():
        raise RuntimeError("offline test：无供应商")

    monkeypatch.setattr("llm.adapter.active_provider", _no_provider)
    monkeypatch.setattr("cad_engine.validators.validate_chain",
                        lambda script, domain=None: (script, True, [], []))
    monkeypatch.setattr("cad_engine.sandbox.run_in_subprocess",
                        lambda code, time_limit_s=300: {"ok": True, "step_files": ["/tmp/fake.step"]})
    monkeypatch.setattr("agents.a3_cad.agent._upload_artifacts",
                        lambda sid, step, script: (dict(STEP_REF), dict(SCRIPT_REF)))
    monkeypatch.setattr("storage.cache.codegen_cache_key", lambda *a, **k: "k")
    monkeypatch.setattr("storage.cache.get_json", lambda *a, **k: None)
    monkeypatch.setattr("storage.cache.set_json", lambda *a, **k: None)

    def fake_run_vision_check(session_id, step_path):
        vision_calls.append((session_id, step_path))
        return dict(VC_CHECKED)

    monkeypatch.setattr("agents.a3_cad.agent.run_vision_check", fake_run_vision_check)


def _run_agent(monkeypatch) -> dict[str, Any]:
    agent = A3Agent(llm=SimpleNamespace(chat=lambda messages, **k: "x = 1\n"))
    agent._ref_service = SimpleNamespace(
        select_reference=lambda doc: (None, None), last_reason="no_catalog")
    agent._generate = lambda doc, params, ref_code, domain: "x = 1\n"  # type: ignore[method-assign]
    agent._inspect_step = lambda path: dict(INSPECTED)  # type: ignore[method-assign]
    ir2 = {"parameters": [], "design_document": "拖曳线列阵声呐湿端段设计"}
    return agent.run("sess-t182", ir2)


class TestAgentHook:
    def test_flag_off_zero_call_byte_identical(self, monkeypatch):
        """默认关：run_vision_check 零调用，IR3 无 vision_check 键且与旧形态 keys 一致
        （回滚保证=逐字节口径，照 t174 test_default_off_byte_identical 形态）。"""
        vision_calls: list = []
        _patch_agent_chain(monkeypatch, vision_calls)
        monkeypatch.setattr(app_config, "CAD_VISION_CHECK", False)
        ir3 = _run_agent(monkeypatch)
        assert vision_calls == []
        assert "vision_check" not in ir3
        assert set(ir3) == {"cadquery_script", "step_file", "features", "geometry_summary",
                            "reference_used"}

    def test_flag_on_field_lands(self, monkeypatch):
        vision_calls: list = []
        _patch_agent_chain(monkeypatch, vision_calls)
        monkeypatch.setattr(app_config, "CAD_VISION_CHECK", True)
        ir3 = validate_ir("IR3", _run_agent(monkeypatch))
        assert len(vision_calls) == 1
        assert vision_calls[0][0] == "sess-t182"
        assert ir3["vision_check"]["status"] == "checked"
        assert ir3["vision_check"]["findings"][0]["item"] == "阵列悬空"

    def test_flag_on_skipped_form_also_lands(self, monkeypatch):
        """skipped 形态也如实进契约（A-16：旁路未完成显性可查）。"""
        vision_calls: list = []
        _patch_agent_chain(monkeypatch, vision_calls)
        monkeypatch.setattr("agents.a3_cad.agent.run_vision_check",
                            lambda sid, path: dict(VC_SKIPPED))
        monkeypatch.setattr(app_config, "CAD_VISION_CHECK", True)
        ir3 = validate_ir("IR3", _run_agent(monkeypatch))
        assert ir3["vision_check"] == VC_SKIPPED

    def test_build_ir3_none_omits_field(self):
        """组装层口径：vision_check=None → 字段省略，keys 与旧调用完全一致。"""
        agent = A3Agent(llm=None)
        agent._inspect_step = lambda path: dict(INSPECTED)  # type: ignore[method-assign]
        legacy = agent._build_ir3("s", dict(STEP_REF), dict(SCRIPT_REF),
                                  {"parameters": []}, "/tmp/x.step", fix_round=1)
        patched = agent._build_ir3("s", dict(STEP_REF), dict(SCRIPT_REF),
                                   {"parameters": []}, "/tmp/x.step", fix_round=1,
                                   vision_check=None)
        assert set(patched) == set(legacy)
        assert "vision_check" not in patched


# ------------------------------------------------------------------------------
# A4 warning 呈现（不进 CSR/coverage 分母，A-07）
class TestA4Vision:
    def test_missing_field_zero_behavior(self):
        assert A4Agent()._check_vision({}) == []
        assert A4Agent()._check_vision({"vision_check": None}) == []

    def test_skipped_warning(self):
        issues = A4Agent()._check_vision({"vision_check": dict(VC_SKIPPED)})
        assert len(issues) == 1
        assert issues[0]["constraint_id"] == "CHK-VISION-SKIPPED"
        assert issues[0]["severity"] == "warning"
        assert "render_failed" in issues[0]["message"]

    def test_findings_mapped(self):
        issues = A4Agent()._check_vision({"vision_check": dict(VC_CHECKED)})
        assert [i["constraint_id"] for i in issues] == ["CHK-VISION-FINDING"]
        assert issues[0]["severity"] == "warning"
        assert "阵列悬空" in issues[0]["message"]

    def test_empty_findings_no_pass_claim(self):
        """checked+空 findings 不造「通过」条目（A-16），也不造 issue。"""
        vc = dict(VC_CHECKED, findings=[])
        assert A4Agent()._check_vision({"vision_check": vc}) == []

    def test_checked_unparseable_no_issue(self):
        vc = {"status": "checked", "verdict_text": "自由文本判词"}
        assert A4Agent()._check_vision({"vision_check": vc}) == []

    def test_integration_csr_denominator_unchanged(self):
        """硬断言：vision_check 有无/形态不改变 CSR 与 coverage 数值与 overall（A-07
        分母纪律——warning 不进分母也不翻总体结论；base=features 未联动属 incomplete
        三态口径，与本测试无关，只断言三态互等）。"""
        ir2 = {"parameters": [], "design_document": "doc"}
        base = A4Agent().run(ir2, dict(BASE_IR3))
        with_checked = A4Agent().run(ir2, dict(BASE_IR3, vision_check=dict(VC_CHECKED)))
        with_skipped = A4Agent().run(ir2, dict(BASE_IR3, vision_check=dict(VC_SKIPPED)))
        assert base["csr"] == with_checked["csr"] == with_skipped["csr"]
        assert base["coverage"] == with_checked["coverage"] == with_skipped["coverage"]
        assert base["overall"] == with_checked["overall"] == with_skipped["overall"]
        vision_issues = [i for i in with_checked["issues"]
                         if i["constraint_id"].startswith("CHK-VISION")]
        assert len(vision_issues) == 1 and vision_issues[0]["severity"] == "warning"
        skipped_issues = [i for i in with_skipped["issues"]
                          if i["constraint_id"] == "CHK-VISION-SKIPPED"]
        assert len(skipped_issues) == 1
