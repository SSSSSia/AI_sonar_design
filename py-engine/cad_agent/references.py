"""渐进式上下文片段（T8.2，迁移自 CadAgent agent/references.py 并按增量编辑场景改写）。

按 Agent 状态（首轮/质量失败/重复错误/迭代告急）由 loop 注入，替代每次发全量参考——
省 token 且聚焦注意力。每段控制在 ~300 token 内。
"""

#: 首轮注入——增量编辑最容易踩的坑（对象是既有模型，不是从零建模）
REF_FIRST_ITERATION = """
FIRST EDIT CHECKLIST:
- This is an EDIT round: `model` already holds the current solid (imported from STEP).
- Do NOT rebuild the whole part — modify/cut/union onto `model` and assign `result`.
- Do NOT import cadquery — `cq` and `math` are pre-injected.
- Boolean ops must physically overlap: extend one shape INTO the other by >= 0.5mm.
- End every tool call with: result = <single solid Shape>  (and cq_show(result, "Edit")).
"""

#: 质量门禁失败——按失败码给出针对性修复指引
REF_QUALITY_FAILURE = """
QUALITY REPAIR GUIDANCE (based on last failure):
- NO_SOLID: `result` must be a solid. Extract one: result = shape.Solids[0].
- MULTI_SOLID / COMPOUND_SHAPE: Fuse fragments into ONE solid (shapes must overlap
  by >= 0.5mm), then result = fused.Solids[0].
- INVALID_SHAPE: Boolean produced invalid geometry — change overlap or construction
  order, simplify.
- NEGATIVE_VOLUME: inside-out geometry; reverse cut order.
- DIMENSION_SUSPICIOUS: units are mm; check your numbers against the request.
"""

#: 重复错误——换思路策略
REF_REPAIR_LOOP = """
REPAIR STRATEGY — repeated errors, change your approach:
1. Find the ROOT CAUSE, not the symptom.
2. Change ONE thing at a time (overlap distance, boolean order, construction method).
3. If stuck 3+ times: revert to a simpler edit that still serves the user's intent.
"""

#: 接近迭代上限
REF_ITERATION_URGENCY = """
WARNING: approaching the tool-call limit ({max_iter} max, currently {current}).
Converge on a valid single-solid result NOW — simplify the edit if needed.
"""

#: 质量通过但有警告
REF_QUALITY_PASSED_WARN = """
Quality check PASSED with warnings. The result is a valid solid but may have minor
issues (see report). Fix them if quick, or summarize honestly what remains.
"""

# ---------------------------------------------------------------------------
# 修复指引映射：QualityIssue 码 → 针对性修复指令（迁移自 CadAgent，编辑场景措辞）
# loop._gate_block_message 按失败原因里的码注入。
# ---------------------------------------------------------------------------

QUALITY_FIX_MAP: dict[str, str] = {
    "NO_SOLID": (
        "`result` must be a single solid. Extract one from the compound: "
        "result = shape.Solids[0]. Ensure boolean operands are solids, not shells/wires."
    ),
    "MULTI_SOLID": (
        "Fragments must physically overlap by >= 0.5mm to fuse. Translate one shape "
        "INTO the other (e.g. .translate((0, 0, 2))), or use safe_fuse which enforces overlap."
    ),
    "COMPOUND_SHAPE": (
        "Extract the solid before finishing: result = shape.Solids[0]. "
        "Then run quality-relevant booleans on that solid."
    ),
    "INVALID_SHAPE": (
        "Boolean produced invalid geometry. Try: (1) increase overlap distance, "
        "(2) change boolean order, (3) simplify the edit into smaller steps."
    ),
    "NEGATIVE_VOLUME": (
        "Inside-out geometry. Reverse construction order or check that cut "
        "subtracts from the larger body."
    ),
    "DIMENSION_SUSPICIOUS": (
        "Units are mm. Verify the edit dimensions match the user request and "
        "the baseline geometry summary."
    ),
    "NO_DOCUMENT": (
        "No active model in the FreeCAD document — ask the user to reload the "
        "baseline version from the platform first."
    ),
}
