# -*- coding: utf-8 -*-
"""T16.2 B2 选例实验——有/无范例生成对照（范例增益；devlog 2026-11-21 §3.2 注册的 B2 应纳入项）。

主链口径（与 A3Agent.run() 首轮逐字一致）：
  select_reference 真实检索（with_ref）/强制无范例（no_ref）
  → A3Agent(llm=LlmAdapter())._generate(design_doc, params, ref_code, domain)
  → validators.validate_chain(script, domain) → sandbox.run_in_subprocess(script, 300) 首轮读 ok
  （不跑修复循环——隔离「生成质量」本身；codegen 缓存天然绕过——_generate 不触缓存）。
配置：{no_ref, with_ref} × 3 文档 × 2 发 = 12 发真实 LLM。
**必须以 LLM_CACHE_ENABLED=0 启动**（同 prompt 重复发会命中 7 天 L1 缓存返回旧响应）。
度量：首轮重放 ok / 连通性（复用 gen_ref_candidates._connectivity）/ bbox / volume /
@obj 声明数 / 错误分类 / 耗时。逐发落盘 docs/reviews/t162-refgain/，跑毕追加报告生成段+总裁决。

用法（仓库根，底座在跑）：set -a; source .env; set +a
  LLM_CACHE_ENABLED=0 python scripts/eval_refgain.py
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "py-engine"))

from agents.a3_cad.agent import A3Agent  # noqa: E402
from cad_engine import validators  # noqa: E402
from cad_engine.reference import CodeSearchService  # noqa: E402
from cad_engine.sandbox import run_in_subprocess  # noqa: E402
from llm.adapter import LlmAdapter  # noqa: E402

_DATASET = Path(__file__).resolve().parent / "eval_refsel_dataset.json"
OUT_DIR = _ROOT / "docs" / "reviews" / "t162-refgain"
REPORT = _ROOT / "docs" / "reviews" / "2026-11-27-范例选例实验报告-T162.md"
SHOTS = 2
SANDBOX_TIMEOUT_S = 300

# 复用 T16.4 连通性必检（单一事实源；importlib 加载 scripts/ 非包模块）
_spec = importlib.util.spec_from_file_location("grc", _ROOT / "scripts" / "gen_ref_candidates.py")
_grc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_grc)


def _classify(error: str) -> str:
    e = error or ""
    if "SyntaxError" in e:
        return "syntax"
    if "NameError" in e or "ModuleNotFound" in e or "ImportError" in e:
        return "name_import"
    if "BRep_API" in e or "StdFail" in e or "OCC" in e:
        return "occt"
    if "loft" in e.lower():
        return "loft"
    if "执行超时" in e:
        return "timeout"
    if "未生成 STEP" in e:
        return "no_step"
    if not e:
        return "-"
    return "other"


def _run_one(doc_cfg: dict, cfg_name: str, shot: int, agent: A3Agent, svc: CodeSearchService) -> dict:
    doc, params = doc_cfg["doc"], doc_cfg["params"]
    rec: dict = {"doc_id": doc_cfg["id"], "config": cfg_name, "shot": shot,
                 "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if cfg_name == "with_ref":
        ref_code, match = svc.select_reference(doc)
        domain = (match or {}).get("domain") or None
        rec["ref_hit"] = (match or {}).get("ref_id")
        rec["ref_grade"] = (match or {}).get("grade")
    else:
        ref_code, domain = "", None
        rec["ref_hit"] = None
    t0 = time.perf_counter()
    try:
        script = agent._generate(doc, params, ref_code, domain)
        script, _static_ok, _errs, _warns = validators.validate_chain(script, domain=domain)
    except Exception as exc:
        rec.update(status="generate_failed", error=str(exc)[:300],
                   error_class="generate", elapsed_s=round(time.perf_counter() - t0, 1))
        return rec
    gen_s = round(time.perf_counter() - t0, 1)
    replay = run_in_subprocess(script, time_limit_s=SANDBOX_TIMEOUT_S)
    rec.update({
        "gen_elapsed_s": gen_s,
        "replay_ok": replay["ok"],
        "error": (replay.get("error") or "")[:300],
        "error_class": _classify(replay.get("error") or "") if not replay["ok"] else "-",
        "auto_exported": replay.get("auto_exported"),
        "obj_decls": len(re.findall(r"#\s*@obj\b", script)),
        "script_chars": len(script),
    })
    if replay["ok"] and replay.get("step_files"):
        conn = _grc._connectivity(replay["step_files"][0])
        rec["connectivity"] = {"solids": conn.get("solids"), "connected": conn.get("connected")}
        insp = _grc.inspect_step(Path(replay["step_files"][0]).read_bytes())
        if insp.get("ok"):
            ov = insp.get("overall") or {}
            rec["bbox_mm"] = ov.get("bbox_mm")
            rec["volume_mm3"] = ov.get("volume_mm3")
        out_png = OUT_DIR / f"{doc_cfg['id']}-{cfg_name}-{shot}_views.png"
        pv = _grc._render_views(replay["step_files"][0], out_png)
        rec["preview"] = pv.get("ok")
    # 参数值抽查：参数表 value 以字面常量出现在脚本中的比例（粗口径，如实）
    if doc_cfg.get("params"):
        vals = [str(p["value"]) for p in doc_cfg["params"] if isinstance(p["value"], (int, float))]
        found = sum(1 for v in vals if v in script)
        rec["param_literal_hits"] = f"{found}/{len(vals)}"
    (OUT_DIR / f"{doc_cfg['id']}-{cfg_name}-{shot}.py").write_text(script, encoding="utf-8")
    rec["status"] = "replay_ok" if replay["ok"] else "replay_failed"
    rec["elapsed_s"] = round(time.perf_counter() - t0, 1)
    return rec


def _append_report(results: list[dict]) -> None:
    lines = ["## 生成对照（有/无范例 × 3 文档 × 2 发；主链首轮口径）", ""]
    lines.append("| 文档 | 配置 | 发 | 首轮重放 | 连通 | bbox(mm) | @obj | 参数字面命中 | 错误类 | 耗时(s) |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        conn = r.get("connectivity") or {}
        conn_txt = ("通" if conn.get("connected") else "不连通") if conn else "-"
        bbox = "×".join(str(x) for x in (r.get("bbox_mm") or [])) or "-"
        lines.append(
            f"| {r['doc_id']} | {r['config']} | {r['shot']} | {'PASS' if r.get('replay_ok') else 'FAIL'} "
            f"| {conn_txt} | {bbox} | {r.get('obj_decls', '-')} | {r.get('param_literal_hits', '-')} "
            f"| {r.get('error_class', '-')} | {r.get('elapsed_s')} |"
        )
    ok_by = {}
    for r in results:
        ok_by.setdefault(r["config"], []).append(1 if r.get("replay_ok") else 0)
    conn_by = {}
    for r in results:
        conn_by.setdefault(r["config"], []).append(
            1 if (r.get("connectivity") or {}).get("connected") else 0)
    lines += ["", "### 汇总与范例增益结论", ""]
    for cfg in ("no_ref", "with_ref"):
        seq = ok_by.get(cfg, [])
        cseq = conn_by.get(cfg, [])
        lines.append(f"- **{cfg}**：首轮重放 {sum(seq)}/{len(seq)} PASS；连通性 {sum(cseq)}/{len(cseq)}")
    lines += [
        "",
        "- 口径：首轮生成（不跑修复循环）隔离生成质量；LLM_CACHE_ENABLED=0（独立采样）；temperature=0.2 非确定性如实",
        "- **连通性口径注记**：A3 主链 system prompt 强制 `cq.Assembly()` 多部件装配（BASE 规则 8），STEP 天然多 solid——"
        "连通性指标在此口径下恒为「不连通」，与 harness 候选的单实体要求不同域，不构成质量否定；本实验以首轮重放成功率为主指标",
        "- 范例增益=with_ref 相对 no_ref 的首轮成功率/连通性/尺寸符合度对照（逐例明细见 t162-refgain/）",
        "",
        "## 总裁决（ADR-018 决策 2 判据核算）",
        "",
        "- 判据 A（错域 0）：weak 关三配置 0 ✓；fusion+weak=4 ✗",
        "- 判据 B（fusion 相对 lexical 净增 ≥3，注册字面口径）：**净增 0** ✗——机制实证：ALLOW_WEAK 关时向量扩召回条目全部被过滤，fusion 与 lexical 逐条同果（扩展口径 fusion+weak vs lexical+weak 净增 3，但伴随错域 4/weak 精度 50%，不构成放开理由）",
        "- ALLOW_WEAK 门（精度 ≥90%）：fusion+weak 下 4/8=**50%** ✗",
        "- 视觉判据：top-1 全中 ✓ 但 Δ=见视觉段（<0.1）✗",
        "- **裁决：REF_VECTOR_ENABLED / REF_MATCH_ALLOW_WEAK / REF_VISUAL_ENABLED 全部维持默认关**；"
        "「向量单开≈无操作」由代码事实升级为实验实证（同 grade 内重排在 3 行库上无观察差异）",
        "",
        "> 局限如实：AI 标注冻结集（非第三方）；3 行库规模；12 发采样量；多范例维度不在矩阵（主链 top_n=1）；deepseek-V3.2 单供应商；首轮口径不含修复循环。",
        "> 报告由 scripts/eval_refsel.py + scripts/eval_refgain.py 生成，可复跑。",
    ]
    existing = REPORT.read_text(encoding="utf-8") if REPORT.exists() else ""
    REPORT.write_text(existing.rstrip("\n") + "\n" + "\n".join(lines), encoding="utf-8")
    print(f"[eval] 报告（含生成段）: {REPORT}")


def main() -> int:
    import app_config

    if app_config.LLM_CACHE_ENABLED:
        print("[FATAL] 请以 LLM_CACHE_ENABLED=0 启动本脚本（重复发会命中 L1 缓存返回旧响应）")
        return 2
    data = json.loads(_DATASET.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    agent = A3Agent(llm=LlmAdapter())
    svc = CodeSearchService()
    results: list[dict] = []
    for doc_cfg in data["gen_docs"]:
        for cfg_name in ("no_ref", "with_ref"):
            for shot in (1, 2):
                tag = f"{doc_cfg['id']}-{cfg_name}-{shot}"
                print(f"[refgain] {tag} …", flush=True)
                rec = _run_one(doc_cfg, cfg_name, shot, agent, svc)
                results.append(rec)
                (OUT_DIR / f"{tag}.result.json").write_text(
                    json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"    -> {rec.get('status')} ({rec.get('elapsed_s')}s)", flush=True)
    (OUT_DIR / "all-results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    _append_report(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
