# SonarCADLink —— 智能辅助声呐装备设计系统 · FreeCAD 交互微调插件（PHASE-008 T8.4）
# 安装：把 SonarCADLink 目录复制进 FreeCAD 的 Mod 目录，重启 FreeCAD，
#       切到「SonarCADLink」工作台 → 点「面板」。详见仓库 clients/freecad-plugin/README.md。
#
# 注意：InitGui.py 被 FreeCAD 以 exec() 方式加载——顶层名在方法体内不可见，
# 方法体内一律局部 import（CadAgent 同款经验）；且多数版本不注入 __file__
# （真机报错 "name '__file__' is not defined"），目录定位须有 __file__ 之外的回退。

import os
import sys

try:
    _here = os.path.dirname(os.path.abspath(__file__))  # noqa: F821 - 新版 FreeCAD 会注入
except NameError:
    _here = ""
if not _here or not os.path.isfile(os.path.join(_here, "InitGui.py")):
    # CadAgent 同款定位：用户目录 Mod 优先，回退 FreeCAD 安装目录 Mod
    import FreeCAD  # noqa: F821 - exec 环境由 FreeCAD 注入，冗余 import 仅为静态检查

    _here = ""
    for _base in (FreeCAD.getUserAppDataDir(), FreeCAD.getHomePath()):  # noqa: F821
        _cand = os.path.join(_base, "Mod", "SonarCADLink")
        if os.path.isfile(os.path.join(_cand, "InitGui.py")):
            _here = _cand
            break
if not _here:
    raise RuntimeError(
        "SonarCADLink：无法定位插件目录（Mod/SonarCADLink）。"
        "请确认整个 SonarCADLink 目录已完整复制进 FreeCAD 的 Mod 目录后重启 FreeCAD。"
    )
if _here not in sys.path:
    sys.path.insert(0, _here)
FreeCAD._sonarlink_dir = _here  # noqa: F821 - FreeCAD 由加载环境注入


class SonarCADLinkWorkbench(Workbench):  # noqa: F821
    """平台微调工作台：聊天面板入口。"""

    Icon = None
    MenuText = "SonarCADLink"
    ToolTip = "智能声呐设计系统：平台生成模型的交互微调"

    def Initialize(self):
        # 注意不可 `import InitGui` 自引：那会把本文件作为普通模块重新 exec 一遍，
        # 导致命令/工作台重复注册（FreeCAD exec 加载不会在 sys.modules 留名）。

        from PySide import QtCore  # noqa: F401

        self.appendToolbar("SonarCADLink", ["SonarCADLink_ShowPanel"])
        self.appendMenu("SonarCADLink", ["SonarCADLink_ShowPanel"])

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def ContextName(self):
        return "SonarCADLink"


class _ShowPanelCmd:
    def GetResources(self):
        return {
            "MenuText": "打开微调面板",
            "ToolTip": "打开 SonarCADLink 平台微调面板（登录 → 打开设计会话 → 对话微调）",
        }

    def Activated(self):
        from sonarlink.ui import panel  # 局部 import：exec 加载环境下包在 sys.path 上

        panel.show_panel()

    def IsActive(self):
        return True


FreeCADGui.addCommand("SonarCADLink_ShowPanel", _ShowPanelCmd())  # noqa: F821
FreeCADGui.addWorkbench(SonarCADLinkWorkbench())  # noqa: F821
