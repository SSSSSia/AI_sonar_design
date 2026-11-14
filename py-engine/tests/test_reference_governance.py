# -*- coding: utf-8 -*-
"""T16.0 A0-1/A0-2 参考范例治理单测（PHASE-016，DEV-001 §1.2）。

- A0-1 外置与刷新：REF_CODE_DIR 外置目录/受控包内回退、目录指纹热更新（新增/修改/
  删除触发整体重载）、坏目录/坏 YAML 进入"无索引"降级且修复后自动恢复；
- A0-2 匹配语义：三级 grade（type_matched/weak_keyword/none）、弱匹配默认过滤
  （REF_MATCH_ALLOW_WEAK 可开）、**无匹配/空索引不注入任何默认文件**（旧版
  "回退第一个 .py"错域静默注入根因的回归锚）、版本与 sha256 对应实际读取内容。
"""

import hashlib
import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app_config
from cad_engine.reference import (
    REASON_NO_CATALOG,
    REASON_NO_MATCH,
    REASON_READ_FAILED,
    REASON_WEAK_FILTERED,
    CodeSearchService,
)

REF_BODY_V1 = "REF_A_V1 = 'towed pattern'\n"
REF_BODY_V2 = "REF_A_V2 = 'towed pattern updated'\n"

CATALOG_ENTRY = {
    "id": "towed_array_system_twin",
    "file": "towed_array_system_twin.py",
    "domain": "towed_array",
    "sonar_types": ["拖曳线列阵声呐"],
    "keywords": ["水听器", "拖曳阵", "减振段"],
}


def _write_catalog(dir_path: Path, entries: list, version: str = "1.0") -> None:
    (dir_path / "catalog.yaml").write_text(
        yaml.safe_dump({"version": version, "reference_files": entries}, allow_unicode=True),
        encoding="utf-8",
    )


def _make_ref_dir(tmp_path: Path) -> Path:
    d = tmp_path / "ref_code"
    d.mkdir()
    (d / "towed_array_system_twin.py").write_text(REF_BODY_V1, encoding="utf-8")
    # 诱饵脚本：不在 catalog 索引内——旧版 fallback 会取字母序第一个 .py 静默注入
    (d / "aaa_decoy_not_in_catalog.py").write_text("DECOY = 1\n", encoding="utf-8")
    _write_catalog(d, [CATALOG_ENTRY])
    return d


# ---------------------------------------------------------------- A0-1 外置与刷新
def test_ref_code_dir_env_and_package_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(app_config, "REF_CODE_DIR", str(tmp_path / "external"))
    assert CodeSearchService()._catalog_dir == Path(tmp_path / "external")
    monkeypatch.setattr(app_config, "REF_CODE_DIR", "")
    assert CodeSearchService()._catalog_dir == Path(__file__).resolve().parents[1] / "cad_engine" / "ref_code"


def test_hot_reload_picks_up_file_change(tmp_path):
    d = _make_ref_dir(tmp_path)
    svc = CodeSearchService(d)
    content, match = svc.select_reference("# 拖曳线列阵声呐 CAD 设计文档\n水听器布置")
    assert content == REF_BODY_V1 and match["sha256"] == hashlib.sha256(REF_BODY_V1.encode()).hexdigest()

    # 修改正文（A0-1 验收：同一长驻实例内更新可见，版本/内容同快照）
    (d / "towed_array_system_twin.py").write_text(REF_BODY_V2, encoding="utf-8")
    os.utime(d / "towed_array_system_twin.py", ns=(2_000_000_000, 2_000_000_000))
    content, match = svc.select_reference("# 拖曳线列阵声呐 CAD 设计文档\n水听器布置")
    assert content == REF_BODY_V2
    assert match["sha256"] == hashlib.sha256(REF_BODY_V2.encode()).hexdigest()


def test_hot_reload_picks_up_catalog_change(tmp_path):
    d = _make_ref_dir(tmp_path)
    svc = CodeSearchService(d)
    _, match = svc.select_reference("# 拖曳线列阵声呐 CAD 设计文档\n水听器")
    assert match["catalog_version"] == "1.0"

    _write_catalog(d, [CATALOG_ENTRY], version="2.0")
    os.utime(d / "catalog.yaml", ns=(3_000_000_000, 3_000_000_000))
    _, match = svc.select_reference("# 拖曳线列阵声呐 CAD 设计文档\n水听器")
    assert match["catalog_version"] == "2.0"


def test_bad_yaml_degrades_then_recovers(tmp_path):
    d = tmp_path / "ref_code"
    d.mkdir()
    (d / "towed_array_system_twin.py").write_text(REF_BODY_V1, encoding="utf-8")
    (d / "catalog.yaml").write_text("{invalid yaml::::", encoding="utf-8")
    svc = CodeSearchService(d)
    content, match = svc.select_reference("# 拖曳线列阵声呐 CAD 设计文档\n水听器")
    assert content == "" and match is None and svc.last_reason == REASON_NO_CATALOG

    # 修复后下次访问自动恢复（A0-1 读失败后的恢复）
    _write_catalog(d, [CATALOG_ENTRY])
    os.utime(d / "catalog.yaml", ns=(4_000_000_000, 4_000_000_000))
    content, match = svc.select_reference("# 拖曳线列阵声呐 CAD 设计文档\n水听器")
    assert content == REF_BODY_V1 and match["grade"] == "type_matched"


def test_missing_dir_degrades(tmp_path):
    svc = CodeSearchService(tmp_path / "nonexistent")
    content, match = svc.select_reference("# 拖曳线列阵声呐 CAD 设计文档\n水听器")
    assert content == "" and match is None and svc.last_reason == REASON_NO_CATALOG


def test_read_failed_reason(tmp_path):
    d = tmp_path / "ref_code"
    d.mkdir()
    _write_catalog(d, [{**CATALOG_ENTRY, "file": "missing_ref.py"}])
    svc = CodeSearchService(d)
    content, match = svc.select_reference("# 拖曳线列阵声呐 CAD 设计文档\n水听器")
    assert content == "" and match is None and svc.last_reason == REASON_READ_FAILED


# ---------------------------------------------------------------- A0-2 匹配语义
def test_type_matched_injection_with_contract_fields(tmp_path):
    d = _make_ref_dir(tmp_path)
    svc = CodeSearchService(d)
    content, match = svc.select_reference("# 拖曳线列阵声呐 CAD 设计文档\n水听器布置")
    assert content == REF_BODY_V1
    assert set(match) == {"ref_id", "file", "sha256", "catalog_version", "grade", "score", "domain"}
    assert match["ref_id"] == "towed_array_system_twin"
    assert match["file"] == "towed_array_system_twin.py"
    assert match["grade"] == "type_matched"
    assert match["catalog_version"] == "1.0"
    assert svc.last_reason is None


def test_no_match_never_falls_back_to_decoy(tmp_path):
    """A0-2 核心回归：无匹配不得自动注入错域默认文件（旧版取目录第一个 .py）。"""
    d = _make_ref_dir(tmp_path)
    svc = CodeSearchService(d)
    doc = "# 完全无关设备 CAD 设计文档\n\n本设备用于气象观测。气象站由传感器与支架组成。"
    content, match = svc.select_reference(doc)
    assert content == "" and match is None
    assert svc.last_reason == REASON_NO_MATCH
    assert "DECOY" not in content


def test_empty_index_no_injection(tmp_path):
    d = tmp_path / "ref_code"
    d.mkdir()
    _write_catalog(d, [])
    svc = CodeSearchService(d)
    content, match = svc.select_reference("# 拖曳线列阵声呐 CAD 设计文档\n水听器")
    assert content == "" and match is None and svc.last_reason == REASON_NO_CATALOG


def test_weak_filtered_by_default_then_allowed(monkeypatch, tmp_path):
    d = tmp_path / "ref_code"
    d.mkdir()
    (d / "towed_array_system_twin.py").write_text(REF_BODY_V1, encoding="utf-8")
    _write_catalog(d, [CATALOG_ENTRY])
    # 标题不含 sonar_types，正文含关键词 → weak 候选（combined ≥ 0.05 但 type 未命中）
    doc = "# 阵列布置设计文档\n\n水听器阵列布置要求与减振段配置说明。"
    monkeypatch.setattr(app_config, "REF_MATCH_ALLOW_WEAK", False)
    svc = CodeSearchService(d)
    content, match = svc.select_reference(doc)
    assert content == "" and match is None and svc.last_reason == REASON_WEAK_FILTERED

    monkeypatch.setattr(app_config, "REF_MATCH_ALLOW_WEAK", True)
    content, match = svc.select_reference(doc)
    assert content == REF_BODY_V1 and match["grade"] == "weak_keyword"


def test_type_match_beats_higher_weak_score(monkeypatch, tmp_path):
    d = tmp_path / "ref_code"
    d.mkdir()
    (d / "towed_array_system_twin.py").write_text("TOWED\n", encoding="utf-8")
    (d / "generic_rich.py").write_text("GENERIC\n", encoding="utf-8")
    _write_catalog(d, [
        {**CATALOG_ENTRY, "file": "towed_array_system_twin.py"},
        {  # 弱匹配高分条目（关键词密集）
            "id": "generic_rich", "file": "generic_rich.py",
            "sonar_types": ["never_matches"], "keywords": ["阵列", "布置", "配置", "说明"],
        },
    ])
    monkeypatch.setattr(app_config, "REF_MATCH_ALLOW_WEAK", True)  # 只验证排序，不验证过滤
    svc = CodeSearchService(d)
    content, match = svc.select_reference("# 拖曳线列阵声呐 CAD 设计文档\n阵列布置配置说明")
    assert match["file"] == "towed_array_system_twin.py"
    assert match["grade"] == "type_matched"


def test_ref_id_falls_back_to_filename_stem(tmp_path):
    d = tmp_path / "ref_code"
    d.mkdir()
    (d / "some_ref.py").write_text("X = 1\n", encoding="utf-8")
    entry = {"file": "some_ref.py", "sonar_types": ["甲型声呐"], "keywords": ["水听器"]}
    _write_catalog(d, [entry])
    svc = CodeSearchService(d)
    _, match = svc.select_reference("# 甲型声呐 CAD 设计文档\n正文")
    assert match["ref_id"] == "some_ref"


# ---------------------------------------------------------------- A0-5 领域策略边界
def test_match_carries_declared_domain(tmp_path):
    d = _make_ref_dir(tmp_path)
    svc = CodeSearchService(d)
    _, match = svc.select_reference("# 拖曳线列阵声呐 CAD 设计文档\n水听器")
    assert match["domain"] == "towed_array"


def test_match_domain_empty_when_undeclared(tmp_path):
    d = tmp_path / "ref_code"
    d.mkdir()
    (d / "some_ref.py").write_text("X = 1\n", encoding="utf-8")
    _write_catalog(d, [{"file": "some_ref.py", "sonar_types": ["甲型声呐"], "keywords": ["水听器"]}])
    svc = CodeSearchService(d)
    _, match = svc.select_reference("# 甲型声呐 CAD 设计文档\n正文")
    assert match["domain"] == ""


def test_system_prompt_domain_boundary():
    from agents.a3_cad.prompts import (
        CADQUERY_BASE_PROMPT,
        DOMAIN_PROMPTS,
        build_system_prompt,
    )

    base = build_system_prompt(None)
    assert base == CADQUERY_BASE_PROMPT
    # 未注册领域如实只有通用段（不误用拖曳阵强制模式）
    assert build_system_prompt("side_scan") == CADQUERY_BASE_PROMPT
    assert "sin vs sin²" not in base and "get_spline_pts" not in base
    # 注册领域 = 通用 + 领域段
    towed = build_system_prompt("towed_array")
    assert towed.startswith(CADQUERY_BASE_PROMPT)
    # T15.1 振幅条款改名"视口自适应"→"振幅物理/显示分离"（视觉缩放必须 @display 声明）
    for marker in ("sin²", "振幅物理/显示分离", "get_spline_pts"):
        assert marker in towed
    assert "get_spline_pts" in DOMAIN_PROMPTS["towed_array"]


def test_fix_prompt_domain_boundary():
    from agents.a3_cad.prompts import FIX_BASE_PROMPT, build_fix_prompt

    assert build_fix_prompt(None) == FIX_BASE_PROMPT
    assert build_fix_prompt("side_scan") == FIX_BASE_PROMPT
    towed_fix = build_fix_prompt("towed_array")
    assert "BRepOffsetAPI" in towed_fix  # sin² 修复条款仅拖曳阵领域
    assert "BRepOffsetAPI" not in FIX_BASE_PROMPT


def test_generic_fixes_run_without_domain():
    """A0-5：语法/完整性/RGB 修复对任何领域恒执行；领域修复需声明。"""
    from cad_engine import validators

    code = validators.auto_fix_patterns("c1 = Color(51, 102, 153)")
    assert "Color(0.2000, 0.4000, 0.6000)" in code  # RGB 修复=通用层
    assert validators.validate_critical_patterns("x = 1", domain=None) == []
    assert validators.validate_critical_patterns("x = 1", domain="side_scan") == []
    bad_spline = "def get_spline_pts(length, amplitude):\n    x = math.sin(math.pi * pct)\n"
    assert validators.validate_critical_patterns(bad_spline, domain="towed_array")  # 领域诊断命中
    assert validators.validate_critical_patterns(bad_spline, domain="side_scan") == []


def test_domain_registry_versions_feed_cache_key():
    """A0-3/A0-5：prompt 与领域规则版本存在且入缓存指纹（同 IR2 换版本必 miss）。"""
    from agents.a3_cad import prompts
    from cad_engine import validators
    from storage import cache

    ir2 = {"design_document": "d", "parameters": []}
    assert prompts.PROMPT_VERSION and validators.DOMAIN_RULES_VERSION
    k1 = cache.codegen_cache_key(
        ir2, "p", "1.0:aaa", prompts.PROMPT_VERSION, validators.DOMAIN_RULES_VERSION,
    )
    k2 = cache.codegen_cache_key(ir2, "p", "1.0:aaa", "changed", validators.DOMAIN_RULES_VERSION)
    k3 = cache.codegen_cache_key(ir2, "p", "1.0:aaa", prompts.PROMPT_VERSION, "changed")
    assert k1 != k2 and k1 != k3
