# -*- coding: utf-8 -*-
"""插件 import 图完整性测试（无 FreeCAD 环境）。

本机没有 FreeCAD/PySide，但包内模块顶层 import FreeCAD/Part/QtCore 等——
用最小桩模块替身后逐个导入 sonarlink 全部模块，抓 import 路径错改/
漏改（如包化重排后的残留平铺路径、迁移残留的旧包前缀）。

注意只能抓"import 期"错误；函数内局部 import 的坏路径由
test_platform_client 的协议链路 + README 真机清单兜底。
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
import types

import pytest


class _Signal:
    """Qt Signal 桩：类属性赋值/连接都不炸。"""

    def __init__(self, *a, **k):
        pass

    def __get__(self, obj, owner=None):
        return self

    def connect(self, *a, **k):
        pass

    emit = connect
    disconnect = connect


class _QThread:
    def __init__(self, parent=None):
        self._parent = parent


class _QDockWidget:
    def __init__(self, *a, **k):
        pass


def _module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


_STUBS = {
    "FreeCAD": lambda: _module("FreeCAD"),
    "FreeCADGui": lambda: _module("FreeCADGui"),
    "Part": lambda: _module("Part"),
    "Import": lambda: _module("Import"),
    # 插件经 PySide6 导入 Qt（FreeCAD 1.1 无裸名 QtCore/QtWidgets，真机实测）
    "PySide6": lambda: _module("PySide6",
                               QtCore=_module("QtCore", Signal=_Signal, QThread=_QThread,
                                              QTimer=_QThread, Qt=_module("Qt")),
                               QtGui=_module("QtGui"),
                               QtWidgets=_module("QtWidgets", QDockWidget=_QDockWidget,
                                                 QWidget=_QDockWidget)),
}


@pytest.fixture()
def fake_freecad_env():
    added = {}
    for name, factory in _STUBS.items():
        if name not in sys.modules:
            sys.modules[name] = factory()
            added[name] = True
    import sonarlink  # 包已在 sys.path（测试以 SonarCADLink 为 PYTHONPATH 运行）

    mods_before = {m for m in list(sys.modules) if m == "sonarlink" or m.startswith("sonarlink.")}
    yield
    # 卸载本次导入的 sonarlink 模块，避免桩环境泄漏到其他测试
    for m in list(sys.modules):
        if (m == "sonarlink" or m.startswith("sonarlink.")) and m not in mods_before:
            del sys.modules[m]
    for name in added:
        del sys.modules[name]


def test_all_sonarlink_modules_import(fake_freecad_env):
    import sonarlink

    names = [m.name for m in pkgutil.walk_packages(sonarlink.__path__, "sonarlink.")]
    assert names, "sonarlink 包为空？"
    for name in names:
        importlib.import_module(name)
