# -*- coding: utf-8 -*-
"""T14.1 执行器行为测试（无 FreeCAD 环境，桩形体替身）。

覆盖外部审查问题 6 的修复面：
  - snapshot_lite 导入路径修复（旧代码在 execute_edit 内局部裸导入必然 ModuleNotFoundError）
  - result 严格校验（42/空形体/非实体拒绝）
  - 目标锁定连续性（切削后体积变小，下轮仍作用于演进对象，不回选原模型）
  - 锁定失效回退最大体积并标注 target_fallback
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest


class FakeShape:
    def __init__(self, volume, kind="Solid", null=False):
        self.Volume = volume
        self.ShapeType = kind
        self._null = null

    def isNull(self):
        return self._null

    @property
    def Solids(self):
        return [self] if self.ShapeType == "Solid" and not self._null else []


class FakeObj:
    def __init__(self, name, label, shape):
        self.Name = name
        self.Label = label
        self.Shape = shape


class FakeDoc:
    def __init__(self, objects=None):
        self.Name = "SonarModel"
        self.Objects = objects or []

    def getObject(self, name):
        for o in self.Objects:
            if o.Name == name:
                return o
        return None

    def getObjectsByLabel(self, label):
        return [o for o in self.Objects if o.Label == label]


@pytest.fixture()
def exec_env(monkeypatch):
    """桩 FreeCAD 环境下全新导入 executor（避免被其他测试的无桩导入污染）。"""
    for m in list(sys.modules):
        if m == "sonarlink" or m.startswith("sonarlink."):
            del sys.modules[m]
    freecad = types.ModuleType("FreeCAD")
    doc = FakeDoc()
    freecad.ActiveDocument = doc
    monkeypatch.setitem(sys.modules, "FreeCAD", freecad)
    monkeypatch.setitem(sys.modules, "FreeCADGui", types.ModuleType("FreeCADGui"))
    monkeypatch.setitem(sys.modules, "Part", types.ModuleType("Part"))
    monkeypatch.setitem(sys.modules, "Import", types.ModuleType("Import"))
    monkeypatch.setitem(sys.modules, "PySide6", types.ModuleType("PySide6"))
    executor = importlib.import_module("sonarlink.cad.executor")
    # 质量门禁替换为固定通过（doc 形体分析不属于本测试面）
    monkeypatch.setattr(executor, "analyze_document_quality",
                        lambda d: types.SimpleNamespace(severity="ok", issues=[]))
    executor._EXEC_NAMESPACE.clear()
    executor._TARGET["name"] = None
    executor._stop_flag["stopped"] = False
    yield executor, freecad, doc
    executor._EXEC_NAMESPACE.clear()
    executor._TARGET["name"] = None


def test_edit_ok_and_lock_continuity(exec_env, monkeypatch):
    """连续编辑作用于锁定对象：原模型体积更大也不回选（审查问题 6b）。"""
    executor, freecad, doc = exec_env
    orig = FakeShape(1000.0)
    cut = FakeShape(800.0)
    doc.Objects = [FakeObj("Orig", "SonarModel", orig), FakeObj("Edit1", "Edit", cut)]
    executor._TARGET["name"] = "Orig"

    taken = []
    monkeypatch.setattr(executor.snapshot_lite, "take", lambda d: taken.append(d) or "snap")

    r1 = executor.execute_edit("prev = model\nresult = model")
    assert r1["ok"] is True, r1["error"]
    assert r1["target_fallback"] is False
    assert taken, "安全网快照未执行（导入修复后必须可达）"
    assert executor._TARGET["name"] == "Edit1"  # 上轮 result 对象滚动锁定

    r2 = executor.execute_edit("prev2 = model\nresult = model")
    assert r2["ok"] is True, r2["error"]
    ns = executor._EXEC_NAMESPACE
    assert ns["prev"].Volume == 1000.0
    assert ns["prev2"].Volume == 800.0  # 第二轮 model 是演进对象，不是体积最大的 Orig


def test_fallback_when_lock_missing(exec_env):
    """锁定对象不存在（如被手动删除）→ 回退最大体积并如实标注。"""
    executor, freecad, doc = exec_env
    big = FakeShape(1000.0)
    doc.Objects = [FakeObj("Big", "SonarModel", big)]
    executor._TARGET["name"] = "Ghost"

    r = executor.execute_edit("prev = model\nresult = model")
    assert r["ok"] is True, r["error"]
    assert r["target_fallback"] is True
    assert executor._EXEC_NAMESPACE["prev"].Volume == 1000.0  # 回退拿到最大体积对象


def test_result_number_rejected(exec_env):
    """result=42 必须拒绝——旧实现 _as_shape 原样返回数值，契约形同虚设。"""
    executor, freecad, doc = exec_env
    doc.Objects = [FakeObj("M", "SonarModel", FakeShape(100.0))]
    executor._TARGET["name"] = "M"

    r = executor.execute_edit("result = 42")
    assert r["ok"] is False
    assert "不是可接受的形体" in (r["error"] or "")


def test_result_null_shape_rejected(exec_env):
    executor, freecad, doc = exec_env
    doc.Objects = [FakeObj("M", "SonarModel", FakeShape(100.0))]
    executor._TARGET["name"] = "M"
    executor._EXEC_NAMESPACE["_null_shape"] = FakeShape(0.0, null=True)

    r = executor.execute_edit("result = _null_shape")
    assert r["ok"] is False
    assert "空形体" in (r["error"] or "")


def test_result_non_solid_rejected(exec_env):
    """Shell/线框 result（无实体）拒绝——契约要求单一实体。"""
    executor, freecad, doc = exec_env
    doc.Objects = [FakeObj("M", "SonarModel", FakeShape(100.0))]
    executor._TARGET["name"] = "M"
    executor._EXEC_NAMESPACE["_shell"] = FakeShape(5.0, kind="Shell")

    r = executor.execute_edit("result = _shell")
    assert r["ok"] is False
    assert "未包含实体" in (r["error"] or "")


def test_workplane_result_accepted(exec_env):
    """CQ Workplane（.solid()）载体照常接受。"""
    executor, freecad, doc = exec_env
    shape = FakeShape(300.0)
    doc.Objects = [FakeObj("M", "SonarModel", FakeShape(100.0))]
    executor._TARGET["name"] = "M"
    executor._EXEC_NAMESPACE["_wp"] = types.SimpleNamespace(solid=lambda: shape)

    r = executor.execute_edit("result = _wp")
    assert r["ok"] is True, r["error"]


def test_no_result_rejected(exec_env):
    executor, freecad, doc = exec_env
    doc.Objects = [FakeObj("M", "SonarModel", FakeShape(100.0))]
    executor._TARGET["name"] = "M"

    r = executor.execute_edit("x = model")
    assert r["ok"] is False
    assert "未赋值 result" in (r["error"] or "")
