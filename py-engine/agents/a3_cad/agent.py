"""A3 CAD 建模 Agent（T1.5，IR2 → IR3）。

模块边界（宪法 §3.1 / A-08）：范例匹配 → 生成 CadQuery 脚本 → 静态校验 → 沙箱执行出 STEP；
**严格继承 IR2 参数值，不擅自改数**——发现矛盾抛错（阶段 2 升级为回环 A2）。

内脏迁移（ADR-001）：
- 参考范例双信号匹配（code_search ★ + codegen_stage 评分逻辑）
- 多层校验链 + sin² 自动修复（codegen_stage ★★）
- 执行 + 修复循环（executor_stage ★★；执行方式改子进程沙箱，宪法 §10.3）
- CADQUERY_SCRIPT_PROMPT / FIX_PROMPT（prompts.py ★）

A-13：修复循环上限 CAD_FIX_MAX_ROUNDS（可配），超限如实失败，绝不出假产物。
T7.3（PHASE-007）：沙箱成功后以 subprocess 调 cad_engine/geometry_inspect.py 提取实测
几何（OCCT 镶嵌禁止进入本进程），features 由真实分段构造替代占位镜像；提取失败/禁用
降级为 features=[] + geometry_summary 如实标注，管线继续（A-14/A-16）。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from agents.a3_cad.geometry_link import link_params
from agents.a3_cad.prompts import CADQUERY_SCRIPT_PROMPT, FIX_PROMPT
from app_config import CAD_GEO_LINK_TOL_PCT, CAD_INSPECT_TIMEOUT_S
from cad_engine import sandbox, validators
from cad_engine.reference import CodeSearchService, strip_markdown
from ir_contract import validate_ir
from storage import object_store

logger = logging.getLogger("sonarcad.a3")

GEOMETRY_INSPECT_CLI = Path(__file__).resolve().parents[2] / "cad_engine" / "geometry_inspect.py"


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
        if self._llm is None:
            raise RuntimeError("A3 依赖 LLM 生成 CadQuery 脚本，当前无可用供应商")

        # Step 1: 参考范例 + 代码生成（L1 codegen 缓存，T9.6：键=IR2 参数指纹，TTL 30d；
        # 沙箱执行/几何实测链路照跑，IR3 真实性不受缓存影响；修复循环 _fix 不查缓存）
        ref_code, match_info = self._ref_service.select_reference(design_doc)
        logger.info("A3 参考范例: %s", (match_info or {}).get("filename", "无（默认）"))
        from storage import cache

        cache_key = cache.codegen_cache_key(ir2)
        cached_script = cache.get_str("codegen", cache_key)
        if cached_script:
            logger.info("A3 codegen 缓存命中（IR2 参数指纹一致），跳过 LLM 生成")
            script = cached_script
        else:
            script = self._generate(design_doc, ref_code)
            if script.strip():
                cache.set_str(cache_key, script, cache.TTL_CODEGEN_S)

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
        return self._build_ir3(script, step_ref, script_ref, ir2, step_file, fix_round)

    # ------------------------------------------------------------------
    def _inspect_step(self, step_local_path: str) -> dict[str, Any] | None:
        """subprocess 调 geometry_inspect.py 提取实测几何（OCCT 镶嵌禁止进入 agent 进程）。

        返回 None = 提取被禁用（CAD_INSPECT_TIMEOUT_S=0）；失败/超时返回 {"ok": False,
        "error": ...}（A-16 如实），绝不抛异常、绝不阻断管线。
        """
        timeout = CAD_INSPECT_TIMEOUT_S
        if timeout <= 0 or not step_local_path:
            return None
        out_path = f"{step_local_path}.inspect.json"
        cmd = [sys.executable, str(GEOMETRY_INSPECT_CLI), str(step_local_path), out_path]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"geometry inspect timeout after {timeout}s"}
        except OSError as exc:
            return {"ok": False, "error": f"geometry inspect spawn failed: {exc}"}
        try:
            result = json.loads(Path(out_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": f"geometry inspect output unreadable: {exc}"}
        finally:
            try:
                os.remove(out_path)
            except OSError:
                pass
        return result

    # ------------------------------------------------------------------
    def _build_ir3(
        self,
        script: str,
        step_ref: dict[str, str],
        script_ref: dict[str, str],
        ir2: dict[str, Any],
        step_local_path: str,
        fix_round: int,
    ) -> dict[str, Any]:
        """IR3 组装（T7.3 重写）：features 由 STEP 实测分段构造，长度类参数自动联动
        （geometry_link.link_params），未联动如实 null——不再按参数 id 批量造占位镜像
        （A-16）。提取失败/禁用降级 features=[] + geometry_summary 如实标注，管线继续。
        """
        params: list[dict[str, Any]] = ir2.get("parameters", [])
        inspect = self._inspect_step(step_local_path)

        features: list[dict[str, Any]] = []
        summary: dict[str, Any]
        if inspect is None:
            summary = {
                "source": "extraction_failed",
                "detail": "跳过：几何提取已禁用（CAD_INSPECT_TIMEOUT_S=0）",
            }
        elif not inspect.get("ok"):
            detail = str(inspect.get("error") or "unknown error")[:500]
            logger.warning("A3 几何提取失败（降级继续）: %s", detail)
            summary = {"source": "extraction_failed", "detail": detail}
        else:
            parts: list[dict[str, Any]] = inspect.get("parts", [])
            links = link_params(params, parts, CAD_GEO_LINK_TOL_PCT)
            for part in parts:
                geometry: dict[str, Any] = {
                    "bbox_mm": part["bbox_mm"],
                    "volume_mm3": part["volume_mm3"],
                    "center_mm": part["center_mm"],
                }
                linked = links.get(part["index"])
                if linked:
                    geometry["linked_dim"] = linked["dim"]
                features.append({
                    "feature_id": f"F-{part['index']:03d}",
                    "segment": f"SEG-{part['index']:02d}",
                    "geometry": geometry,
                    "linked_param_id": linked["param_id"] if linked else None,
                })
            overall = inspect.get("overall", {})
            meta = inspect.get("meta", {})
            summary = {
                "source": "extracted",
                "bbox_mm": overall.get("bbox_mm"),
                "volume_mm3": overall.get("volume_mm3"),
                "part_count": overall.get("part_count"),
                "detail": f"{meta.get('segmentation', '?')} · {meta.get('extractor', '?')}",
            }

        ir3 = validate_ir("IR3", {
            "cadquery_script": script,
            "step_file": step_ref,
            "features": features,
            "geometry_summary": summary,
        })
        # 执行元数据（修复轮数/脚本引用）只进日志，不进 IR3 契约（A-04：快照与 schema 严格一致）
        logger.info(
            "A3 完成：fix_rounds=%s script_ref=%s features=%s geo=%s",
            fix_round, script_ref, len(features), summary.get("source"),
        )
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
