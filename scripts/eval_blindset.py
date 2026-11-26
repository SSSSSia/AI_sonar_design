# -*- coding: utf-8 -*-
"""T18.3 视觉校验盲测 harness（PHASE-018；gate 判据兑现，毕设实验数据）。

盲测集（维护者构造的已知几何错误候选 + gate2 批准同源正确对照，A-16 如实非第三方集）：
  side_scan_towfish.py      v1 错误例——loft 轴横穿 + 翼悬空（两类，REPORT.md 诊断在案）
  transducer_array.py       v1 错误例——pushPoints 栈替换致 24 根阵元只造 1 根
  towed_array_system_twin   正确对照（ref_code catalog / deposit 同源，历史 gate2 批准）
  sonobuoy.py               第二正确对照（v1 达标候选——只增强防误报强度，不放宽判据）

流程：sandbox 重放出 STEP → vision_check.run_vision_check（生产入口直调，不经 worker；
session_id=blindtest-t183 专用前缀，views.png 落 step-models 桶可回看）→ 逐例记录。

**gate 判据（ADR-017 决策 3 事先注册，实施前锁定不得放宽）**：3 类已知错误至少 2 类
被该例 warning 级 findings 语义命中 + 正确对照无 warning 级 findings（info 如实记录）。
harness 只产出原始数据与确定性关键词提示；gate 裁决由报告人工做出（判词全文可核对）。
**不过不进 T18.4**（修复循环视觉反馈），如实停在旁路收尾。

用法（仓库根）：set -a; source .env; set +a; python scripts/eval_blindset.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "py-engine"))

from cad_engine import sandbox  # noqa: E402
from cad_engine.vision_check import run_vision_check  # noqa: E402

CANDIDATES = _ROOT / "docs" / "reviews" / "t164-candidates"
OUT_DIR = _ROOT / "docs" / "reviews" / "t183-blindset"
REPORT = _ROOT / "docs" / "reviews" / "2026-11-26-视觉盲测报告-T183.md"
SESSION_ID = "blindtest-t183"
SANDBOX_TIMEOUT_S = 300

# 逐类错误的确定性关键词提示（仅呈现辅助；gate 裁决以报告人工核对 findings 原文为准）
HINTS = {
    "loft 轴横穿": ("横穿", "穿透", "埋进", "相交", "穿过"),
    "翼悬空": ("悬空", "断裂", "脱离", "分离", "断开"),
    "阵列数量不符": ("数量", "只有一根", "单根", "缺失", "仅一", "阵元"),
}

BLINDSET = [
    {"id": "side_scan_towfish_v1", "kind": "error", "expected": ["loft 轴横穿", "翼悬空"],
     "script": CANDIDATES / "side_scan_towfish.py"},
    {"id": "transducer_array_v1", "kind": "error", "expected": ["阵列数量不符"],
     "script": CANDIDATES / "transducer_array.py"},
    {"id": "towed_array_system_twin", "kind": "control", "expected": [],
     "script": _ROOT / "py-engine" / "cad_engine" / "ref_code" / "towed_array_system_twin.py"},
    {"id": "sonobuoy_v1", "kind": "control", "expected": [],
     "script": CANDIDATES / "sonobuoy.py"},
]


def run_case(case: dict) -> dict:
    script_path = Path(case["script"])
    text = script_path.read_text(encoding="utf-8")
    t0 = time.perf_counter()
    replay = sandbox.run_in_subprocess(text, SANDBOX_TIMEOUT_S)
    row: dict = {"id": case["id"], "kind": case["kind"], "expected": case["expected"],
                 "script": str(script_path.relative_to(_ROOT)),
                 "replay_ok": bool(replay.get("ok")), "replay_error": str(replay.get("error") or "")}
    if not replay["ok"] or not replay.get("step_files"):
        row.update({"vc_status": "skipped", "detail": f"replay_failed: {row['replay_error'][:200]}",
                    "findings": None, "verdict_text": None,
                    "elapsed_s": round(time.perf_counter() - t0, 1)})
        return row

    step = replay["step_files"][0]
    vc = run_vision_check(SESSION_ID, str(step))
    findings = vc.get("findings")
    row.update({
        "vc_status": vc.get("status"), "vc_detail": vc.get("detail"),
        "views_png": vc.get("views_png"), "verdict_text": vc.get("verdict_text"),
        "findings": findings, "elapsed_s": round(time.perf_counter() - t0, 1),
    })
    if isinstance(findings, list):
        warn = [f for f in findings if f.get("severity") == "warning"]
        row["warn_findings"] = warn
        hints = {}
        for et in case["expected"]:
            hints[et] = [f for f in findings
                         if any(k in (str(f.get("item")) + str(f.get("detail"))) for k in HINTS.get(et, ()))]
        row["hint_hits"] = {k: len(v) for k, v in hints.items()}
        row["hint_hit_texts"] = {k: v for k, v in hints.items() if v}
    return row


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in BLINDSET:
        print(f"[blind] {case['id']}（{case['kind']}，期望 {case['expected'] or '无错误'}）…", flush=True)
        row = run_case(case)
        rows.append(row)
        warn_n = len(row.get("warn_findings") or [])
        print(f"[blind]   replay_ok={row['replay_ok']} vc={row['vc_status']} "
              f"warn_findings={warn_n} hint_hits={row.get('hint_hits')} {row['elapsed_s']}s", flush=True)
        (OUT_DIR / f"{case['id']}.result.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")

    errors = [r for r in rows if r["kind"] == "error"]
    controls = [r for r in rows if r["kind"] == "control"]
    hit_types = [et for r in errors for et in r.get("hint_hits", {})
                 if r.get("hint_hits", {}).get(et, 0) > 0]
    control_warn = [r["id"] for r in controls if r.get("warn_findings")]
    gate_pass = len(hit_types) >= 2 and not control_warn

    lines = [
        "# 视觉校验盲测报告 · T18.3（gate 裁决；毕设实验数据）",
        "",
        f"- 盲测集：维护者构造的已知几何错误 v1 候选（docs/reviews/t164-candidates/，错误诊断在案）"
        f"+ 正确对照 twin（gate2 批准同源 catalog）与 sonobuoy（v1 达标候选，防误报增强对照）——"
        f"非第三方集，A-16 如实",
        f"- 流程：sandbox 重放（300s 上限）→ vision_check 生产入口直调（session_id={SESSION_ID}，"
        f"views.png 落 step-models 桶可回看）→ VLM=Qwen/Qwen3-VL-32B-Instruct 单次运行（失败重试一次语义）",
        f"- gate 判据（ADR-017 决策 3 事先注册）：3 类已知错误（loft 轴横穿/翼悬空/阵列数量不符）"
        f"至少 2 类被 warning 级 findings 命中 + 正确对照无 warning 级 findings",
        "",
        "## 逐例明细",
        "",
        "| id | kind | 期望错误 | replay | vc | warning findings | 关键词提示命中 | 耗时(s) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['kind']} | {'、'.join(r['expected']) or '—'} "
            f"| {r['replay_ok']} | {r['vc_status']} "
            f"| {len(r.get('warn_findings') or [])} | {r.get('hint_hits') or '—'} | {r['elapsed_s']} |")
    lines += ["", "## 逐例 findings 与判词（原文全文，人工可核对 A-16）", ""]
    for r in rows:
        lines.append(f"### {r['id']}（{r['kind']}）")
        if r["vc_status"] != "checked":
            lines.append(f"- vc_status={r['vc_status']}：{r.get('vc_detail') or r.get('detail')}")
        else:
            if r.get("findings") is not None:
                lines.append(f"- findings（{len(r['findings'])} 条）：")
                for f in r["findings"]:
                    lines.append(f"  - [{f.get('severity')}] {f.get('item')}：{f.get('detail')}")
            else:
                lines.append("- findings 解析失败（verdict_text 全文在案）")
            vt = (r.get("verdict_text") or "").replace("\n", " ")
            lines.append(f"- verdict_text：`{vt[:400]}{'…' if len(vt) > 400 else ''}`")
            if r.get("hint_hit_texts"):
                for et, fs in r["hint_hit_texts"].items():
                    for f in fs:
                        lines.append(f"  - 命中提示[{et}]：[{f.get('severity')}] {f.get('item')}——{f.get('detail')}")
        lines.append("")

    lines += [
        "## gate 裁决",
        "",
        f"- 错误类型命中（关键词提示口径，裁决以 findings 原文人工核对为准）：{hit_types or '无'}"
        f"（{len(hit_types)}/3 类）",
        f"- 正确对照 warning findings：{control_warn or '无'}",
        f"- **判据核算：{len(hit_types)}≥2 且对照零 warning → {'满足' if gate_pass else '不满足'}**",
        f"- **gate 结论：{'PASS——T18.4（修复循环视觉反馈）获准入' if gate_pass else 'FAIL——T18.4 不做，如实停在旁路收尾（ADR-017 决策 3），T18.5/T18.6 相对独立按排期推进'}**",
        "",
        "> 局限如实：单次运行（VLM 非确定性）；盲测集小（2 错误脚本覆盖 3 类错误+2 对照）；"
        "CHECK_PROMPT 所列异常类型（悬空/穿透/数量）与盲测错误同型——属设计使然的同源性，"
        "不构成对未见错误类型的泛化能力证明；三视图分辨率对细小异常的可达性未单独评估。",
        "> 报告由 scripts/eval_blindset.py 生成，逐例原始结果在 docs/reviews/t183-blindset/，可复跑。",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"[blind] 报告: {REPORT}", flush=True)
    print(json.dumps({"hit_types": hit_types, "control_warn": control_warn,
                      "gate_pass": gate_pass}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
