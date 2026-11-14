"""A3 CAD 建模 Agent（T1.5，IR2 → IR3）。

模块边界（宪法 §3.1 / A-08）：范例匹配 → 生成 CadQuery 脚本 → 静态校验 → 沙箱执行出 STEP；
**严格继承 IR2 参数值，不擅自改数**——发现矛盾抛错（阶段 2 升级为回环 A2）。

内脏迁移（ADR-001）：
- 参考范例双信号匹配（code_search ★ + codegen_stage 评分逻辑）
- 多层校验链 + sin² 自动修复（codegen_stage ★★）
- 执行 + 修复循环（executor_stage ★★；执行方式改子进程沙箱，宪法 §10.3）
- 生成/修复提示词（prompts.py ★；T16.0 A0-5 起拆分通用/领域段，按匹配领域注入）

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
import time
import uuid
from pathlib import Path
from typing import Any

from agents.a3_cad.geometry_link import link_params
from agents.a3_cad.prompts import (
    DOMAIN_REF_PATTERNS,
    PROMPT_VERSION,
    build_fix_prompt,
    build_system_prompt,
)
from app_config import CAD_GEO_LINK_TOL_PCT, CAD_INSPECT_TIMEOUT_S, REF_CODE_DIR
from cad_engine import sandbox, validators
from cad_engine.reference import CodeSearchService, strip_markdown
from ir_contract import validate_ir
from storage import object_store

logger = logging.getLogger("sonarcad.a3")

GEOMETRY_INSPECT_CLI = Path(__file__).resolve().parents[2] / "cad_engine" / "geometry_inspect.py"


def _upload_artifacts(session_id: str, step_file: Path, script: str) -> tuple[dict[str, str], dict[str, str]]:
    """产物按次版本化落桶（T14.5，审查问题 4a）：{sid}/a3/{token}/ 前缀每次生成
    唯一——旧产物不被覆盖（A-12），引用附 sha256，历史 IR3 快照各自可复现。"""
    token = f"{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    step_ref = object_store.upload_artifact(
        step_file, object_store.BUCKET_STEP_MODELS,
        f"{session_id}/a3/{token}/sonar_model.step",
    )
    script_ref = object_store.upload_text(
        script, object_store.BUCKET_CAD_SCRIPTS,
        f"{session_id}/a3/{token}/generated_script.py",
    )
    return step_ref, script_ref


def _metrics():
    """指标模块（缺失时返回 None，打点调用静默跳过——指标故障不阻断建模主链路）。"""
    try:
        from observability import metrics

        return metrics
    except Exception:  # noqa: BLE001
        return None


class A3Agent:
    def __init__(self, llm: Any = None, fix_max_rounds: int = 3, execute_timeout_s: int = 300):
        self._llm = llm
        self._fix_max_rounds = fix_max_rounds
        self._timeout_s = execute_timeout_s
        self._ref_service = CodeSearchService(REF_CODE_DIR or None)

    # ------------------------------------------------------------------
    def run(self, session_id: str, ir2: dict[str, Any]) -> dict[str, Any]:
        """IR2 → IR3。产物落 MinIO（step-models / cad-scripts 分桶，object_key 按会话组织）。"""
        design_doc = ir2["design_document"]
        if self._llm is None:
            raise RuntimeError("A3 依赖 LLM 生成 CadQuery 脚本，当前无可用供应商")

        # Step 1: 参考范例 + 代码生成（L1 codegen 缓存，T9.6 键=IR2 参数指纹；T16.0 A0-3 起
        # 键另含生成配置指纹（范例/提示词/领域规则版本），值=JSON envelope 可追溯旧生成配置；
        # 沙箱执行/几何实测链路照跑，IR3 真实性不受缓存影响；修复循环 _fix 不查缓存）
        ref_code, match_info = self._ref_service.select_reference(design_doc)
        if match_info:
            logger.info(
                "A3 参考范例: %s (grade=%s score=%s)", match_info["file"], match_info["grade"], match_info["score"],
            )
        else:
            logger.info("A3 未使用参考范例（reason=%s，无领域范例模式）", self._ref_service.last_reason)
        # A0-5 领域策略边界：领域随 catalog 条目声明（domain 字段），未匹配=无领域规则
        domain: str | None = (match_info or {}).get("domain") or None
        from storage import cache

        _m = _metrics()
        t_gen = time.perf_counter()
        try:
            from llm.adapter import active_provider

            p = active_provider()
            provider_tag = f"{p.base_url}|{p.model}"
        except Exception:  # noqa: BLE001 - 无供应商（单测/降级）时退化指纹
            provider_tag = ""
        ref_fingerprint = f"{match_info['catalog_version']}:{match_info['sha256']}" if match_info else None
        cache_key = cache.codegen_cache_key(
            ir2, provider_tag, ref_fingerprint, PROMPT_VERSION, validators.DOMAIN_RULES_VERSION,
        )
        cached = cache.get_json("codegen", cache_key)
        if isinstance(cached, dict) and cached.get("script"):
            # 缓存命中可追溯旧生成配置（A0-3 验收）：来源以 envelope 内 gen_meta 为准，
            # 不归因到当前刚检索的候选
            gen_meta: dict[str, Any] = cached.get("gen_meta") or {}
            logger.info(
                "A3 codegen 缓存命中（IR2+生成配置指纹一致），旧生成配置: ref=%s prompt=v%s rules=v%s source=%s",
                (gen_meta.get("ref") or {}).get("file"), gen_meta.get("prompt_version"),
                gen_meta.get("rules_version"), gen_meta.get("generation_source"),
            )
            script = cached["script"]
        else:
            script = self._generate(design_doc, ref_code, domain)
            gen_meta = {
                "ref": match_info,
                "ref_reason": self._ref_service.last_reason,
                "generation_source": "fresh_llm",
                "domain": domain,
                "prompt_version": PROMPT_VERSION,
                "rules_version": validators.DOMAIN_RULES_VERSION,
                "provider_tag": provider_tag,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            if script.strip():
                cache.set_json(cache_key, {"script": script, "gen_meta": gen_meta}, cache.TTL_CODEGEN_S)
        if _m:
            _m.observe_a3_codegen(time.perf_counter() - t_gen)

        # Step 2: 静态校验链（★★迁移；未过但代码非空不丢弃，交执行修复循环；
        # A0-5：领域诊断/自动修复仅对声明领域执行，语法/完整性/配色修复恒执行）
        script, static_ok, errors, warnings = validators.validate_chain(script, domain=domain)
        for w in warnings:
            logger.warning("A3 模式诊断: %s", w)
        if not script.strip():
            raise RuntimeError(f"CadQuery 代码生成为空: {'; '.join(errors)}")

        # Step 3: 沙箱执行 + 修复循环（A-13 有界）
        t_sbx = time.perf_counter()
        result = sandbox.run_in_subprocess(script, self._timeout_s)
        if _m:
            _m.observe_a3_sandbox(time.perf_counter() - t_sbx, result["ok"])
        fix_round = 0
        while not result["ok"] and fix_round < self._fix_max_rounds:
            fix_round += 1
            logger.warning(
                "A3 执行失败（第 %s/%s 轮修复）: %s", fix_round, self._fix_max_rounds, result["error"]
            )
            script = self._fix(script, result["error"], domain)
            script = validators.auto_fix_patterns(script, domain)
            t_sbx = time.perf_counter()
            result = sandbox.run_in_subprocess(script, self._timeout_s)
            if _m:
                _m.observe_a3_sandbox(time.perf_counter() - t_sbx, result["ok"])
        if _m:
            _m.observe_fix_rounds(fix_round)
        if not result["ok"]:
            raise RuntimeError(
                f"CadQuery 执行在 {self._fix_max_rounds} 轮修复后仍失败: {result['error']}"
            )

        # Step 4: 产物落 MinIO（D-10；IR 只存引用）。T14.5（审查问题 4a）：路径按次
        # 版本化——历史产物不被后续生成覆盖（A-12），引用带 sha256 供独立下载核对；
        # 旧 IR3 快照引用各自的对象，任意历史版本可完整复现。
        step_file = result["step_files"][0]
        step_ref, script_ref = _upload_artifacts(session_id, step_file, script)
        return self._build_ir3(script, step_ref, script_ref, ir2, step_file, fix_round, gen_meta)

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
        _m = _metrics()
        t_ins = time.perf_counter()
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"geometry inspect timeout after {timeout}s"}
        except OSError as exc:
            return {"ok": False, "error": f"geometry inspect spawn failed: {exc}"}
        finally:
            if _m:
                _m.observe_geometry_inspect(time.perf_counter() - t_ins)
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
        gen_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """IR3 组装（T7.3 重写）：features 由 STEP 实测分段构造，长度类参数自动联动
        （geometry_link.link_params），未联动如实 null——不再按参数 id 批量造占位镜像
        （A-16）。提取失败/禁用降级 features=[] + geometry_summary 如实标注，管线继续。
        T16.0 A0-4：gen_meta（生成配置，含参考来源/缓存命中）组装为 reference_used 契约字段。
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
            "reference_used": self._reference_used_payload(gen_meta or {}),
        })
        # 来源信息 reference_used 自 T16.0 A0-4 起进 IR3 契约（溯源，加法演进旧快照可读）；
        # 其余执行元数据（修复轮数/脚本引用）仍只进日志，不进契约（A-04：快照与 schema 严格一致）
        logger.info(
            "A3 完成：fix_rounds=%s script_ref=%s features=%s geo=%s",
            fix_round, script_ref, len(features), summary.get("source"),
        )
        return ir3

    # ------------------------------------------------------------------
    @staticmethod
    def _reference_used_payload(gen_meta: dict[str, Any]) -> dict[str, Any]:
        """A0-4 来源契约（ir3.schema.json `reference_used`，加法演进旧快照可读）。

        字段对应脚本**实际生成所用**参考：缓存命中取 envelope 内旧生成配置
        （gen_meta.ref），不归因当前刚检索的候选（A0-3 验收）；未注入如实记录
        reason（A-14），无分数不造值（A-16）。
        """
        ref = gen_meta.get("ref") if isinstance(gen_meta, dict) else None
        if isinstance(ref, dict) and ref.get("file"):
            payload: dict[str, Any] = {
                "used": True,
                "ref_id": str(ref.get("ref_id", "")),
                "file": str(ref.get("file", "")),
                "sha256": str(ref.get("sha256", "")),
                "match_grade": str(ref.get("grade", "")),
                "generation_source": str(gen_meta.get("generation_source") or "fresh_llm"),
            }
            if ref.get("catalog_version"):
                payload["catalog_version"] = str(ref["catalog_version"])
            if ref.get("score") is not None:
                payload["score"] = float(ref["score"])
            if gen_meta.get("prompt_version"):
                payload["prompt_version"] = str(gen_meta["prompt_version"])
            if gen_meta.get("rules_version"):
                payload["rules_version"] = str(gen_meta["rules_version"])
            return payload
        payload = {"used": False}
        reason = gen_meta.get("ref_reason") if isinstance(gen_meta, dict) else None
        if reason:
            payload["reason"] = str(reason)
        return payload

    # ------------------------------------------------------------------
    def _generate(self, design_doc: str, ref_code: str, domain: str | None = None) -> str:
        """LLM 生成（A0-5：system prompt 与领域注入模板按匹配领域选择，未注册领域
        只有通用段——其他类型不误用拖曳阵强制建模模式）。"""
        user_msg = f"根据以下设计文档生成完整的 CadQuery Python 脚本：\n\n{design_doc}"
        if ref_code:
            # 领域注入模板（A0-5）：通用语句 +（仅注册领域）领域专属"必须复制"模式；
            # 无匹配领域时不注入任何领域模式（其他类型不误用拖曳阵强制模式）
            injection = (
                "以下是一份参考代码，展示了 CadQuery 建模的标准模式和 API 用法。\n"
                "你需要从中提取【建模模式】，然后用设计文档的参数独立生成代码。\n\n"
            )
            injection += DOMAIN_REF_PATTERNS.get(domain or "", "")
            injection += (
                "【必须替换的内容】（来自设计文档，不来自参考代码）：\n"
                "- 所有组件名称、数量、尺寸参数\n- 配色方案（Color 值）\n"
                "- 空间布局参数（坐标偏移量等）\n- 组件列表和装配顺序\n\n"
                f"```python\n{ref_code}\n```\n\n---\n\n"
            )
            user_msg = injection + user_msg
        raw = self._llm.chat(
            [
                {"role": "system", "content": build_system_prompt(domain)},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=8192,
            temperature=0.2,
            timeout_s=900,
        )
        return strip_markdown(raw)

    def _fix(self, code: str, error_message: str, domain: str | None = None) -> str:
        raw = self._llm.chat(
            [
                {"role": "system", "content": build_system_prompt(domain)},
                {"role": "user", "content": build_fix_prompt(domain) % (error_message, code)},
            ],
            max_tokens=8192,
            temperature=0.2,
            timeout_s=900,
        )
        return strip_markdown(raw)
