# -*- coding: utf-8 -*-
"""生成代码多层校验（T1.5，迁移自旧 codegen_stage ★★内脏复用）。

校验链：空内容 → 语法 → 完整性（关键标记）→ 建模模式诊断（仅警告）→ 自动修复（sin²）。
规则内容与旧版一致；对外暴露纯函数便于单测。
"""

from __future__ import annotations

import re

REQUIRED_MARKERS = {
    "assy": "Assembly 变量 (assy = cq.Assembly())",
    "Assembly": "Assembly 类使用",
    ".save(": "STEP 导出调用 (assy.save(...))",
    ".step": "STEP 文件扩展名",
    "cq.Workplane": "CadQuery 几何创建",
}


def validate_syntax(code: str) -> tuple[bool, str]:
    try:
        compile(code, "<generated_script>", "exec")
        return True, ""
    except SyntaxError as e:
        return False, f"Line {e.lineno}: {e.msg}"


def validate_completeness(code: str) -> tuple[bool, list[str]]:
    missing = [desc for marker, desc in REQUIRED_MARKERS.items() if marker not in code]
    return len(missing) == 0, missing


def validate_critical_patterns(code: str) -> list[str]:
    """关键建模模式诊断（仅警告，不拒绝）：sin²、柔性部件直线建模、关键配色。"""
    warnings: list[str] = []

    if "get_spline_pts" in code:
        fstart = code.find("def get_spline_pts")
        next_def = code.find("\ndef ", fstart + 5)
        body = code[fstart:next_def] if next_def > 0 else code[fstart:]
        if "math.sin" in body and "** 2" not in body:
            warnings.append(
                "CRITICAL: get_spline_pts 使用 sin() 而非 sin()**2，弯曲轨迹两端斜率不为零"
            )

    flexible = [("build_cable", "传输缆"), ("build_damping", "减振段")]
    for func_name, part_name in flexible:
        if func_name in code:
            fstart = code.find(f"def {func_name}")
            if fstart >= 0:
                fnext = code.find("\ndef ", fstart + 5)
                fbody = code[fstart:fnext] if fnext > 0 else code[fstart:]
                if ".extrude(" in fbody and "sweep" not in fbody and "spline" not in fbody:
                    warnings.append(f"CRITICAL: {func_name}（{part_name}）应使用 spline+sweep 曲线建模")

    for color_var, part_name, expected in (
        ("C_CABLE", "传输缆", "红色系"),
        ("C_DAMPING", "减振段", "绿色系"),
    ):
        m = re.search(rf"{color_var}\s*=\s*Color\(([^)]+)\)", code)
        if m:
            vals = [v.strip() for v in m.group(1).split(",")]
            try:
                r, g, b = float(vals[0]), float(vals[1]), float(vals[2])
                if r < 0.3 and g < 0.3 and b < 0.3:
                    warnings.append(f"CRITICAL: {part_name}配色为黑色/深灰，应为{expected}")
            except (ValueError, IndexError):
                pass
    return warnings


def auto_fix_patterns(code: str) -> str:
    """自动修复已知问题：弯曲轨迹 sin() → sin()**2（保证端点斜率为零）。"""
    marker = "def get_spline_pts"
    start = code.find(marker)
    if start < 0:
        return code
    next_def = code.find("\ndef ", start + 5)
    end = next_def if next_def > 0 else len(code)
    body = code[start:end]
    if "math.sin" in body and "** 2" not in body and "**2" not in body:
        fixed = re.sub(r"math\.sin(\([^)]+\))", r"math.sin\1 ** 2", body)
        code = code[:start] + fixed + code[end:]
    return code


def validate_chain(code: str, label: str = "main") -> tuple[str, bool, list[str], list[str]]:
    """完整校验链。返回 (code, ok, errors, warnings)。"""
    errors: list[str] = []
    warnings: list[str] = []

    if not code.strip():
        return code, False, [f"[{label}] LLM 返回空内容"], warnings

    ok, err = validate_syntax(code)
    if not ok:
        errors.append(f"[{label}] 语法错误: {err}")

    complete, missing = validate_completeness(code)
    if not complete:
        errors.append(f"[{label}] 缺少: {', '.join(missing)}")

    warnings = validate_critical_patterns(code)
    code = auto_fix_patterns(code)
    return code, not errors, errors, warnings
