# -*- coding: utf-8 -*-
"""参数化配方执行器（T19.1，PHASE-019，契约=ADR-019 决策 1）。

recipe = 带 PARAM_SCHEMA 的参数化构建模块（`build(params)`），领域约定固化在确定性代码里
而非 prompt 里——A3 命中 kind=recipe 条目时（T19.2 接线）走本模块执行：LLM 退场、几何
by construction、连通必检与沙箱/实测链路照跑（IR3 真实性契约不变）。

沙箱对齐：runner 尾段定义 `result` 变量即命中 cad_engine/sandbox.py 自动导出契约
（L63-77：非 Assembly 包成 Assembly save 为 sonar_model.step，auto_exported=True）；
拼接式单文件——沙箱单文件纪律不变（OCCT 镶嵌/构造不入本进程）。

缺参语义：check_coverage 返回人读缺失清单，绝不模糊匹配、绝不猜值（A-08/A-16）。
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

from cad_engine import quality, sandbox


def load_recipe(path) -> tuple[str, dict[str, Any], list[dict]]:
    """读配方文件 →（源码, PARAM_SCHEMA, SAMPLES）。

    进程内 exec 仅取模块常量与函数定义（import 开销除外，无 OCCT 构造——构造只发生在
    沙箱子进程）；缺 PARAM_SCHEMA.required 或 build() 即 ValueError（A-16 不造空契约）。
    """
    source = Path(path).read_text(encoding="utf-8")
    namespace: dict[str, Any] = {"__name__": "recipe_module"}
    exec(compile(source, str(path), "exec"), namespace)  # noqa: S102 - 受信配方（入库须人工 confirm，A-11）
    schema = namespace.get("PARAM_SCHEMA")
    if not isinstance(schema, dict) or not schema.get("required"):
        raise ValueError("配方缺少 PARAM_SCHEMA.required 声明（A-16 不造空契约）")
    if not callable(namespace.get("build")):
        raise ValueError("配方缺少 build(params) 函数")
    samples = namespace.get("SAMPLES")
    if samples is not None and not isinstance(samples, list):
        raise ValueError("配方 SAMPLES 必须为 list[dict]")
    return source, schema, list(samples or [])


def check_coverage(schema: dict[str, Any], ir2_params: list[dict] | None) -> tuple[dict[str, float], list[str]]:
    """IR2 参数 → 配方 schema 绑定（T19.2 A3 配方分支消费）。

    匹配：name_match==param.name（strip 精确）优先，id==param.id 兜底；每个 schema 条目
    消耗至多一个 IR2 参数（先命中先用）。required 缺失 → missing 人读清单；optional 缺失
    取声明 default；值一律 float 数值化，不可数值化=缺参如实。返回 (bound, missing)。"""
    params = list(ir2_params or [])
    used: set[int] = set()
    bound: dict[str, float] = {}
    missing: list[str] = []
    required_ids = {str(e.get("id") or "").strip() for e in schema.get("required") or []}
    entries = list(schema.get("required") or []) + list(schema.get("optional") or [])
    for e in entries:
        eid = str(e.get("id") or "").strip()
        nm = str(e.get("name_match") or "").strip()
        label = nm or eid or "?"
        hit: int | None = None
        value = None
        for i, p in enumerate(params):
            if i in used:
                continue
            if (nm and str(p.get("name") or "").strip() == nm) or (
                    eid and str(p.get("id") or "").strip() == eid):
                hit = i
                value = p.get("value")
                break
        if hit is None:
            if eid not in required_ids and e.get("default") is not None:
                bound[eid] = float(e["default"])  # optional 缺失取声明 default
                continue
            missing.append(f"{label}（{e.get('unit') or '?'}）")
            continue
        used.add(hit)
        try:
            bound[eid] = float(value)
        except (TypeError, ValueError):
            missing.append(f"{label}（值不可数值化: {value}）")
    return bound, missing


def build_runner_source(recipe_source: str, params: dict) -> str:
    """拼接式单文件：配方源码 + base64 传参尾段。

    base64 规避引号/反斜杠转义面（json 文本不经任何字符串字面量嵌套）；尾段 `result`
    变量=沙箱自动导出契约（sandbox.py L63-77），零新机制。"""
    payload = base64.b64encode(
        json.dumps(params, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).decode("ascii")
    runner = (
        "\n\n# === recipe runner（确定性执行；ADR-019 决策 1，T19.1）===\n"
        "import base64 as _base64\n"
        "import json as _json\n"
        f"_params = _json.loads(_base64.b64decode('{payload}').decode('utf-8'))\n"
        "result = build(_params)\n"
    )
    return recipe_source.rstrip() + "\n" + runner


def _check_bbox(bbox_mm, expected: dict | None, tol: float) -> tuple[bool, str]:
    """实测 extents（geometry_inspect 契约 bbox_mm=[dx,dy,dz]）与配方声明比对（±容差）。
    未声明 expected_bbox_mm → 跳过比对（如实标注，不判通过也不判失败）。"""
    if not (isinstance(bbox_mm, (list, tuple)) and len(bbox_mm) == 3):
        return False, f"bbox 实测形态异常: {bbox_mm}"
    if not expected:
        return True, "未声明 expected_bbox_mm（跳过比对）"
    got = {"x": float(bbox_mm[0]), "y": float(bbox_mm[1]), "z": float(bbox_mm[2])}
    details: list[str] = []
    ok = True
    for axis in ("x", "y", "z"):
        exp = expected.get(axis)
        if exp is None:
            continue
        good = abs(got[axis] - float(exp)) <= abs(float(exp)) * tol
        details.append(f"{axis}: exp={exp} got={round(got[axis], 3)} {'OK' if good else 'MISMATCH'}")
        ok = ok and good
    return ok, "; ".join(details)


def sample_validate(recipe_path, time_limit_s: int = 180,
                    samples: list[dict] | None = None) -> dict[str, Any]:
    """ADR-019 §4 判据 1 执行体：逐采样组「沙箱执行+连通必检+几何实测+bbox 声明比对」。

    每组 pass = sandbox_ok ∧ connected=True ∧ valid ∧ solids=声明值 ∧ bbox ±容差；
    未声明 SAMPLES → all_pass=False 如实（无可验证采样组不造通过）。
    T19.5 上传工装复用本函数做 recipe 验证起草。"""
    source, _schema, module_samples = load_recipe(recipe_path)
    use_samples = samples if samples is not None else module_samples
    if not use_samples:
        return {"recipe": str(recipe_path), "all_pass": False,
                "reason": "配方未声明 SAMPLES（无可验证采样组，不造通过）", "samples": []}
    out_samples: list[dict] = []
    for s in use_samples:
        name = str(s.get("name") or f"sample-{len(out_samples) + 1}")
        rec: dict[str, Any] = {"name": name}
        try:
            src = build_runner_source(source, dict(s.get("params") or {}))
        except Exception as exc:  # noqa: BLE001 - 采样组构造失败如实记录
            out_samples.append({**rec, "pass": False, "error": f"runner 构造失败: {exc}"})
            continue
        r = sandbox.run_in_subprocess(src, time_limit_s=time_limit_s)
        rec["sandbox_ok"] = bool(r.get("ok"))
        rec["auto_exported"] = bool(r.get("auto_exported"))
        if not r.get("ok") or not r.get("step_files"):
            rec["pass"] = False
            rec["error"] = str(r.get("error") or "未产出 STEP")[:200]
            out_samples.append(rec)
            continue
        rec["step_file"] = str(r["step_files"][0])
        conn = quality.check_connectivity(r["step_files"][0])
        rec["connectivity"] = {k: conn.get(k) for k in
                               ("solids", "valid", "components", "connected", "min_pair_overlap_ratio")}
        m = quality.measure_step(r["step_files"][0])
        rec["measure"] = {k: m.get(k) for k in ("overall_bbox_mm", "overall_volume_mm3", "part_count")}
        bbox_ok, bbox_detail = _check_bbox(
            m.get("overall_bbox_mm"), s.get("expected_bbox_mm"), float(s.get("tolerance", 0.01)))
        rec["bbox_check"] = {"pass": bbox_ok, "detail": bbox_detail}
        solids_ok = conn.get("solids") == int(s.get("expected_solids", 1))
        rec["pass"] = bool(conn.get("connected") is True and conn.get("valid")
                           and bbox_ok and solids_ok)
        if not solids_ok:
            rec["solids_check"] = {"pass": False,
                                   "expected": s.get("expected_solids", 1), "got": conn.get("solids")}
        out_samples.append(rec)
    return {
        "recipe": str(recipe_path),
        "all_pass": all(bool(s.get("pass")) for s in out_samples),
        "validated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "samples": out_samples,
    }
