"""A4 合规校验 Agent（T2.1，IR2 + IR3 → IR4）。

模块边界（宪法 §3.1 / A-09）：**只出报告不改参数**；不通过由编排层触发局部回环（A-11）。
**纯确定性实现，不调用 LLM**——合规校验必须可审计、可复现（A-07 精神；llm 参数仅为与其他
Agent 管线签名一致而保留，未使用）。

按 source_type 分流校验 + 几何自洽：
  formula  → solver.recompute：SymPy 精确代入复算（T14.7 取数源：优先 IR2 参数快照
             按符号别名解析+单位换算，derivation 文本解析降为兜底——推导只承担展示）
  case     → solver.intervals：候选池区间传播 + 同符号候选冲突检测
  standard → solver.standards：军标核对（空库如实"待补"，A-16）
  estimated → 汇总 info：未经公式验证，请人工复核
  几何自洽  → features.linked_param_id ⊆ IR2 参数 id / feature_id 唯一 / step·script 引用完整
  数值复核  → linked 参数 vs 实测 bbox 维度（T7.4，PHASE-007；容差可配默认 2%，无 LLM）
  显示变换  → IR3 display_transforms 逐条 info 呈现（T15.1，ADR-014 §3；物理/显示分离
             显性化，禁止静默缩小工程参数；声明引用未知参数 → warning）
  映射状态  → IR3 param_mapping 中 ambiguous/unmatched 对象的参数 → CHK-SEM-MAPPING
             unverified（T15.2，ADR-014 §2；应检未检显性，verified 对象由语义检查器
             在 semantics.py 按尺寸语义核对——T15.3）
  尺寸语义  → verified 映射对象的声明参数过 semantics 注册表（T15.3，ADR-014 §4）：
             CHK-SEM-LENGTH / CHK-SEM-DIAMETER（violation→error+back_to_A3，pass→info，
             无测量/无检查器→unverified）；被映射覆盖参数从 legacy 数值复核排除

三级风险：error=高（须回环）/ warning=中（来源冲突存疑）/ info=低（提示）。
overall：存在任一 error 即 fail。输出经 contracts/ir/ir4.schema.json 契约校验（A-04）。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from agents.a3_cad.geometry_link import UNIT_TO_MM
from agents.a4_validation import semantics
from app_config import CAD_GEO_NUM_TOL_PCT, SEMANTIC_TOL_PCT
from ir_contract import validate_ir
from solver import deps, intervals, recompute, standards
from solver.formulas import REGISTRY, _first_number
from solver.recompute import ABS_TOL

logger = logging.getLogger("sonarcad.a4")


class A4Agent:
    """A4 合规校验。不依赖 LLM 与网络（standards/neo4j 之外的纯内存计算）。"""

    def __init__(self, llm: Any = None):  # noqa: ARG002 - 管线签名一致性，A4 确定性不用 LLM
        self._llm = llm

    # ------------------------------------------------------------------
    def run(self, ir2: dict[str, Any], ir3: dict[str, Any] | None = None) -> dict[str, Any]:
        params: list[dict[str, Any]] = ir2.get("parameters", [])
        features: list[dict[str, Any]] = (ir3 or {}).get("features", [])

        # CSR 统计器（T9.2，PHASE-009）：口径写死于本注释与 PHASE-009 §2.2，前端 tooltip 同文案——
        # CSR = 通过的约束检查数 / (通过 + 违反) × 100。
        # 计数对象 = 产出确定性判定的检查：formula 复算、standard 符合性核对、几何数值复核（CHK-GEO-NUM）；
        # warning（来源冲突存疑）与 info（提示/待补/跳过）不计入分母——存疑不等于违反，避免虚低；
        # 结构完整性错误（引用悬空/特征重复/产物缺失）属产物完整性而非约束满足，由 overall=fail 体现，不计入 CSR；
        # 无任何有效检查（全 skip / 空方案）→ csr=None（A-16 无数据不造假，前端显示「—」）。
        stats: dict[str, int] = {"pass": 0, "violation": 0}
        # 覆盖率统计器（T14.4，审查问题 2d）：applicable=适用必检项（formula 参数复算 +
        # 适用军标 expr 条目 + 已联动几何期望比对；已登记无表达式条目不计入），checked=已产出
        # 确定性判定的项数。与 CSR 分母口径不同：未验证项计入 coverage 分母、不计入 CSR。
        cov: dict[str, int] = {"applicable": 0, "checked": 0}

        issues: list[dict[str, Any]] = []
        issues += self._check_formula(params, stats, cov)
        issues += self._check_case(params)
        issues += self._check_standard(params, stats, cov)
        issues += self._check_estimated(params)
        issues += self._check_geometry(ir2, ir3 or {}, stats, cov)
        issues += self._check_semantic(ir2, ir3 or {}, stats, cov)
        issues += self._check_display_transform(ir2, ir3 or {})
        issues += self._check_semantic_mapping(ir2, ir3 or {})
        issues += self._check_document(ir2, params)

        total = stats["pass"] + stats["violation"]
        csr = round(stats["pass"] / total * 100, 2) if total > 0 else None
        coverage = None
        if cov["applicable"] > 0:
            coverage = {
                "checked": cov["checked"],
                "applicable": cov["applicable"],
                "ratio": round(cov["checked"] / cov["applicable"] * 100, 2),
            }
        # overall 三态（T14.4，审查问题 2c）：error → fail；无 error 但应检未检
        # （unverified 级问题存在）→ incomplete——不允许"部分未验证"伪装成完整通过；
        # warning/info 不影响总体结论（与 v0.6 前一致）。
        has_unverified = any(i["severity"] == "unverified" for i in issues)
        overall = (
            "fail" if any(i["severity"] == "error" for i in issues)
            else "incomplete" if has_unverified
            else "pass"
        )
        ir4 = {
            "overall": overall,
            "issues": issues,
            "csr": csr,
            "coverage": coverage,
        }
        return validate_ir("IR4", ir4)

    # ------------------------------------------------------------------
    def _check_formula(self, params: list[dict[str, Any]], stats: dict[str, int],
                       cov: dict[str, int]) -> list[dict[str, Any]]:
        """formula 参数：SymPy 复算。定位顺序：solver REGISTRY → kb_formulas 业务库（T3.3）。
        无法定位/缺代入值/输入不全 → unverified（T14.4 升级，原 info）——应检未检，
        驱动 overall=incomplete，不再以提示性 info 混入"通过"。"""
        issues: list[dict[str, Any]] = []
        for p in params:
            if p.get("source_type") != "formula":
                continue
            cov["applicable"] += 1  # formula 参数复算 = 必检项
            key = recompute.resolve_formula_key(p.get("source_ref", ""))
            db_row = None
            if key is None:
                db_row = self._match_db_formula(p)
                if db_row is None:
                    issues.append(_issue(
                        "CHK-FORMULA-RECOMPUTE", "unverified",
                        f"formula 参数「{p.get('name')}」无法定位注册公式"
                        f"（source_ref={p.get('source_ref')}），未复算，请人工复核",
                        [p["id"]]))
                    continue
            # T14.7 取数源切换（审查问题 8）：输入优先取 IR2 参数快照（按符号别名解析、
            # 单位换算），推导文本解析降为兜底——推导文本只承担展示，不再当事实存储；
            # 快照输入与登记值不一致会以 error 暴露（过期公式参数不再被旧 derivation 掩护）。
            spec = REGISTRY.get(key) if key is not None else None
            snap_inputs = deps.snapshot_inputs(params, p, spec) if spec is not None else None
            if snap_inputs is not None:
                inputs = snap_inputs
                source_note = "（输入取自参数快照）"
            else:
                inputs = recompute.parse_inputs_from_derivation(p.get("derivation", ""))
                source_note = ""
            if not inputs:
                issues.append(_issue(
                    "CHK-FORMULA-RECOMPUTE", "unverified",
                    f"formula 参数「{p.get('name')}」无复算输入（快照无上游符号映射且 "
                    "derivation 未记录代入值），无法复算，请人工复核",
                    [p["id"]]))
                continue
            try:
                if key is not None:
                    result = recompute.recompute(key, inputs)
                else:
                    symbols = db_row.get("symbols") or []
                    result = recompute.recompute_dynamic(
                        expr=db_row["expr"], symbols=symbols, defaults={},
                        inputs=inputs, formula_name=db_row["name"], unit=db_row.get("unit", "-"),
                        formula_key=f"kb_formulas:{db_row['key']}",
                    )
            except KeyError as exc:
                issues.append(_issue(
                    "CHK-FORMULA-RECOMPUTE", "unverified",
                    f"formula 参数「{p.get('name')}」复算输入不全（{exc}），请人工复核",
                    [p["id"]]))
                continue
            cov["checked"] += 1  # 复算已产出确定性判定
            value = _first_number(p.get("value"))
            if value is None:
                stats["violation"] += 1
                issues.append(_issue(
                    "CHK-FORMULA-RECOMPUTE", "error",
                    f"formula 参数「{p.get('name')}」取值缺失或非数值，无法与复算结果比对",
                    [p["id"]], suggestion="back_to_A2"))
            elif not recompute.values_match(result.value, value):
                stats["violation"] += 1
                issues.append(_issue(
                    "CHK-FORMULA-RECOMPUTE", "error",
                    f"复算不符{source_note}：参数「{p.get('name')}」登记值 {p.get('value')} {p.get('unit')}，"
                    f"{result.derivation}（依据 {p.get('source_ref')}）",
                    [p["id"]], suggestion="back_to_A2"))
            else:
                stats["pass"] += 1  # 复算一致：静默通过（不产 issue），计入 CSR
        return issues

    @staticmethod
    def _match_db_formula(p: dict[str, Any]) -> dict[str, Any] | None:
        """业务库 kb_formulas 定位（T3.3）：key 出现在 source_ref 或名称一致；库不可达→None。"""
        try:
            from storage import registry_store

            for row in registry_store.list_rows_cached("formulas"):
                if row.get("key") and str(row["key"]) in str(p.get("source_ref") or ""):
                    return row
                if row.get("name") and str(row["name"]) == str(p.get("name")):
                    return row
        except Exception:
            return None
        return None

    # ------------------------------------------------------------------
    def _check_case(self, params: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """case 参数：区间传播（采纳值与案例候选总体相容性）+ 候选冲突检测。"""
        issues: list[dict[str, Any]] = []
        pools = intervals.candidate_pools(params)
        formula_by_key: dict[str, list[dict[str, Any]]] = {}
        for p in params:
            if p.get("source_type") != "formula":
                continue
            key = recompute.resolve_formula_key(p.get("source_ref", ""))
            if key:
                formula_by_key.setdefault(key, []).append(p)

        for key in REGISTRY:
            interval = intervals.propagate_interval(key, pools)
            if interval is None:
                continue
            low, high = interval
            for p in formula_by_key.get(key, []):
                value = _first_number(p.get("value"))
                if value is None:
                    continue
                eps = 1e-6 * max(1.0, abs(value))
                if value < low - eps or value > high + eps:
                    issues.append(_issue(
                        "CHK-CASE-INTERVAL", "warning",
                        f"参数「{p.get('name')}」采纳值 {value} 落在案例候选传播区间 "
                        f"[{low:.6g}, {high:.6g}] 之外（{p.get('source_ref')}）——"
                        "与其他案例来源不一致，采纳值可能掩盖了候选间的取舍",
                        [p["id"]]))

        for c in intervals.case_conflicts(pools):
            issues.append(_issue(
                "CHK-CASE-CONFLICT", "warning",
                f"符号「{c['symbol']}」存在相互冲突的案例候选：{c['values']}"
                f"（参数 {', '.join(c['param_ids'])}），请人工裁决取舍",
                c["param_ids"]))
        return issues

    # ------------------------------------------------------------------
    def _check_standard(self, params: list[dict[str, Any]], stats: dict[str, int],
                        cov: dict[str, int]) -> list[dict[str, Any]]:
        """军标核对（T9.3 expr 求值；T14.4 来源解耦，审查问题 2a）：

        - 适用性按规则 applies_to 对**全部参数**核对（不再按 source_type=standard
          分流）——case/estimated 来源的参数同样受军标约束，来源仅用于追溯展示；
        - coverage：适用 expr 条目计入 applicable，pass/violation 计入 checked
          （uncovered=应检未检；无表达式条目不计入分母，出 info 提示性核对）；
        - A-16 空库/未覆盖如实待补——standard 来源参数无适用条目出 unverified
          （军标核对是它的应尽义务），非 standard 参数无适用条目不产 issue。
        """
        issues: list[dict[str, Any]] = []
        for p in params:
            result = standards.check_param(p, all_params=params)
            entries = result.get("entries") or []
            cov["applicable"] += sum(1 for e in entries if e.get("has_expr"))
            cov["checked"] += sum(1 for e in entries
                                  if e.get("has_expr") and e.get("verdict") in ("pass", "violation"))
            if result["status"] == "violation":
                stats["violation"] += 1
                issues.append(_issue(
                    "CHK-STD-VIOLATION", "error",
                    f"军标违例：{result['message']}", [p["id"]], suggestion="back_to_A2"))
            elif result["status"] == "pass":
                stats["pass"] += 1  # 军标核对通过：静默（不产 issue），计入 CSR
            elif result["status"] == "uncovered":
                # 条目引用符号在 IR2 无对应值：应检未检（T14.4 升级，原 info）
                issues.append(_issue(
                    "CHK-STD-UNCOVERED", "unverified",
                    f"参数「{p.get('name')}」：{result['message']}", [p["id"]]))
            elif result["status"] == "pending":
                if p.get("source_type") == "standard":
                    issues.append(_issue(
                        "CHK-STD-PENDING", "unverified",
                        f"参数「{p.get('name')}」：{result['message']}", [p["id"]]))
                # 非 standard 来源、无适用条目：无可核对内容，不产 issue
        return issues

    # ------------------------------------------------------------------
    def _check_estimated(self, params: list[dict[str, Any]]) -> list[dict[str, Any]]:
        estimated = [p for p in params if p.get("source_type") == "estimated"]
        if not estimated:
            return []
        names = "、".join(p.get("name", "?") for p in estimated)
        return [_issue(
            "CHK-EST-UNVERIFIED", "info",
            f"{len(estimated)} 个估算参数未经公式验证，请人工复核：{names}",
            [p["id"] for p in estimated])]

    # ------------------------------------------------------------------
    def _check_geometry(self, ir2: dict[str, Any], ir3: dict[str, Any],
                        stats: dict[str, int], cov: dict[str, int]) -> list[dict[str, Any]]:
        """几何自洽：特征-参数引用完整、feature_id 唯一、STEP/脚本引用非空
        ＋ 数值级复核（T7.4：linked 参数 vs 实测 bbox 维度）。"""
        issues: list[dict[str, Any]] = []
        param_ids = {p["id"] for p in ir2.get("parameters", [])}
        features: list[dict[str, Any]] = ir3.get("features", [])

        seen: set[str] = set()
        for f in features:
            fid = f.get("feature_id", "?")
            if fid in seen:
                issues.append(_issue(
                    "CHK-GEO-DUP-FEATURE", "error",
                    f"特征 id 重复：{fid}", [], [fid], suggestion="back_to_A3"))
            seen.add(fid)
            linked = f.get("linked_param_id")
            if linked is not None and linked not in param_ids:
                # null=实测未联动，合法（T7.1 契约放宽，A-16）；非 null 才查悬空引用
                issues.append(_issue(
                    "CHK-GEO-LINK", "error",
                    f"特征 {fid} 引用了 IR2 中不存在的参数 {linked}（悬空引用）",
                    [linked] if linked else [], [fid], suggestion="back_to_A3"))

        step_ref = ir3.get("step_file") or {}
        if not step_ref.get("bucket") or not step_ref.get("object_key"):
            issues.append(_issue(
                "CHK-GEO-STEP", "error",
                "IR3 缺少有效的 STEP 产物引用（bucket/object_key 为空）",
                [], [], suggestion="back_to_A3"))
        if not (ir3.get("cadquery_script") or "").strip():
            issues.append(_issue(
                "CHK-GEO-SCRIPT", "error",
                "IR3 缺少 CadQuery 脚本",
                [], [], suggestion="back_to_A3"))

        issues += self._check_geometry_numeric(ir2, ir3, stats, cov)
        return issues

    # ------------------------------------------------------------------
    def _check_document(self, ir2: dict[str, Any], params: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """方案正文风险质检（T12.4，P2-12，设计 §5.4）：warning 级不阻断（overall/CSR 口径
        不变——CSR 分母本就不含 warning，T9.2）。三检查：①降级横幅残留；②正文引用参数 ID
        与参数表一致性；③formula/standard 类参数 source_ref 缺失（溯源依据空洞）。"""
        doc = str(ir2.get("design_document") or "")
        issues: list[dict[str, Any]] = []
        if not doc.strip():
            issues.append(_issue(
                "CHK-DOC-DEGRADED", "warning",
                "方案正文为空（design_document 缺失），请人工补全后复审", []))
            return issues
        if "降级模式" in doc or "降级产物" in doc:
            issues.append(_issue(
                "CHK-DOC-DEGRADED", "warning",
                "正文含降级模式标记（A2 降级产物残留）——正式交付前请人工改写或驳回重算", []))
        # 正文引用参数 ID 一致性（P-数字 形态；用字符类避免转义问题）
        param_ids = {str(p.get("id")) for p in params}
        for ref in sorted(set(re.findall(r"P-[0-9][0-9]{0,3}", doc))):
            if param_ids and ref not in param_ids:
                issues.append(_issue(
                    "CHK-DOC-PARAM-REF", "warning",
                    f"正文引用了参数表中不存在的 {ref}（参数表含：{', '.join(sorted(param_ids)[:6])}）",
                    []))
        for p in params:
            st = str(p.get("source_type") or "")
            if st in ("formula", "standard") and not str(p.get("source_ref") or "").strip():
                issues.append(_issue(
                    "CHK-DOC-SOURCE-MISSING", "warning",
                    f"{st} 类参数「{p.get('name')}」（{p.get('id')}）source_ref 缺失——溯源依据空洞",
                    [str(p.get("id"))]))
        return issues

    def _check_geometry_numeric(self, ir2: dict[str, Any], ir3: dict[str, Any],
                                stats: dict[str, int], cov: dict[str, int]) -> list[dict[str, Any]]:
        """数值级几何复核（T7.4，PHASE-007）。确定性无 LLM；A-09 只报告不改参数。

        - extracted：逐个「已联动」特征比对 bbox[linked_dim] vs 参数值（换算 mm），
          相对偏差 ≤ CAD_GEO_NUM_TOL_PCT（默认 2%，对齐 mockup 06 口径）→ info；超容差 → error；
        - 退化几何（part_count=0 或体积 ≤0）→ CHK-GEO-DEGENERATE error；
        - 提取失败 / 无特征 / 旧版本快照（features 无实测数值）→ CHK-GEO-NUM-SKIP
          **unverified**（T14.4 升级，原 info）——几何复核是应检项，未执行即应检未检；
        - coverage：每个已联动的可比对特征计 1 项 applicable，产出判定后计 checked。
        """
        issues: list[dict[str, Any]] = []
        summary = ir3.get("geometry_summary") or {}
        source = summary.get("source")
        features: list[dict[str, Any]] = ir3.get("features", [])
        params_by_id = {p["id"]: p for p in ir2.get("parameters", [])}

        if source != "extracted":
            if source == "extraction_failed":
                detail = str(summary.get("detail") or "")
                issues.append(_issue(
                    "CHK-GEO-NUM-SKIP", "unverified",
                    f"几何数值复核未执行：STEP 实测提取未成功{'（' + detail + '）' if detail else ''}，"
                    "本轮仅完成引用完整性校验"))
            elif not features:
                issues.append(_issue(
                    "CHK-GEO-NUM-SKIP", "unverified",
                    "IR3 无特征（降级产物），几何数值复核未执行，仅完成引用完整性校验"))
            else:
                issues.append(_issue(
                    "CHK-GEO-NUM-SKIP", "unverified",
                    "旧版本快照无数值几何（features 未携带实测 bbox），仅完成引用完整性校验"))
            return issues

        # 退化几何
        part_count = summary.get("part_count")
        volume = summary.get("volume_mm3")
        if part_count == 0 or (isinstance(volume, (int, float)) and volume <= 0):
            issues.append(_issue(
                "CHK-GEO-DEGENERATE", "error",
                f"几何提取结果退化（part_count={part_count}，volume_mm3={volume}），模型可能为空或非实体",
                [], [], suggestion="back_to_A3"))

        # 数值比对（只对「已联动」的特征；A-09：只报告）。
        # T15.3（ADR-014 §4）：被 param_mapping 覆盖的参数由语义检查器核对（声明制身份），
        # 从 legacy 数值复核中排除——避免同一参数双重裁决与 coverage 重复计数。
        covered = semantics.covered_param_ids(ir3)
        tol = CAD_GEO_NUM_TOL_PCT
        linked_any = False
        for f in features:
            fid = f.get("feature_id", "?")
            linked = f.get("linked_param_id")
            if not linked or linked in covered:
                continue
            linked_any = True
            p = params_by_id.get(linked)
            geometry = f.get("geometry") or {}
            bbox = geometry.get("bbox_mm")
            dim = geometry.get("linked_dim")
            if p is None or not isinstance(bbox, list) or dim not in (0, 1, 2) or len(bbox) <= dim:
                continue  # 悬空引用已由引用完整性检查覆盖；缺数值按旧快照口径由上方兜底提示
            cov["applicable"] += 1  # 已联动的可比对期望 = 必检项
            factor = UNIT_TO_MM.get(str(p.get("unit", "")).strip().lower())
            value = _first_number(p.get("value"))
            if factor is None or value is None or value <= 0:
                issues.append(_issue(
                    "CHK-GEO-NUM-SKIP", "unverified",
                    f"参数 {linked}「{p.get('name')}」单位/取值非长度数值，未做数值复核",
                    [linked], [fid]))
                continue
            cov["checked"] += 1  # 比对已产出确定性判定
            expected_mm = value * factor
            measured = float(bbox[dim])
            dev_pct = abs(measured - expected_mm) / expected_mm * 100.0
            if dev_pct <= tol:
                stats["pass"] += 1  # 数值一致（issue 保持 info 级降低噪音），计入 CSR
                issues.append(_issue(
                    "CHK-GEO-NUM", "info",
                    f"数值一致：参数 {linked}「{p.get('name')}」= {value}{p.get('unit')}，"
                    f"关联特征 {fid} 实测 {measured:g} mm，偏差 {dev_pct:.2f}%（容差 {tol:g}%）",
                    [linked], [fid]))
            else:
                stats["violation"] += 1
                issues.append(_issue(
                    "CHK-GEO-NUM", "error",
                    f"{linked}「{p.get('name')}」= {value}{p.get('unit')}，但关联特征 {fid} "
                    f"实测 {measured:g} mm，偏差 {dev_pct:.1f}%（容差 {tol:g}%），"
                    "建议在审阅关结合 IR4 裁决",
                    [linked], [fid], suggestion="back_to_A3"))
        if not linked_any and not covered:
            issues.append(_issue(
                "CHK-GEO-NUM-SKIP", "unverified",
                "几何为实测提取，但无已联动的长度类参数可做数值复核（联动容差内无匹配）——"
                "关键尺寸未经实测核对，请人工复核或回 A3 建立参数联动"))
        return issues

    # ------------------------------------------------------------------
    def _check_semantic(self, ir2: dict[str, Any], ir3: dict[str, Any],
                        stats: dict[str, int], cov: dict[str, int]) -> list[dict[str, Any]]:
        """尺寸语义检查（T15.3/T15.5，ADR-014 §4、§7）：verified 映射对象的声明参数逐一过
        semantics.SEMANTIC_CHECKERS 注册表（length/diameter/thickness）；容差按
        参数登记 > dim_type 覆盖 > 全局默认解析，非全局来源在 issue 消息披露出处。

        - violation → error + suggestion=back_to_A3（案例 02/03 的落点：声明制映射
          不受联动容差断链影响，100 vs 150 必报超差；孔径不被同值宽度顶替）；
        - pass → info（计 CSR 通过）；unverified → 应检未检显性（无测量/单位非长度/
          无该维检查器/声明参数缺失），不计 checked；
        - coverage：verified 对象的声明参数计入 applicable，产出判定计 checked；
          ambiguous/unmatched 对象的参数由 CHK-SEM-MAPPING 承担（与 legacy skip 同口径，
          不计 applicable）。
        """
        mapping = ir3.get("param_mapping") or {}
        params_by_id = {str(p["id"]): p for p in ir2.get("parameters", [])}
        issues: list[dict[str, Any]] = []
        for obj in mapping.get("objects", []):
            if obj.get("status") != "verified":
                continue  # 非 verified 由 _check_semantic_mapping 显性（unverified）
            oid = str(obj.get("object_id", "?"))
            role = str(obj.get("role", "?"))
            feats = [obj["feature_id"]] if obj.get("feature_id") else []
            for entry in obj.get("params", []):
                pid = str(entry.get("param_id", ""))
                dim = str(entry.get("dim_type", "other"))
                cov["applicable"] += 1  # 已声明且已确证绑定的参数 = 必检项
                param = params_by_id.get(pid)
                if param is None:
                    noun = self._identity_noun(ir3)
                    issues.append(_issue(
                        f"CHK-SEM-{dim.upper()}", "unverified",
                        f"对象 {oid}（role={role}）{noun}的参数 {pid} 不在 IR2 中——{noun}与参数表"
                        f"不一致，请人工复核或回 A3 修正{noun}", [pid] if pid else [], feats))
                    continue
                checker = semantics.SEMANTIC_CHECKERS.get(dim)
                if checker is None:
                    issues.append(_issue(
                        f"CHK-SEM-{dim.upper()}", "unverified",
                        f"参数 {pid}「{param.get('name')}」的尺寸语义 {dim} 尚无注册检查器——"
                        "该维度未做语义核对（如需覆盖请在 semantics.py 注册）", [pid], feats))
                    continue
                tol = semantics.resolve_tolerance(dim, SEMANTIC_TOL_PCT, pid)
                # 容差出处披露（A-16）：全局默认保持原文案逐字节不变，仅非全局来源追加出处
                src = semantics.tolerance_source(dim, pid)
                tol_note = f"（容差 {tol:g}%）" if src == "global" else (
                    f"（容差 {tol:g}%，{'按参数登记' if src == 'param' else 'dim_type 缺省'}）")
                verdict = checker(param, entry, tol)
                if verdict["verdict"] == "pass":
                    stats["pass"] += 1
                    cov["checked"] += 1
                    issues.append(_issue(
                        f"CHK-SEM-{dim.upper()}", "info",
                        f"语义一致：参数 {pid}「{param.get('name')}」= {_first_number(param.get('value'))}"
                        f"{param.get('unit')}，对象 {oid}（role={role}）实测 "
                        f"{verdict['measured_mm']:g} mm（{entry.get('measurement')}），"
                        f"偏差 {verdict['deviation_pct']:.2f}%{tol_note}", [pid], feats))
                elif verdict["verdict"] == "violation":
                    stats["violation"] += 1
                    cov["checked"] += 1
                    issues.append(_issue(
                        f"CHK-SEM-{dim.upper()}", "error",
                        f"{pid}「{param.get('name')}」= {_first_number(param.get('value'))}"
                        f"{param.get('unit')}（期望 {verdict['expected_mm']:g} mm），但对象 {oid}"
                        f"（role={role}）实测 {verdict['measured_mm']:g} mm"
                        f"（{entry.get('measurement')}），偏差 {verdict['deviation_pct']:.1f}%"
                        f"{tol_note}——建议回 A3 修正建模",
                        [pid], feats, suggestion="back_to_A3"))
                else:
                    issues.append(_issue(
                        f"CHK-SEM-{dim.upper()}", "unverified",
                        f"参数 {pid}「{param.get('name')}」语义核对未执行：{verdict.get('reason', '')}"
                        "（A-16 如实未检，请人工复核）", [pid], feats))
        return issues

    # ------------------------------------------------------------------
    def _check_display_transform(self, ir2: dict[str, Any], ir3: dict[str, Any]) -> list[dict[str, Any]]:
        """显示变换显性化（T15.1，ADR-014 §3）。IR3 display_transforms 逐条呈现：
        建模使用了视觉值而非物理值这一事实必须可见，不得静默（A-16）。

        - info：正常声明（物理值 → 视觉值 + 规则），物理原值保留于参数表；
        - warning：param_id 指向 IR2 不存在的参数——声明存疑，请人工复核；
        - 纯提示级：不计入 CSR/coverage（与 info 同口径；"用了显示变换"不是约束违反，
          也不是应检未检——裁决权在 gate2 人工）。
        """
        transforms = ir3.get("display_transforms") or []
        if not transforms:
            return []
        known = {str(p.get("id")) for p in ir2.get("parameters", [])}
        issues: list[dict[str, Any]] = []
        for t in transforms:
            pid = t.get("param_id")
            physical = t.get("physical_mm")
            visual = t.get("visual_mm")
            rule = str(t.get("rule") or "未声明规则")
            label = f"参数 {pid}" if pid else "（无关联参数）"
            shrink_note = ""
            try:
                if physical and visual and float(physical) != 0:
                    shrink_note = f"，缩至 {abs(float(visual) / float(physical)) * 100:.1f}%"
            except (TypeError, ValueError):
                shrink_note = ""
            if pid and str(pid) not in known:
                issues.append(_issue(
                    "CHK-DISPLAY-TRANSFORM", "warning",
                    f"显示变换声明引用了 IR2 不存在的 {pid}"
                    f"（物理值 {physical} mm → 视觉值 {visual} mm，rule={rule}）——声明存疑，请人工复核"))
                continue
            issues.append(_issue(
                "CHK-DISPLAY-TRANSFORM", "info",
                f"显示变换：{label} 物理值 {physical} mm → 视觉值 {visual} mm{shrink_note}"
                f"（rule={rule}）——建模使用视觉值，物理原值保留于参数表（显性呈现，A-16）",
                [str(pid)] if pid else []))
        return issues

    # ------------------------------------------------------------------
    @staticmethod
    def _identity_noun(ir3: dict[str, Any]) -> str:
        """身份依据措辞（T15.4，A-16 不修饰）：声明制对象才「声明」过；legacy 几何候选
        对象只有候选证据——文案按 identity_basis 区分，不把候选说成声明。"""
        basis = str((ir3.get("param_mapping") or {}).get("identity_basis") or "declared_objects")
        return "声明" if basis == "declared_objects" else "几何候选"

    @staticmethod
    def _check_semantic_mapping(ir2: dict[str, Any], ir3: dict[str, Any]) -> list[dict[str, Any]]:
        """映射状态显性化（T15.2，ADR-014 §2/§6）。param_mapping 中 ambiguous/unmatched
        对象的参数 → CHK-SEM-MAPPING unverified（应检未检，T14.4 状态学）：
        映射未确立即无法按尺寸语义核对——不允许静默漏检；无参数对象（role-only/legacy
        无候选不进映射）不产生检查语义（无噪声）。verified 对象的参数由语义检查器核对
        （semantics.py，T15.3）。文案按身份依据区分「声明/几何候选」（T15.4，A-16）。
        """
        mapping = ir3.get("param_mapping") or {}
        noun = A4Agent._identity_noun(ir3)
        issues: list[dict[str, Any]] = []
        known = {str(p.get("id")) for p in ir2.get("parameters", [])}
        for obj in mapping.get("objects", []):
            if obj.get("status") == "verified":
                continue
            pids = [str(p.get("param_id")) for p in obj.get("params", []) if p.get("param_id")]
            if not pids:
                continue
            dangling = [pid for pid in pids if pid not in known]
            note = f"；{noun}引用了 IR2 不存在的参数 {dangling}（请核对）" if dangling else ""
            issues.append(_issue(
                "CHK-SEM-MAPPING", "unverified",
                f"对象 {obj.get('object_id')}（role={obj.get('role')}）映射状态为 "
                f"{obj.get('status')}——{noun}的 {len(pids)} 个参数未做尺寸语义核对，请人工复核"
                f"或回 A3 修正{noun}{note}",
                pids, [obj["feature_id"]] if obj.get("feature_id") else []))
        return issues


def _issue(
    constraint_id: str,
    severity: str,
    message: str,
    related_params: list[str] | None = None,
    related_features: list[str] | None = None,
    suggestion: str | None = None,
) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "constraint_id": constraint_id,
        "severity": severity,
        "message": message,
        "related_params": related_params or [],
        "related_features": related_features or [],
    }
    if suggestion:
        issue["suggestion"] = suggestion
    return issue


# ABS_TOL 从 recompute 再导出供测试引用（容差口径单点定义）
__all__ = ["A4Agent", "ABS_TOL"]
