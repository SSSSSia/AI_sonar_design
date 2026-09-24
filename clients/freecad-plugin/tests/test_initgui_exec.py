# -*- coding: utf-8 -*-
"""InitGui exec 加载模拟测试：复现真机「name '__file__' is not defined」（2026-09-24 用户报错）。

FreeCAD 以 exec 源码方式加载 InitGui.py——多数版本不注入 __file__，全局仅预注入
FreeCAD/FreeCADGui/Workbench。本测试在无 __file__ 的最小全局里 exec 真实 InitGui.py，
验证插件目录定位回退（用户目录 Mod → 安装目录 Mod）与命令/工作台注册。
"""

from __future__ import annotations

import os
import sys
import types

import pytest

REPO_INITGUI = os.path.join(os.path.dirname(__file__), "..", "SonarCADLink", "InitGui.py")


@pytest.fixture()
def stub_freecad(tmp_path, monkeypatch):
    """最小 FreeCAD/FreeCADGui 桩：可配置两个 Mod 根；清理 sys.path 注入。"""
    fc = types.ModuleType("FreeCAD")
    gui = types.ModuleType("FreeCADGui")
    registered = {"commands": [], "workbenches": []}
    gui.addCommand = lambda name, _cmd: registered["commands"].append(name)
    gui.addWorkbench = lambda wb: registered["workbenches"].append(wb)
    fc._registered = registered
    monkeypatch.setitem(sys.modules, "FreeCAD", fc)
    monkeypatch.setitem(sys.modules, "FreeCADGui", gui)
    yield fc, gui


def _exec_initgui(fc):
    """在无 __file__ 的最小全局中 exec 真实 InitGui.py（复现真机 exec 环境）。"""
    source = open(os.path.abspath(REPO_INITGUI), encoding="utf-8").read()
    g = {"FreeCAD": fc, "FreeCADGui": sys.modules["FreeCADGui"],
         "Workbench": type("Workbench", (), {})}
    exec(compile(source, "InitGui.py", "exec"), g)  # noqa: S102 - 被测对象就是 exec 加载
    return g


def test_exec_without_file_resolves_mod_dir(stub_freecad, tmp_path, monkeypatch):
    fc, gui = stub_freecad
    fc.getUserAppDataDir = lambda: str(tmp_path / "user")
    fc.getHomePath = lambda: str(tmp_path / "home")
    mod_dir = tmp_path / "home" / "Mod" / "SonarCADLink"
    mod_dir.mkdir(parents=True)
    (mod_dir / "InitGui.py").write_text("# marker", encoding="utf-8")

    g = _exec_initgui(fc)

    assert g["_here"] == str(mod_dir)
    assert str(mod_dir) in sys.path
    assert fc._sonarlink_dir == str(mod_dir)
    assert "SonarCADLink_ShowPanel" in fc._registered["commands"]
    assert len(fc._registered["workbenches"]) == 1
    monkeypatch.syspath_prepend  # noqa: B018 - sys.path 清理由下述过滤完成
    sys.path.remove(str(mod_dir))


def test_exec_with_file_takes_precedence(stub_freecad, tmp_path):
    fc, gui = stub_freecad
    fc.getUserAppDataDir = lambda: str(tmp_path / "user")
    fc.getHomePath = lambda: str(tmp_path / "home")
    alt = tmp_path / "elsewhere" / "SonarCADLink"
    alt.mkdir(parents=True)
    (alt / "InitGui.py").write_text("# marker", encoding="utf-8")

    source = open(os.path.abspath(REPO_INITGUI), encoding="utf-8").read()
    g = {"FreeCAD": fc, "FreeCADGui": gui, "Workbench": type("Workbench", (), {}),
         "__file__": str(alt / "InitGui.py")}
    exec(compile(source, "InitGui.py", "exec"), g)  # noqa: S102

    assert g["_here"] == str(alt)
    sys.path.remove(str(alt))


def test_exec_missing_mod_dir_raises_clear_error(stub_freecad, tmp_path):
    fc, gui = stub_freecad
    fc.getUserAppDataDir = lambda: str(tmp_path / "user")
    fc.getHomePath = lambda: str(tmp_path / "home")
    (tmp_path / "user").mkdir()
    (tmp_path / "home").mkdir()

    with pytest.raises(RuntimeError, match="SonarCADLink"):
        _exec_initgui(fc)
