# -*- coding: utf-8 -*-
"""CadQuery 沙箱执行器（T1.5，宪法 §10.3 / A-15）。

v2 强制改造点（旧 executor 是进程内 exec，不符合宪法，已弃用其执行方式）：
- **子进程**：生成脚本在独立 python 进程中运行，主进程永不 import 用户代码；
- **独立工作目录**：每次执行新建临时目录，STEP 只可能落在其中；
- **执行时限**：硬超时杀进程（A-13 有界）；
- 命名空间预置与旧版一致（cq/math/os/Color/OUTPUT_DIR 等），脚本无需 import 即可运行。

子进程入口是本模块的 run_in_subprocess()；真正执行体在 _child_main()（同文件，python -c 调起）。
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("sonarcad.sandbox")

DEFAULT_TIME_LIMIT_S = 300  # 单次执行上限（A-13；可在 sandbox 运行配置调整）


_PRELUDE = r'''
import json, math, os, sys, time

def _child_main(script_path, output_dir):
    results = {"ok": False, "step_files": [], "error": ""}
    os.makedirs(output_dir, exist_ok=True)
    pre = {str(p): p.stat().st_mtime for p in Path(output_dir).glob("*.step")}

    import cadquery as cq
    namespace = {
        "cq": cq,
        "__builtins__": __builtins__,
        "math": math,
        "os": os,
        "cos": lambda d: math.cos(math.radians(d)),
        "sin": lambda d: math.sin(math.radians(d)),
        "pi": math.pi,
        "radians": math.radians,
        "sqrt": math.sqrt,
        "Color": cq.Color,
        "OUTPUT_DIR": output_dir,
    }
    try:
        code = Path(script_path).read_text(encoding="utf-8")
        exec(compile(code, script_path, "exec"), namespace)

        post = {str(p): p.stat().st_mtime for p in Path(output_dir).glob("*.step")}
        changed = [f for f, mt in post.items() if f not in pre or mt != pre[f]]
        changed.sort(key=lambda f: os.path.getmtime(f), reverse=True)
        if not changed and post:
            changed = sorted(post, key=lambda f: os.path.getmtime(f), reverse=True)[:1]

        if changed:
            results["ok"] = True
            results["step_files"] = changed
        else:
            # 后处理：脚本未导出 STEP 时尝试自动导出（迁移自旧 executor 的补救逻辑）
            export_obj = namespace.get("assy") if namespace.get("assy") is not None else namespace.get("result")
            if export_obj is not None:
                step_file = os.path.join(output_dir, "sonar_model.step")
                if isinstance(export_obj, cq.Assembly):
                    export_obj.save(step_file, "STEP")
                else:
                    assy = cq.Assembly()
                    assy.add(export_obj, name="sonar_model")
                    assy.save(step_file, "STEP")
                results["ok"] = True
                results["step_files"] = [step_file]
                results["auto_exported"] = True
            else:
                results["error"] = "执行完成但未生成 STEP（缺少 assy.save 导出或 assy/result 变量）"
    except Exception as e:
        results["error"] = f"{type(e).__name__}: {e}"
    print("__SANDBOX_RESULT__" + json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    from pathlib import Path
    _child_main(sys.argv[1], sys.argv[2])
'''


def run_in_subprocess(code: str, time_limit_s: int = DEFAULT_TIME_LIMIT_S) -> dict:
    """在隔离子进程中执行生成脚本。

    返回 {"ok": bool, "step_files": [Path...], "error": str, "workdir": Path, "auto_exported": bool}。
    超时/崩溃如实返回 error（A-13/A-16），不抛出——由调用方决定修复或失败。
    """
    workdir = Path(tempfile.mkdtemp(prefix="sonarcad_sandbox_"))
    script_path = workdir / "generated_script.py"
    output_dir = workdir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    script_path.write_text(code, encoding="utf-8")

    try:
        proc = subprocess.run(
            [sys.executable, "-c", _PRELUDE, str(script_path), str(output_dir)],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=time_limit_s,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "step_files": [],
            "error": f"执行超时（>{time_limit_s}s），已强制终止",
            "workdir": workdir,
            "auto_exported": False,
        }

    marker = "__SANDBOX_RESULT__"
    result: dict = {"ok": False, "step_files": [], "error": "", "auto_exported": False}
    for line in (proc.stdout or "").splitlines():
        if line.startswith(marker):
            result = json.loads(line[len(marker):])
            break
    if not result["ok"] and not result["error"]:
        # 子进程本身崩溃（如 import 失败/OOM）：stderr 尾部即根因
        tail = (proc.stderr or "").strip().splitlines()
        result["error"] = tail[-1] if tail else f"子进程退出码 {proc.returncode}"

    result["step_files"] = [Path(f) for f in result.get("step_files", [])]
    result["workdir"] = workdir
    return result
