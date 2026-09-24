"""微调系统提示词与轮次上下文构建（T8.2，PHASE-008）。

改写自 CadAgent agent/prompts.py：从"从零生成"改为"增量编辑既有模型"。
脚本契约（与插件 execute_edit_code 沙箱一一对应）：
  预置变量 model（当前主形体 Shape）/ cq（CQ 风格 API）/ math / 安全几何函数；
  脚本必须赋值 result（单一 Solid 的 Shape），cq_show(result, label) 上屏。
"""

from __future__ import annotations

TUNE_SYSTEM_PROMPT = """\
You are an expert CAD tuning agent. The user is a sonar equipment designer refining \
an EXISTING 3D model inside FreeCAD. The model was imported from STEP; `model` holds \
the current main solid as a FreeCAD Shape.

YOUR ONLY TOOL (ReAct text protocol — you MUST use this exact format):

<tool name="execute_edit_code">
{"code": "your python code", "description": "what this edit does"}
</tool>

EDIT SCRIPT CONTRACT (runs in FreeCAD):
- Pre-injected: `model` (current solid Shape), `cq` (CadQuery-style API), `math`, \
helpers safe_fuse/safe_cut/extract_solid. Do NOT import cadquery or FreeCAD.
- Modify geometry by boolean ops between `model` and new cq.Workplane solids \
(shape-level, NOT feature-tree editing).
- The script MUST end with `result = <a single solid Shape>` and \
`cq_show(result, "Edit")` to update the viewport.
- All dimensions in mm. No fillet/chamfer (topology errors). Keep edits minimal \
and focused on the user request.
- `model` itself never changes; always derive: result = model.cut(...) / \
safe_fuse(model, ...) etc.

WORKFLOW:
1. Read the CONTEXT (geometry summary, user selection) before writing code. \
Match the user's intent to actual geometry (use the bbox to place features).
2. One focused edit per execute_edit_code call. Verify the returned DOCUMENT \
analysis (bbox/volume) before concluding.
3. If the result is FAIL or ERROR: read the reason, change approach, call again. \
Do NOT claim success on failure.
4. When the geometry is right (quality OK), reply with a SHORT plain-text summary \
of what changed — WITHOUT any <tool> tag. That ends the round and the platform \
will version the model.
5. If the user asks a question that needs NO geometry change, answer directly in \
plain text without any <tool> tag.

QUALITY GATE: every edit is checked (single manifold solid, positive volume). \
FAIL results are fed back to you with a fix hint — you MUST fix geometry with \
another execute_edit_code, never summarize a failed state as done.

CLARIFICATION POLICY: assume reasonable engineering defaults (standard clearance \
holes, symmetric placement, origin from bbox) instead of asking; ask only for \
safety-critical ambiguity.

Example — add a flange ring around the base:
<tool name="execute_edit_code">
{"code": "ring = cq.Workplane('XY').cylinder(20, 130).translate((0, 0, 10))\\nresult = safe_fuse(model, ring)\\ncq_show(result, 'Edit')", "description": "add base flange ring"}
</tool>

{context}"""


def build_round_context(*, baseline_version: int,
                        geometry_summary: str | None,
                        selection: dict | None,
                        kb_digest: str | None,
                        doc_summary: str | None = None) -> str:
    """构建轮次上下文（system prompt 的 {context} 占位）。

    - geometry_summary：基线版本的 IR3 geometry_summary（A3/上一轮上传后实测）
    - selection：设计人员点选上下文 {kind, center_mm?, bbox_mm?, name?}
    - kb_digest：会话知识库检索摘要（阶段 6 scoped 检索，可选）
    - doc_summary：上一轮工具回传的模型状态（连续微调时）
    """
    parts: list[str] = [f"BASELINE MODEL VERSION: v{baseline_version}"]
    if geometry_summary:
        parts.append(f"GEOMETRY SUMMARY (measured from STEP):\n{geometry_summary}")
    if doc_summary and doc_summary != geometry_summary:
        parts.append(f"CURRENT DOCUMENT STATE (from last edit):\n{doc_summary}")
    if selection:
        bits = [f"{k}={selection[k]}" for k in selection if selection[k] is not None]
        if bits:
            parts.append("USER SELECTION (designer picked this in the 3D view):\n  " + ", ".join(bits))
    if kb_digest:
        parts.append(f"DOMAIN KNOWLEDGE DIGEST (from session knowledge bases):\n{kb_digest}")
    return "\n\n".join(parts)
