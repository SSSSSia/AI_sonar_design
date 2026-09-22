"""T7.2 geometry_inspect 单测（PHASE-007）。

合成 STEP 验证提取契约与分段三级降级阶梯：
- 双分离圆柱 → 连通体拆分（part_count=2，体积守恒）；
- 并轴变径融合单 solid → 半径剖面切分（part_count=2，体积守恒）；
- 单半径圆柱 → 兜底单段；
- 垃圾字节 → {"ok": false} 不抛异常；
- CLI subprocess 调用（A3 实际路径）。
"""

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

cq = pytest.importorskip("cadquery")

from cad_engine.geometry_inspect import inspect_step  # noqa: E402

CLI = Path(__file__).resolve().parents[1] / "cad_engine" / "geometry_inspect.py"


def _export_step(shape, tmp_path: Path) -> bytes:
    path = tmp_path / "model.step"
    cq.exporters.export(shape, str(path), exportType="STEP")
    return path.read_bytes()


def _two_separate_cylinders():
    """同半径、轴向间隔的两独立圆柱（未融合多体 → 连通体拆分分支）。"""
    c1 = cq.Workplane("XY").circle(20.0).extrude(100.0).val()
    c2 = cq.Workplane("XY").workplane(offset=200.0).circle(20.0).extrude(100.0).val()
    return cq.Compound.makeCompound([c1, c2])


def _fused_two_radius_solid():
    """并轴变径融合单 solid（R20×100 + R10×100 → 半径剖面分支）。"""
    s1 = cq.Solid.makeCylinder(20.0, 100.0)
    s2 = cq.Solid.makeCylinder(10.0, 100.0, cq.Vector(0, 0, 100))
    return s1.fuse(s2)


def _assert_volume_conservation(result: dict) -> None:
    total_parts = sum(p["volume_mm3"] for p in result["parts"])
    assert total_parts == pytest.approx(result["overall"]["volume_mm3"], rel=1e-6)


# ------------------------------------------------------------------------------
def test_two_bodies_connected_components(tmp_path) -> None:
    result = inspect_step(_export_step(_two_separate_cylinders(), tmp_path))
    assert result["ok"] is True, result
    assert result["meta"]["segmentation"] == "connected_components"
    assert result["overall"]["part_count"] == 2
    # 整体 bbox：Ø40 × 长 300（含 100 间隔）
    bbox = result["overall"]["bbox_mm"]
    assert bbox[0] == pytest.approx(40.0, rel=0.05)
    assert bbox[1] == pytest.approx(40.0, rel=0.05)
    assert bbox[2] == pytest.approx(300.0, rel=0.02)
    # 每段体积 ≈ π·20²·100，轴向升序
    expect = math.pi * 20.0 ** 2 * 100.0
    for part in result["parts"]:
        assert part["volume_mm3"] == pytest.approx(expect, rel=0.12)  # 镶嵌多边形低估
    assert result["parts"][0]["center_mm"][2] < result["parts"][1]["center_mm"][2]
    _assert_volume_conservation(result)


def test_fused_solid_radius_profile(tmp_path) -> None:
    result = inspect_step(_export_step(_fused_two_radius_solid(), tmp_path))
    assert result["ok"] is True, result
    # 融合为单 solid：连通体拆分无效，半径剖面应切出 2 段
    assert result["meta"]["segmentation"] == "radius_profile"
    assert result["overall"]["part_count"] == 2
    vols = [p["volume_mm3"] for p in result["parts"]]
    assert vols[0] == pytest.approx(math.pi * 20.0 ** 2 * 100.0, rel=0.12)
    assert vols[1] == pytest.approx(math.pi * 10.0 ** 2 * 100.0, rel=0.12)
    # 段 bbox：细段 dx≈20，粗段 dx≈40；轴向升序
    assert result["parts"][0]["bbox_mm"][0] == pytest.approx(40.0, rel=0.05)
    assert result["parts"][1]["bbox_mm"][0] == pytest.approx(20.0, rel=0.08)
    _assert_volume_conservation(result)


def test_single_cylinder_fallback_single(tmp_path) -> None:
    solid = cq.Solid.makeCylinder(15.0, 200.0)
    result = inspect_step(_export_step(solid, tmp_path))
    assert result["ok"] is True
    assert result["meta"]["segmentation"] == "fallback_single"
    assert result["overall"]["part_count"] == 1
    assert len(result["parts"]) == 1


def test_garbage_bytes_returns_ok_false() -> None:
    result = inspect_step(b"this is not a STEP file")
    assert result["ok"] is False
    assert "error" in result


def test_cli_subprocess(tmp_path) -> None:
    """A3 的实际调用路径：subprocess 跑 CLI，读状态 JSON。"""
    step_path = tmp_path / "m.step"
    out_path = tmp_path / "out.json"
    step_path.write_bytes(_export_step(_fused_two_radius_solid(), tmp_path))
    proc = subprocess.run(
        [sys.executable, str(CLI), str(step_path), str(out_path)],
        capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(out_path.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["meta"]["segmentation"] == "radius_profile"
    assert result["overall"]["part_count"] == 2


def test_cli_garbage_exit_1(tmp_path) -> None:
    step_path = tmp_path / "bad.step"
    out_path = tmp_path / "out.json"
    step_path.write_bytes(b"junk")
    proc = subprocess.run(
        [sys.executable, str(CLI), str(step_path), str(out_path)],
        capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 1
    result = json.loads(out_path.read_text(encoding="utf-8"))
    assert result["ok"] is False
