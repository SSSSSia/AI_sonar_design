# -*- coding: utf-8 -*-
"""T19.1 配方契约与执行器测试（PHASE-019，契约=ADR-019 决策 1）。

覆盖：runner 拼接（base64 传参尾段 + `result` 变量=sandbox 自动导出契约）语义、
check_coverage 绑定/缺参如实（A-08/A-16）、sample_validate 聚合（mock 沙箱）、
check_connectivity 三态（真 in-process OCCT——2026-11-28 实测脚本同型）、
默认采样组全链真沙箱（先例 test_t14_t15 TestSandbox）。
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from cad_engine import quality, recipe

_REPO = Path(__file__).resolve().parents[2]
RECIPE_PATH = _REPO / "scripts" / "recipes" / "towfish_recipe.py"

MINI_RECIPE = (
    "PARAM_SCHEMA = {'required': [{'id': 'a', 'name_match': 'A', 'unit': 'mm'}]}\n"
    "def build(params):\n"
    "    return {'k': params['a']}\n"
)


class TestRunnerSource:
    def test_tail_hits_sandbox_contract(self):
        """尾段含 result=build(_params) 且 exec 后 result=build(参数)（自动导出契约对齐）。"""
        src = recipe.build_runner_source(MINI_RECIPE, {"a": 3.0, "b": 1200.0})
        assert "result = build(_params)" in src
        assert MINI_RECIPE.rstrip() in src
        ns: dict = {}
        exec(compile(src, "<runner>", "exec"), ns)  # noqa: S102 - mini build 纯 python（无 OCCT）
        assert ns["result"] == {"k": 3.0}
        payload = src.split("b64decode('")[1].split("')")[0]
        assert json.loads(base64.b64decode(payload).decode("utf-8")) == {"a": 3.0, "b": 1200.0}

    def test_towfish_compilable_single_file(self):
        """拖鱼配方+尾段拼接后语法完整（沙箱单文件纪律）。"""
        source, _schema, samples = recipe.load_recipe(RECIPE_PATH)
        src = recipe.build_runner_source(source, dict(samples[0]["params"]))
        compile(src, "<runner>", "exec")

    def test_load_recipe_rejects_missing_schema(self, tmp_path):
        p = tmp_path / "bad.py"
        p.write_text("def build(params):\n    return None\n", encoding="utf-8")
        with pytest.raises(ValueError, match="PARAM_SCHEMA"):
            recipe.load_recipe(p)


class TestCheckCoverage:
    SCHEMA = {
        "required": [
            {"id": "body_radius", "name_match": "主体半径", "unit": "mm"},
            {"id": "body_length", "name_match": "主体耐压段长度", "unit": "mm"},
        ],
        "optional": [{"id": "ring_width", "name_match": "拖曳环宽度", "unit": "mm", "default": 15.0}],
    }

    def test_name_exact_and_optional_default(self):
        ir2 = [{"id": "P-01", "name": "主体半径", "value": 100},
               {"id": "P-02", "name": "主体耐压段长度", "value": "1200"}]
        bound, missing = recipe.check_coverage(self.SCHEMA, ir2)
        assert bound == {"body_radius": 100.0, "body_length": 1200.0, "ring_width": 15.0}
        assert missing == []

    def test_id_fallback_and_missing_required(self):
        ir2 = [{"id": "body_radius", "name": "R", "value": 80}]
        bound, missing = recipe.check_coverage(self.SCHEMA, ir2)
        assert bound["body_radius"] == 80.0
        assert any("主体耐压段长度" in m for m in missing)
        assert bound.get("ring_width") == 15.0  # optional 缺失取声明 default

    def test_non_numeric_is_missing(self):
        ir2 = [{"id": "P-01", "name": "主体半径", "value": "约一百"},
               {"id": "P-02", "name": "主体耐压段长度", "value": 1200}]
        bound, missing = recipe.check_coverage(self.SCHEMA, ir2)
        assert "body_radius" not in bound
        assert any("不可数值化" in m for m in missing)

    def test_empty_params_all_missing(self):
        bound, missing = recipe.check_coverage(self.SCHEMA, [])
        assert bound == {"ring_width": 15.0}
        assert len(missing) == 2


_GENERIC_SAMPLES = [{"name": f"s{i}", "params": {"a": float(i)}} for i in range(1, 4)]


class TestSampleValidateAggregation:
    """mock 沙箱+连通+实测——聚合语义（通过采样组不含 expected_bbox，bbox 比对如实跳过）。"""

    def _patch(self, monkeypatch, sandbox_results):
        seq = list(sandbox_results)

        def fake_run(code, time_limit_s=180):
            r = seq.pop(0) if seq else {"ok": True}
            return {"ok": r.get("ok", True), "error": r.get("error", ""),
                    "step_files": [Path("/tmp/fake.step")] if r.get("ok", True) else [],
                    "auto_exported": True}

        monkeypatch.setattr("cad_engine.sandbox.run_in_subprocess", fake_run)
        monkeypatch.setattr("cad_engine.quality.check_connectivity",
                            lambda p: {"measured": True, "solids": 1, "valid": True,
                                       "components": 1, "connected": True,
                                       "min_pair_overlap_ratio": 0.5})
        monkeypatch.setattr("cad_engine.quality.measure_step",
                            lambda p: {"measured": True, "overall_bbox_mm": [10.0, 10.0, 10.0],
                                       "overall_volume_mm3": 1.0, "part_count": 1})

    def test_all_pass(self, monkeypatch):
        self._patch(monkeypatch, [{"ok": True}, {"ok": True}, {"ok": True}])
        report = recipe.sample_validate(RECIPE_PATH, samples=_GENERIC_SAMPLES)
        assert report["all_pass"] is True
        assert len(report["samples"]) == 3
        assert all(s["pass"] for s in report["samples"])

    def test_one_fail_honest(self, monkeypatch):
        self._patch(monkeypatch, [{"ok": True}, {"ok": False, "error": "执行超时（>180s）"}, {"ok": True}])
        report = recipe.sample_validate(RECIPE_PATH, samples=_GENERIC_SAMPLES)
        assert report["all_pass"] is False
        assert report["samples"][1]["pass"] is False
        assert "超时" in report["samples"][1]["error"]

    def test_no_samples_honest(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, [])
        p = tmp_path / "mini.py"
        p.write_text(MINI_RECIPE, encoding="utf-8")
        report = recipe.sample_validate(p)
        assert report["all_pass"] is False
        assert "无可验证采样组" in report["reason"]


class TestConnectivityTriState:
    """真 in-process OCCT 三态（2026-11-28 审查实测同型：N/A 上限/重叠连通/分离不连通）。"""

    @staticmethod
    def _export(tmp_path: Path, name: str, shape) -> Path:
        import cadquery as cq

        p = tmp_path / name
        cq.exporters.export(shape, str(p))
        return p

    def test_tri_state(self, tmp_path):
        import cadquery as cq

        def box(x, y):
            return cq.Workplane("XY").box(10, 10, 10).translate((x, y, 0)).val()

        na = self._export(tmp_path, "na.step",
                          cq.Compound.makeCompound([box(i * 50, 0) for i in range(13)]))
        ov = self._export(tmp_path, "ov.step", cq.Compound.makeCompound([box(0, 0), box(5, 0)]))
        sep = self._export(tmp_path, "sep.step", cq.Compound.makeCompound([box(0, 0), box(50, 0)]))
        c_na = quality.check_connectivity(na)
        c_ov = quality.check_connectivity(ov)
        c_sep = quality.check_connectivity(sep)
        assert c_na["connected"] is None and "上限" in c_na.get("reason", "")
        assert c_ov["connected"] is True and c_ov["components"] == 1
        assert c_sep["connected"] is False and c_sep["components"] == 2


class TestRealSandboxDefaultSample:
    """真沙箱全链（先例 test_t14_t15 TestSandbox）：默认组 ok+auto_exported+连通+bbox。"""

    def test_default_sample_full_chain(self):
        samples = [s for s in recipe.load_recipe(RECIPE_PATH)[2] if s["name"] == "default"]
        report = recipe.sample_validate(RECIPE_PATH, time_limit_s=180, samples=samples)
        s = report["samples"][0]
        assert s["sandbox_ok"] is True, s.get("error")
        assert s["auto_exported"] is True
        assert s["connectivity"]["connected"] is True
        assert s["connectivity"]["solids"] == 1
        assert s["bbox_check"]["pass"] is True, s["bbox_check"]["detail"]
        assert report["all_pass"] is True
