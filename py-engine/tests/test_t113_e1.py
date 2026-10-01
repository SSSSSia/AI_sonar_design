# -*- coding: utf-8 -*-
"""T11.3 单测：E1 参数变更提案（diff 映射 / derivation 同步 / IR2 回写构造）。不依赖 DB。"""

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cad_agent.param_proposals import apply_proposals, diff_proposals, sync_derivation
from solver.recompute import parse_inputs_from_derivation


def _ir2(value=100, unit="mm", derivation="缆长 len=100 mm，按任务书区间取值"):
    return {
        "design_document": "# 方案\n正文",
        "parameters": [{
            "id": "P-001", "name": "缆长", "value": value, "unit": unit,
            "source_type": "estimated", "source_ref": "任务书",
            "derivation": derivation, "confidence": "medium", "trace_id": "t-1",
        }],
    }


def _ir3(bbox=(100.0, 20.0, 20.0), linked="P-001", dim=0, segments=("SEG-01", "SEG-02")):
    feats = []
    for i, seg in enumerate(segments):
        b = list(bbox) if i == 0 else [50.0, 20.0, 20.0]
        feats.append({
            "feature_id": f"F-{i + 1:03d}", "segment": seg,
            "geometry": {"bbox_mm": b, "volume_mm3": 1.0, "center_mm": [0, 0, 0],
                         **({"linked_dim": dim} if i == 0 and linked else {})},
            "linked_param_id": linked if i == 0 and linked else None,
        })
    return {"features": feats}


# ----------------------------------------------------------------------
# diff_proposals
# ----------------------------------------------------------------------
def test_diff_basic_length_change():
    proposals = diff_proposals(_ir2(), _ir3(bbox=(100.0, 20.0, 20.0)), _ir3(bbox=(120.0, 20.0, 20.0)))
    assert len(proposals) == 1
    p = proposals[0]
    assert p["param_id"] == "P-001" and p["old_value"] == 100 and p["new_value"] == 120
    assert p["delta_pct"] == 20.0 and p["dimension"] == 0 and p["unit"] == "mm"


def test_diff_unit_conversion_m_to_mm():
    # 参数 100 m（=100000mm），实测 120000mm → 提案 new_value = 120.0（原生单位 m）
    proposals = diff_proposals(_ir2(value=100, unit="m"), _ir3(bbox=(100000.0, 20, 20)),
                               _ir3(bbox=(120000.0, 20, 20)))
    assert len(proposals) == 1 and proposals[0]["new_value"] == 120.0


def test_diff_segmentation_changed_no_proposal():
    # 微调增删分段 → 无法自动对位，如实不产提案（A-16）
    base = _ir3(segments=("SEG-01", "SEG-02"))
    new = _ir3(bbox=(130.0, 20, 20), segments=("SEG-01", "SEG-02", "SEG-03"))
    assert diff_proposals(_ir2(), base, new) == []


def test_diff_chained_tune_baseline_no_proposal():
    # 链式微调：基线是上次定稿版（linked_param_id=None）→ 0 条
    assert diff_proposals(_ir2(), _ir3(linked=None), _ir3(bbox=(130.0, 20, 20))) == []


def test_diff_below_noise_threshold_ignored():
    # 0.5% 偏差 < MIN_DELTA_PCT(1%)：实测噪声不算变更
    assert diff_proposals(_ir2(), _ir3(bbox=(100.0, 20, 20)), _ir3(bbox=(100.5, 20, 20))) == []


def test_diff_non_length_unit_skipped():
    # 角度/无量纲参数不做几何映射
    ir2 = _ir2()
    ir2["parameters"][0]["unit"] = "dB"
    assert diff_proposals(ir2, _ir3(bbox=(100.0, 20, 20)), _ir3(bbox=(130.0, 20, 20))) == []


# ----------------------------------------------------------------------
# derivation 同步（符号表 setdefault 首匹配 → 必须原地替换）
# ----------------------------------------------------------------------
def test_sync_derivation_replaces_first_symbol_value():
    d = "缆长 len=100 mm，参考 n=16 阵元布置"
    out = sync_derivation(d, 100.0, 120.0, "微调 r1 缆长（P-001）")
    assert "len=120" in out and "len=100" not in out
    assert parse_inputs_from_derivation(out)["len"] == 120.0
    assert "n=16" in out  # 其他符号不动
    assert "E1 实测修订" in out and "100 → 120" in out
    assert "P-001" in out


def test_sync_derivation_without_symbol_pattern_appends_note_only():
    out = sync_derivation("按案例库 C-3 取整", 100.0, 120.0, "微调 r1")
    assert "120" in out and "E1 实测修订" in out
    # 无 symbol=value 可替换——但修订记录本身不能引入符号表键
    assert parse_inputs_from_derivation(out) == {}


def test_sync_derivation_keeps_other_equal_numbers():
    # 其他位置的同值数字（非 symbol= 形态）不受影响
    d = "每 100m 布置 1 个；len=100"
    out = sync_derivation(d, 100.0, 120.0, "r1")
    assert out.startswith("每 100m 布置 1 个；len=120")


# ----------------------------------------------------------------------
# apply_proposals（IR2 回写构造）
# ----------------------------------------------------------------------
def test_apply_proposals_updates_value_and_document():
    ir2 = _ir2()
    original = copy.deepcopy(ir2)
    applied = [{"param_id": "P-001", "param_name": "缆长", "unit": "mm",
                "old_value": 100, "new_value": 120, "delta_pct": 20.0, "dimension": 0}]
    new_ir2 = apply_proposals(ir2, applied, round_no=2)
    assert ir2 == original  # 旧快照不可变（A-12）：深拷贝不改入参
    p = new_ir2["parameters"][0]
    assert p["value"] == 120
    assert parse_inputs_from_derivation(p["derivation"])["len"] == 120.0
    assert "## 修订记录（E1 CAD→文档反向回写）" in new_ir2["design_document"]
    assert "| P-001 | 缆长 | 100 | 120 | mm | 20.0% |" in new_ir2["design_document"]
