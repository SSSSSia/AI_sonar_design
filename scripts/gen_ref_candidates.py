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
import os
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
            "设计一个侧扫声呐拖鱼（towfish）。构造配方（严格照做，只用 circle/extrude/workplane/loft/union/cut，"
            "禁止 revolve、mirror、transformed、fillet、chamfer、pushPoints 和单参数 loft）：\n"
            "1) 主体三段沿 X 轴首尾相接【串联外延，段与段不得互相重叠】：圆柱耐压段居中"
            "（circle(R_HULL).extrude(L_HULL)，X 从 0 到 L_HULL）；首锥从圆柱前端面向 +X 外延伸出"
            "（workplane(offset=L_HULL) 起 circle(R_HULL)，offset 再加 L_NOSE 处 circle(R_NOSE_TIP)，"
            "loft(combine=False)——小端朝外）；尾锥从圆柱尾端面向 -X 外延伸出（origin=(-L_TAIL,0,0) 处"
            "circle(R_TAIL_TIP) 起，offset=L_TAIL 处 circle(R_HULL)，loft(combine=False)——小端朝 -X 外）。\n"
            "2) 稳定翼=单块薄板箱体沿 Y 轴横穿主体：cq.Workplane('XY').box(C_FIN, SPAN_FIN*2, T_FIN,"
            " centered=(True, True, False)) 平移到主体中部（板中心过轴线）后 union——板横穿圆柱自然体积重叠。\n"
            "3) 拖曳环=短空心圆柱（circle(R_RING_OD)+circle(R_RING_ID) 同面 extrude）沿 X 轴放在尾锥小端"
            "外侧，并向尾锥方向多挤出一段嵌入尾锥（体积重叠），union。\n"
            "4) 唯一允许的段间重叠=翼板贯穿主体、环嵌入尾锥；三段主体必须纯串联（总长=L_NOSE+L_HULL+L_TAIL）。\n"
            "5) 下方骨架已验证可运行且几何正确（单一连通实体）。要求：以骨架为基础做【最小修改】输出完整脚本——"
            "仅允许 (a)常量重命名为物理语义名 (b)补充每个常量的中文注释 (c)微调尺寸常量值（主体耐压段长约 1200mm、"
            "直径约 200mm 量级；几何结构与构造顺序一字不改）。禁止改动 workplane/origin/offset/extrude/loft/union"
            " 的任何参数与顺序：\n"
            "```python\n"
            "import cadquery as cq\n"
            "R_HULL = 100.0      # 主体半径\n"
            "L_HULL = 1200.0     # 耐压段长\n"
            "L_NOSE = 150.0      # 首锥长\n"
            "L_TAIL = 100.0      # 尾锥长\n"
            "R_NOSE_TIP = 10.0   # 首端小端半径\n"
            "R_TAIL_TIP = 15.0   # 尾端小端半径\n"
            "C_FIN, SPAN_FIN, T_FIN = 400.0, 150.0, 10.0   # 翼弦/单侧翼展/翼厚\n"
            "R_RING_OD, R_RING_ID, W_RING, EMBED = 40.0, 25.0, 15.0, 20.0  # 环外径/内径/环宽/嵌入段\n"
            "nose = cq.Workplane('YZ').workplane(offset=L_HULL).circle(R_HULL).workplane(offset=L_NOSE).circle(R_NOSE_TIP).loft(combine=False)\n"
            "cyl = cq.Workplane('YZ').circle(R_HULL).extrude(L_HULL)\n"
            "tail = cq.Workplane('YZ').workplane(offset=-L_TAIL).circle(R_TAIL_TIP).workplane(offset=L_TAIL).circle(R_HULL).loft(combine=False)\n"
            "hull = nose.union(cyl).union(tail)\n"
            "fin = cq.Workplane('XY').box(C_FIN, SPAN_FIN*2, T_FIN, centered=(True, True, False)).translate((L_HULL*0.6, 0, -T_FIN/2))\n"
            "ring = cq.Workplane('YZ').workplane(offset=-L_TAIL - W_RING + EMBED).circle(R_RING_OD).circle(R_RING_ID).extrude(W_RING + EMBED)\n"
            "result = hull.union(fin).union(ring)\n"
            "```\n"
            "（骨架仅示意构造方式，尺寸/朝向细节以你的常量与设计文档为准；确保最终 result 单一连通实体。）\n"
            "顶部用 # 注释声明每个参数的物理含义；主体耐压段长度与含锥段总长分别用两个常量声明。"
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

Common CadQuery pitfalls to AVOID (each of these has produced wrong geometry in practice):
- `pushPoints` REPLACES the pending point stack. Build the complete list of points first and
  call `pushPoints(...)` ONCE with all of them; never call it inside a loop to add points
  one at a time (only the last call survives).
- `workplane(offset=N)` moves along the CURRENT plane normal, and `transformed(rotate=...)`
  changes that normal. For features that run along the body axis, keep the sketch plane
  normal aligned with that axis; verify every offset direction before extruding/lofting.
- Every auxiliary feature (fins, rings, handles) must be boolean-combined (`union`) with the
  main body so the final `result` is ONE connected solid — never leave features floating
  detached from the body.
- Two solids that only TOUCH at a face or an edge do NOT fuse: `union` of tangent solids
  still yields multiple separate solids. Every attached feature must geometrically OVERLAP
  the body (embed its root at least one wall thickness into the body) so the union fuses.
- The named constants must map one-to-one to the required dimensions; do not invent extra
  orientation axes that change the stated bounding dimensions.
"""


_RENDER_CODE = r'''
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import cadquery as cq

step_path, out_png = sys.argv[1], sys.argv[2]
shape = cq.importers.importStep(step_path).val()
verts, tris = shape.tessellate(2.0)
V = np.array([(v.x, v.y, v.z) for v in verts])
F = np.array(tris)
tri = V[F]
n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
norm = np.linalg.norm(n, axis=1, keepdims=True)
norm[norm == 0] = 1.0
n = n / norm
light = np.array([0.4, 0.35, 0.85])
light = light / np.linalg.norm(light)
intensity = 0.35 + 0.65 * np.clip(n @ light, 0.0, 1.0)
base = np.array([0.39, 0.58, 0.93])
colors = np.clip(intensity[:, None] * base[None, :], 0.0, 1.0)

views = [(22, -60, "iso"), (0, -90, "side"), (90, -90, "top")]
fig = plt.figure(figsize=(15, 5))
center = (V.max(axis=0) + V.min(axis=0)) / 2.0
span = float(np.max(V.max(axis=0) - V.min(axis=0))) or 1.0
for k, (elev, azim, label) in enumerate(views, 1):
    ax = fig.add_subplot(1, 3, k, projection="3d")
    ax.add_collection3d(Poly3DCollection(tri, facecolors=colors, edgecolors="none"))
    ax.set_xlim(center[0] - span / 2, center[0] + span / 2)
    ax.set_ylim(center[1] - span / 2, center[1] + span / 2)
    ax.set_zlim(center[2] - span / 2, center[2] + span / 2)
    ax.set_box_aspect((1, 1, 1))
    ax.view_init(elev=elev, azim=azim)
    ax.set_title(label)
    ax.axis("off")
plt.tight_layout()
plt.savefig(out_png, dpi=110)
print("rendered", out_png)
'''


def _render_views(step_file, out_png) -> dict:
    """三视图渲染（子进程：重镶嵌纪律，convert_worker/geometry_inspect 同款）。失败如实返回不抛。"""
    import subprocess

    try:
        proc = subprocess.run(
            [sys.executable, "-c", _RENDER_CODE, str(step_file), str(out_png)],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0 or "rendered" not in (proc.stdout or ""):
            return {"ok": False, "error": (proc.stderr or proc.stdout or "")[-300:]}
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


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


# 连通性两两核对上限（ADR-018 决策 1 新增必检项）：超过只报数量不判连通（组合爆炸，如实 N/A）
_MAX_PAIRWISE_SOLIDS = 12


def _connectivity(step_file) -> dict:
    """连通性实测（2026-11-26 裁决新增入库必检项，ADR-018）：实体数/有效性/两两重叠建图
    判连通分量。教训=拖鱼 v2 翼板与主体仅线相切、拖曳环悬空——重放 PASS+bbox/体积证据
    不测「部件之间连没连」（docs/reviews/t164-candidates/REPORT.md 裁决记录节）。
    connected 判据=isValid 且全部实体在同一个重叠连通分量（union-find）；
    min_pair_overlap_ratio=相连实体对的最小重叠体积比（对较小实体体积归一）。"""
    out: dict = {"measured": False}
    try:
        import tempfile

        import cadquery as cq

        data = Path(step_file).read_bytes()
        with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as f:
            f.write(data)
            tmp = f.name
        try:
            shape = cq.importers.importStep(tmp).val()
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        solids = shape.Solids()  # cadquery 2.x 直接返回 list（T17.5 实测）
        out["solids"] = len(solids)
        out["valid"] = bool(shape.isValid())
        out["measured"] = True
        if len(solids) > _MAX_PAIRWISE_SOLIDS:
            out["connected"] = None  # 超上限不判（A-16 如实 N/A）
            out["reason"] = f"solids={len(solids)} 超两两核对上限 {_MAX_PAIRWISE_SOLIDS}"
            return out
        parent = list(range(len(solids)))

        def _find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        vols = [s.Volume() for s in solids]
        ratios: list[float] = []
        for i in range(len(solids)):
            for j in range(i + 1, len(solids)):
                try:
                    inter = solids[i].intersect(solids[j])
                    ov = sum(s.Volume() for s in inter.Solids()) if inter.Solids() else 0.0
                except Exception:  # noqa: BLE001 - 不相交/布尔失败按零重叠（如实）
                    ov = 0.0
                if ov > 1e-6:
                    parent[_find(i)] = _find(j)
                    ratios.append(ov / (min(vols[i], vols[j]) or 1.0))
        components = len({_find(i) for i in range(len(solids))})
        out["components"] = components
        out["min_pair_overlap_ratio"] = round(min(ratios), 6) if ratios else None
        out["connected"] = components == 1 and out["valid"]
        return out
    except Exception as exc:  # noqa: BLE001 - 实测失败如实不抛（harness 纪律）
        out["reason"] = str(exc)[:200]
        return out


def run_one(spec: dict, out_dir: Path, timeout_s: int, suffix: str = "", render: bool = False) -> dict:
    t0 = time.perf_counter()
    sfx = f"_{suffix}" if suffix else ""
    rec: dict = {
        "id": spec["id"],
        "suffix": suffix,
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
        rec["connectivity"] = _connectivity(replay["step_files"][0])
        if render:
            pv = _render_views(replay["step_files"][0], out_dir / f"{spec['id']}{sfx}_views.png")
            rec["preview"] = {"ok": pv["ok"], "file": f"{spec['id']}{sfx}_views.png"} if pv["ok"] \
                else {"ok": False, "error": pv["error"]}
    (out_dir / f"{spec['id']}{sfx}.py").write_text(code, encoding="utf-8")
    if not replay["ok"]:
        rec["status"] = "replay_failed"
    elif (rec.get("connectivity") or {}).get("connected") is False:
        # ADR-018 决策 1：连通性必检不过=不入库候选（如实落状态，不冒充 candidate_ready）
        rec["status"] = "candidate_rejected_disconnected"
    else:
        rec["status"] = "candidate_ready"
    rec["elapsed_s"] = round(time.perf_counter() - t0, 1)
    return rec


def rec_batch() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="T16.4 路径③候选生成（候选≠范例，入库须人工确认）")
    ap.add_argument("--only", help="只跑指定类型 id", default=None)
    ap.add_argument("--out-dir", default=str(_ROOT / "docs" / "reviews" / "t164-candidates"))
    ap.add_argument("--timeout-s", type=int, default=300)
    ap.add_argument("--suffix", help="输出文件名后缀（如 v2；留空覆盖 v1 命名）", default="")
    ap.add_argument("--render", action="store_true", help="重放成功后出三视图 PNG（matplotlib 子进程）")
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = [s for s in TYPE_SPECS if not args.only or s["id"] == args.only]
    if not specs:
        print("no matching type spec; available:", ", ".join(s["id"] for s in TYPE_SPECS))
        return 2

    records = []
    sfx = f"_{args.suffix}" if args.suffix else ""
    for spec in specs:
        print(f"[gen] {spec['id']} ({spec['cn']}) …", flush=True)
        rec = run_one(spec, out_dir, args.timeout_s, suffix=args.suffix, render=args.render)
        records.append(rec)
        print(f"    -> {rec.get('status')} ({rec.get('elapsed_s', '?')}s)", flush=True)
        (out_dir / f"{spec['id']}{sfx}.report.json").write_text(
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
        c = r.get("connectivity") or {}
        if c.get("measured"):
            conn = c.get("connected")
            conn_txt = "连通" if conn else ("N/A（超上限不判）" if conn is None else "不连通")
            lines.append(f"- 连通性: solids={c.get('solids')} valid={c.get('valid')} → {conn_txt}"
                         + (f"（min_overlap_ratio={c.get('min_pair_overlap_ratio')}）"
                            if c.get("min_pair_overlap_ratio") is not None else ""))
        pv = r.get("preview") or {}
        if pv.get("ok"):
            lines.append(f"- 三视图: {pv.get('file')}")
        elif pv.get("error"):
            lines.append(f"- 三视图: 渲染失败（{str(pv.get('error'))[:100]}）——如实")
        if r.get("error"):
            lines.append(f"- 异常: {r['error']}")
        lines.append(f"- 脚本: docs/reviews/t164-candidates/{r['id']}{sfx}.py · 明细: {r['id']}{sfx}.report.json")
        lines.append("")
    lines.append("> 裁决点：每条候选是否「参数语义正确 + 几何合理 + 值得入库」由用户判定；")
    lines.append("> 入库动作 = manual_upload 上传 + 六件套核对 + confirm（PHASE-016 §3.9 checklist）。")
    report_name = f"REPORT_{args.suffix}.md" if args.suffix else "REPORT.md"
    (out_dir / report_name).write_text("\n".join(lines), encoding="utf-8")
    print("report:", out_dir / report_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
