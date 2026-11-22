# -*- coding: utf-8 -*-
"""T16.4 路径③候选生成 harness（PHASE-016 §3.9；候选≠范例，入库须用户逐条人工确认）。

对每个类型规格：真实 LLM 单发生成 CadQuery 脚本 → strip_markdown → 沙箱子进程重放
（A3 同款隔离）→ geometry_inspect 实测 → 候选文件+质量证据落盘。AI 生成无领域真值
（几何可测、域正确性不可测）——候选报告是用户的裁决输入，不是登记事实（B4 警句）。

用法（仓库根，需 .env 且 llm_providers 有 enabled 真实供应商）：
    set -a; source .env; set +a
    python scripts/gen_ref_candidates.py                 # 全部内置类型
    python scripts/gen_ref_candidates.py --only side_scan_towfish
    python scripts/gen_ref_candidates.py --out-dir docs/reviews/t164-candidates
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "py-engine"))

from cad_engine.geometry_inspect import inspect_step  # noqa: E402
from cad_engine.reference import strip_markdown  # noqa: E402
from cad_engine.sandbox import run_in_subprocess  # noqa: E402

# 内置类型规格（首批 3 类；类型枚举出自 docs/reference/总体设计方案 §安装形式×功能体制）
TYPE_SPECS: list[dict] = [
    {
        "id": "side_scan_towfish",
        "cn": "侧扫声呐拖鱼",
        "requirement": (
            "设计一个侧扫声呐拖鱼（towfish）：流线型主体，圆柱形耐压段 + 收缩的头部与尾部，"
            "两侧对称的稳定翼（薄板），尾部小尺寸推进/拖曳环。主体长约 1200mm、耐压段直径约 200mm。"
            "顶部用注释声明参数（@param 语法：长度/直径/翼展等），参数化为命名常量。"
        ),
        "sonar_types": ["侧扫声呐", "side scan sonar", "拖鱼"],
    },
    {
        "id": "sonobuoy",
        "cn": "声呐浮标",
        "requirement": (
            "设计一个声呐浮标（sonobuoy）：圆柱形浮体（直径约 124mm、高约 915mm）+ 顶部天线杆"
            "（细圆柱）+ 底部水听器悬挂段（细长圆柱）。参数化为命名常量（浮体直径/高度/天线杆长）。"
        ),
        "sonar_types": ["声呐浮标", "sonobuoy"],
    },
    {
        "id": "transducer_array",
        "cn": "换能器基阵",
        "requirement": (
            "设计一个平面换能器基阵：矩形基座板 + 按 4x6 网格阵列的圆柱阵元（每个阵元为短圆柱），"
            "阵元间距均匀。基座约 480x320x20mm，阵元直径 40mm 高 30mm。参数化为命名常量（行/列数、间距）。"
        ),
        "sonar_types": ["换能器基阵", "transducer array"],
    },
]

_PROMPT_TMPL = """You are an expert CAD engineer. Generate ONE self-contained CadQuery script (Python).

Requirements:
- `import cadquery as cq` ONLY (no other third-party imports).
- Define all dimensions as named module-level constants with a short physical-meaning comment each.
- Units: millimeters.
- Model: {requirement}
- The script MUST end with a variable named `result` holding a single solid Workplane/Shape.
- Keep it simple and robust (box/cylinder/extrude/cut/union primitives preferred).
- Output ONLY the Python code, no markdown fences, no explanations.
"""


def _llm_generate(spec: dict, timeout_s: int) -> str:
    from llm.adapter import LlmAdapter

    adapter = LlmAdapter()
    messages = [
        {"role": "system", "content": "You output only runnable Python code. No explanations."},
        {"role": "user", "content": _PROMPT_TMPL.format(requirement=spec["requirement"])},
    ]
    return adapter.chat(messages, timeout_s=timeout_s)


def _measure(step_files: list) -> dict:
    """首个 STEP 的几何实测摘要（A-16：实测什么写什么，不造值）。"""
    if not step_files:
        return {"measured": False, "reason": "沙箱未产出 STEP"}
    insp = inspect_step(Path(step_files[0]).read_bytes())
    if not insp.get("ok"):
        return {"measured": False, "reason": str(insp.get("error"))[:200]}
    overall = insp.get("overall") or {}
    return {
        "measured": True,
        "overall_bbox_mm": overall.get("bbox_mm"),
        "overall_volume_mm3": overall.get("volume_mm3"),
        "part_count": overall.get("part_count"),
        "parts": [
            {"name": p.get("name"), "volume_mm3": p.get("volume_mm3"), "bbox_mm": p.get("bbox_mm")}
            for p in (insp.get("parts") or [])[:3]
        ],
    }


def run_one(spec: dict, out_dir: Path, timeout_s: int) -> dict:
    t0 = time.perf_counter()
    rec: dict = {
        "id": spec["id"],
        "cn": spec["cn"],
        "sonar_types": spec["sonar_types"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "six_items_draft": {
            "来源": f"路径③：真实 LLM 单发生成（harness scripts/gen_ref_candidates.py，批次 {rec_batch()}）",
            "适用域": "待裁决（AI 生成无领域真值；sonar_types=" + ",".join(spec["sonar_types"]) + "）",
            "参数语义": "以脚本内命名常量注释为准（生成后人工核对）",
            "可重放证据": None,  # 重放后回填
            "许可范围": "项目自有（候选阶段，未入库）",
            "重复关系": "与库内既有范例无内容重叠（zztest/towed 均不同类型）——入库前复查",
        },
    }
    try:
        raw = _llm_generate(spec, timeout_s)
        code = strip_markdown(raw)
        rec["llm_chars"] = len(raw)
    except Exception as exc:
        rec["status"] = "llm_failed"
        rec["error"] = str(exc)[:300]
        return rec
    replay = run_in_subprocess(code, time_limit_s=120)
    rec["replay"] = {
        "ok": replay["ok"],
        "auto_exported": replay.get("auto_exported"),
        "error": (replay.get("error") or "")[:300],
    }
    rec["six_items_draft"]["可重放证据"] = (
        f"沙箱重放 {'PASS' if replay['ok'] else 'FAIL'}（auto_export={replay.get('auto_exported')}）"
    )
    if replay["ok"] and replay.get("step_files"):
        rec["geometry"] = _measure(replay["step_files"])
    (out_dir / f"{spec['id']}.py").write_text(code, encoding="utf-8")
    rec["status"] = "candidate_ready" if replay["ok"] else "replay_failed"
    rec["elapsed_s"] = round(time.perf_counter() - t0, 1)
    return rec


def rec_batch() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="T16.4 路径③候选生成（候选≠范例，入库须人工确认）")
    ap.add_argument("--only", help="只跑指定类型 id", default=None)
    ap.add_argument("--out-dir", default=str(_ROOT / "docs" / "reviews" / "t164-candidates"))
    ap.add_argument("--timeout-s", type=int, default=300)
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = [s for s in TYPE_SPECS if not args.only or s["id"] == args.only]
    if not specs:
        print("no matching type spec; available:", ", ".join(s["id"] for s in TYPE_SPECS))
        return 2

    records = []
    for spec in specs:
        print(f"[gen] {spec['id']} ({spec['cn']}) …", flush=True)
        rec = run_one(spec, out_dir, args.timeout_s)
        records.append(rec)
        print(f"    -> {rec.get('status')} ({rec.get('elapsed_s', '?')}s)", flush=True)
        (out_dir / f"{spec['id']}.report.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# T16.4 首批候选报告（路径③；候选≠范例，入库须用户逐条人工确认）", "",
             f"批次 {rec_batch()} · {len(records)} 条 · harness scripts/gen_ref_candidates.py", ""]
    for r in records:
        lines.append(f"## {r['id']}（{r['cn']}）— {r.get('status')}（{r.get('elapsed_s', '?')}s）")
        lines.append(f"- sonar_types: {','.join(r['sonar_types'])}")
        if r.get("replay"):
            lines.append(f"- 沙箱重放: {'PASS' if r['replay']['ok'] else 'FAIL'}"
                         + (f"（error: {r['replay']['error'][:120]}）" if r['replay'].get("error") else ""))
        g = r.get("geometry") or {}
        if g.get("measured"):
            lines.append(f"- 实测: bbox={g.get('overall_bbox_mm')} volume={g.get('overall_volume_mm3')}mm³ "
                         f"parts={g.get('part_count')}")
            for p in g.get("parts", []):
                lines.append(f"  - {p.get('name')} volume={p.get('volume_mm3')}mm³ bbox={p.get('bbox_mm')}")
        elif r.get("replay", {}).get("ok"):
            lines.append(f"- 实测: 失败（{g.get('reason')}）——如实")
        if r.get("error"):
            lines.append(f"- 异常: {r['error']}")
        lines.append(f"- 脚本: docs/reviews/t164-candidates/{r['id']}.py · 明细: {r['id']}.report.json")
        lines.append("")
    lines.append("> 裁决点：每条候选是否「参数语义正确 + 几何合理 + 值得入库」由用户判定；")
    lines.append("> 入库动作 = manual_upload 上传 + 六件套核对 + confirm（PHASE-016 §3.9 checklist）。")
    (out_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("report:", out_dir / "REPORT.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
