"""CAD 执行底座：CadQuery（OCP/OpenCascade）隔离沙箱执行（D-13、A-15）。

安全约束（宪法 §10.3）：生成脚本只在子进程 + 独立工作目录执行，限制执行时间；
禁止在主进程 import 用户生成代码。重试次数上限可配（A-13）。
阶段 1 T1.5 从旧仓库 stages/executor_stage.py 迁移（★★ 内脏复用、外壳重写）。
"""
