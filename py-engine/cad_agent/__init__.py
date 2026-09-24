"""CAD 交互微调服务端大脑（T8.2，PHASE-008，ADR-004）。

自研 CadAgent 插件（D:\\Download\\FreeCAD\\Mod\\CadAgent，只读参考）的 AgentLoop
改造迁移：循环驱动权外移到服务端，工具执行经 tune_store 协议外包 FreeCAD 插件。

LLM 交互 = ReAct 文本协议（<tool name="...">...</tool>）：不改共享 LlmAdapter
（A1~A5 同源）、供应商无关（原生 tool calling 各家支持参差，CadAgent 实测后回退先例）。
"""

from cad_agent.loop import RunResult, TuneRoundRunner
from cad_agent.prompts import TUNE_SYSTEM_PROMPT, build_round_context

__all__ = ["RunResult", "TuneRoundRunner", "TUNE_SYSTEM_PROMPT", "build_round_context"]
