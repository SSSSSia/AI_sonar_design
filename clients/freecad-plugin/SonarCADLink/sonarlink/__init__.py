# -*- coding: utf-8 -*-
"""SonarCADLink 插件包（后端大脑模式的 FreeCAD 微调客户端）。

子包：client=平台 HTTP 客户端；cad=FreeCAD 内执行端（CQ 翻译/质量/沙箱）；
ui=聊天面板。包化原因：FreeCAD 把 Mod 目录放进 sys.path，平铺顶层模块名
（theme/executor 等）易与其他 Mod 撞名，且目录结构可读性差。
"""
