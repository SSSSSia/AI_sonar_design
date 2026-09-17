"""A3 CAD 建模 Agent（T1.5，IR2 → IR3）。

模块边界（宪法 §3.1 / A-08）：范例匹配 → 生成 CadQuery 脚本 → 静态校验 → 沙箱执行出 STEP；
**严格继承 IR2 参数值，不擅自改数**——发现矛盾抛错（阶段 2 升级为回环 A2）。

内脏迁移（ADR-001）：
- 参考范例双信号匹配（code_search ★ + codegen_stage 评分逻辑）
- 多层校验链 + sin² 自动修复（codegen_stage ★★）
- 执行 + 修复循环（executor_stage ★★；执行方式改子进程沙箱，宪法 §10.3）
- CADQUERY_SCRIPT_PROMPT / FIX_PROMPT（prompts.py ★）

A-13：修复循环上限 CAD_FIX_MAX_ROUNDS（可配），超限如实失败，绝不出假产物。
"""

from __future__ import annotations

import logging
from typing import Any

from agents.a3_cad.prompts import CADQUERY_SCRIPT_PROMPT, FIX_PROMPT
from cad_engine import sandbox, validators
from cad_engine.reference import CodeSearchService, strip_markdown
from ir_contract import validate_ir
from storage import object_store

logger = logging.getLogger("sonarcad.a3")


class A3Agent:
    def __init__(self, llm: Any = None, fix_max_rounds: int = 3, execute_timeout_s: int = 300):
        self._llm = llm
        self._fix_max_rounds = fix_max_rounds
        self._timeout_s = execute_timeout_s
        self._ref_service = CodeSearchService()

    # ------------------------------------------------------------------
    def run(self, session_id: str, ir2: dict[str, Any]) -> dict[str, Any]:
        """IR2 → IR3。产物落 MinIO（step-models / cad-scripts 分桶，object_key 按会话组织）。"""
        design_doc = ir2["design_document"]
        param_ids = {p["id"] for p in ir2.get("parameters", [])}
        if self._llm is None:
            raise RuntimeError("A3 依赖 LLM 生成 CadQuery 脚本，当前无可用供应商")

        # Step 1: 参考范例 + 代码生成
        ref_code, match_info = self._ref_service.select_reference(design_doc)
        logger.info("A3 参考范例: %s", (match_info or {}).get("filename", "无（默认）"))
        script = self._generate(design_doc, ref_code)

        # Step 2: 静态校验链（★★迁移；未过但代码非空不丢弃，交执行修复循环）
        script, static_ok, errors, warnings = validators.validate_chain(script)
        for w in warnings:
            logger.warning("A3 模式诊断: %s", w)
        if not script.strip():
            raise RuntimeError(f"CadQuery 代码生成为空: {'; '.join(errors)}")

        # Step 3: 沙箱执行 + 修复循环（A-13 有界）
        result = sandbox.run_in_subprocess(script, self._timeout_s)
        fix_round = 0
        while not result["ok"] and fix_round < self._fix_max_rounds:
            fix_round += 1
            logger.warning(
                "A3 执行失败（第 %s/%s 轮修复）: %s", fix_round, self._fix_max_rounds, result["error"]
            )
            script = self._fix(script, result["error"])
            script = validators.auto_fix_patterns(script)
            result = sandbox.run_in_subprocess(script, self._timeout_s)
        if not result["ok"]:
            raise RuntimeError(
                f"CadQuery 执行在 {self._fix_max_rounds} 轮修复后仍失败: {result['error']}"
            )

        # Step 4: 产物落 MinIO（D-10；IR 只存引用）
        step_file = result["step_files"][0]
        step_ref = object_store.upload_artifact(
            step_file, object_store.BUCKET_STEP_MODELS, f"{session_id}/sonar_model.step"
        )
        script_ref = object_store.upload_text(
            script, object_store.BUCKET_CAD_SCRIPTS, f"{session_id}/generated_script.py"
        )
        return self._build_ir3(script, step_ref, script_ref, param_ids, fix_round)

    # ------------------------------------------------------------------
    def _build_ir3(
        self,
        script: str,
        step_ref: dict[str, str],
        script_ref: dict[str, str],
        param_ids: set[str],
        fix_round: int,
    ) -> dict[str, Any]:
        """IR3 组装。features 与 IR2 参数镜像（E1 双向联动基础；A-08 继承的载体）。"""
        features = []
        for i, pid in enumerate(sorted(param_ids), 1):
            features.append({
                "feature_id": f"F-{i:03d}",
                "segment": "model",
                "geometry": {"linked_param": pid, "note": "尺寸由 IR2 参数驱动（E1 联动细化随阶段 2）"},
                "linked_param_id": pid,
            })
        ir3 = validate_ir("IR3", {
            "cadquery_script": script,
            "step_file": step_ref,
            "features": features,
        })
        # 执行元数据（修复轮数/脚本引用）只进日志，不进 IR3 契约（A-04：快照与 schema 严格一致）
        logger.info("A3 完成：fix_rounds=%s script_ref=%s", fix_round, script_ref)
        return ir3

    # ------------------------------------------------------------------
    def _generate(self, design_doc: str, ref_code: str) -> str:
        user_msg = f"根据以下设计文档生成完整的 CadQuery Python 脚本：\n\n{design_doc}"
        if ref_code:
            user_msg = (
                "以下是一份参考代码，展示了 CadQuery 建模的标准模式和 API 用法。\n"
                "你需要从中提取【建模模式】，然后用设计文档的参数独立生成代码。\n\n"
                "【必须复制的建模模式】（CadQuery API 调用方式）：\n"
                "- 柔性段：get_spline_pts() -> spline(pts) -> circle().sweep(path)\n"
                "- 空心管：outer_sweep.cut(inner_sweep)\n"
                "- 沿曲线放置元件：sin² 偏移 + atan(dxdz) 切线角 + rotate + translate\n"
                "- 对接件：sweep + intersect 截取两端\n"
                "- 变径件：分段 extrude + loft(combine=True)\n"
                "- 装配：z_cursor 递增 + cq.Assembly() + assy.add()\n"
                "- 坐标偏移：多区域布局通过 translate 实现空间隔离\n\n"
                "【必须替换的内容】（来自设计文档，不来自参考代码）：\n"
                "- 所有组件名称、数量、尺寸参数\n- 配色方案（Color 值）\n"
                "- 空间布局参数（坐标偏移量等）\n- 组件列表和装配顺序\n\n"
                f"```python\n{ref_code}\n```\n\n---\n\n" + user_msg
            )
        raw = self._llm.chat(
            [
                {"role": "system", "content": CADQUERY_SCRIPT_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=8192,
            temperature=0.2,
            timeout_s=900,
        )
        return strip_markdown(raw)

    def _fix(self, code: str, error_message: str) -> str:
        raw = self._llm.chat(
            [
                {"role": "system", "content": CADQUERY_SCRIPT_PROMPT},
                {"role": "user", "content": FIX_PROMPT % (error_message, code)},
            ],
            max_tokens=8192,
            temperature=0.2,
            timeout_s=900,
        )
        return strip_markdown(raw)
