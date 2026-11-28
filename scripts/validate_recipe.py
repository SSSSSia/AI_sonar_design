# -*- coding: utf-8 -*-
"""T19.1 配方抽样验证 CLI（ADR-019 §4 判据 1 执行入口）。

本地配方文件 → 逐采样组（SAMPLES）沙箱执行+连通必检+几何实测+bbox 声明比对 →
validation-report.json 落盘。纯本地 OCCT，底座无需在线。

用法（仓库根）：
    python scripts/validate_recipe.py                       # 默认拖鱼配方三组采样
    python scripts/validate_recipe.py --recipe <path> --render
    python scripts/validate_recipe.py --out-dir docs/reviews/t191-recipe
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "py-engine"))

from cad_engine import recipe as recipe_mod  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="参数化配方抽样验证（沙箱+连通必检+bbox 声明比对）")
    ap.add_argument("--recipe", default=str(_ROOT / "scripts" / "recipes" / "towfish_recipe.py"))
    ap.add_argument("--out-dir", default=str(_ROOT / "docs" / "reviews" / "t191-recipe"))
    ap.add_argument("--render", action="store_true", help="对通过采样组出三视图 PNG（复用 harness 渲染）")
    ap.add_argument("--time-limit-s", type=int, default=180)
    args = ap.parse_args(argv)

    report = recipe_mod.sample_validate(args.recipe, time_limit_s=args.time_limit_s)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "validation-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    for s in report.get("samples", []):
        conn = s.get("connectivity") or {}
        bbox = s.get("bbox_check") or {}
        print(f"[{s.get('name')}] pass={s.get('pass')} sandbox={s.get('sandbox_ok')} "
              f"connected={conn.get('connected')} solids={conn.get('solids')} "
              f"min_ratio={conn.get('min_pair_overlap_ratio')} bbox: {bbox.get('detail')} "
              f"{(s.get('error') or '')[:120]}", flush=True)
        if args.render and s.get("pass") and s.get("step_file"):
            spec = importlib.util.spec_from_file_location(
                "grc", _ROOT / "scripts" / "gen_ref_candidates.py")
            grc = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(grc)
            png = out_dir / f"{s.get('name')}_views.png"
            pv = grc._render_views(s["step_file"], png)
            print(f"    render: {'OK → ' + str(png) if pv.get('ok') else 'FAIL ' + str(pv.get('error'))[:100]}",
                  flush=True)

    print(f"all_pass={report.get('all_pass')}  report → {report_path}")
    return 0 if report.get("all_pass") else 1


if __name__ == "__main__":
    sys.exit(main())
