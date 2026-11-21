# -*- coding: utf-8 -*-
"""插件测试套件 conftest（T8.8 新建，ADR-004 §5）。

把 SonarCADLink 包目录与 freecad-plugin 根注入 sys.path：
- `sonarlink.*` 绝对导入可在任何工作目录裸跑（根治 HANDOFF 记录的
  「裸跑须 PYTHONPATH=SonarCADLink」老坑）；
- `from tests.helpers import ...`（上游引进件）按普通包解析。
"""
from __future__ import annotations

import os
import sys

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SONARCADLINK = os.path.join(_PLUGIN_ROOT, "SonarCADLink")

for _p in (_SONARCADLINK, _PLUGIN_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
