# SonarCADLink —— 智能辅助声呐装备设计系统 · FreeCAD 交互微调插件（PHASE-008 T8.4）
# 安装：把 SonarCADLink 目录复制进 FreeCAD 的 Mod 目录，重启 FreeCAD，
#       切到「SonarCADLink」工作台 → 点「面板」。详见仓库 clients/freecad-plugin/README.md。
#
# 注意：InitGui.py 被 FreeCAD 以 exec() 方式加载——顶层名在方法体内不可见，
# 目录须在顶层捕获并挂到 FreeCAD 上，方法体内一律局部 import（CadAgent 同款经验）。

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)
FreeCAD._sonarlink_dir = _here  # noqa: F821 - FreeCAD 由加载环境注入


class SonarCADLinkWorkbench(Workbench):  # noqa: F821
    """平台微调工作台：聊天面板入口。"""

    Icon = None
    MenuText = "SonarCADLink"
    ToolTip = "智能声呐设计系统：平台生成模型的交互微调"

    def Initialize(self):
        import InitGui  # noqa: F401 - 确保 sys.path 就绪

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
        import panel  # 局部 import：exec 加载环境下模块在 sys.path 上

        panel.show_panel()

    def IsActive(self):
        return True


FreeCADGui.addCommand("SonarCADLink_ShowPanel", _ShowPanelCmd())  # noqa: F821
FreeCADGui.addWorkbench(SonarCADLinkWorkbench())  # noqa: F821
