# -*- coding: utf-8 -*-
"""生成代码多层校验（T1.5，迁移自旧 codegen_stage ★★内脏复用）。

校验链：空内容 → 语法 → 完整性（关键标记）→ 建模模式诊断（仅警告）→ 自动修复（sin²）。
规则内容与旧版一致；对外暴露纯函数便于单测。

T16.0 A0-3/A0-5（PHASE-016）：领域诊断/自动修复规则集参与 codegen 缓存指纹——
规则文本变更必须递增 DOMAIN_RULES_VERSION（同 IR2 换规则必须 miss）。A0-5 将
领域规则收敛入注册表并升版。
"""

from __future__ import annotations

import re
from typing import Any

# 领域规则版本（A0-3 缓存指纹组件）：v1=拆分前单块规则；v2=A0-5 注册表化
DOMAIN_RULES_VERSION = "2"

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


def _check_towed_array_patterns(code: str) -> list[str]:
    """拖曳阵领域模式诊断（仅警告，不拒绝）：sin²、柔性部件直线建模、关键配色。
    （A0-5：自 validate_critical_patterns 收敛入领域注册表，仅对声明领域执行。）"""
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


def _fix_color_range(code: str) -> str:
    """「Standard_OutOfRange: Color out」的确定性修复（2026-09-22 e2e 实测：LLM 连续
    3 轮把 RGB 写成 0–255，修复循环无法自愈）。cadquery/OCCT 要求分量在 0–1：
    把 Color(...) 中任一数值分量 >1 的调用整体 ÷255；命名色与已是 0–1 的不动。
    颜色纯视觉，不影响几何与 STEP 有效性。"""
    def _scale(m: "re.Match[str]") -> str:
        vals = [v.strip() for v in m.group(1).split(",")]
        try:
            nums = [float(v) for v in vals[:3]]
        except ValueError:
            return m.group(0)  # 命名色等非数值参数
        if len(nums) == 3 and any(v > 1.0 for v in nums):
            scaled = ", ".join(f"{v / 255.0:.4f}" for v in nums)
            rest = ", " + ", ".join(vals[3:]) if len(vals) > 3 else ""
            return f"Color({scaled}{rest})"
        return m.group(0)

    return re.sub(r"\bColor\(([^()]*)\)", _scale, code)


def _fix_towed_array_patterns(code: str) -> str:
    """拖曳阵领域自动修复：弯曲轨迹 sin() → sin()**2（保证端点斜率为零）。
    （A0-5：自 auto_fix_patterns 收敛入领域注册表；RGB 修复属通用层保留。）"""
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


# 领域注册表（A0-5，catalog 条目 domain 字段声明适用范围；新领域在此注册，未注册领域不执行）
DOMAIN_CHECKS: dict[str, Any] = {"towed_array": _check_towed_array_patterns}
DOMAIN_FIXES: dict[str, Any] = {"towed_array": _fix_towed_array_patterns}


def validate_critical_patterns(code: str, domain: str | None = None) -> list[str]:
    """领域模式诊断（仅警告）：仅当声明领域且已注册规则时执行（A0-5 边界——
    其他类型不误用拖曳阵强制建模模式；未注册领域如实无领域诊断）。"""
    check = DOMAIN_CHECKS.get(domain) if domain else None
    return check(code) if check else []


def auto_fix_patterns(code: str, domain: str | None = None) -> str:
    """通用修复（RGB 0–255 → 0–1，颜色类错误不消耗修复轮次）+ 领域修复（仅注册领域）。"""
    code = _fix_color_range(code)
    fix = DOMAIN_FIXES.get(domain) if domain else None
    return fix(code) if fix else code


def validate_chain(code: str, label: str = "main", domain: str | None = None) -> tuple[str, bool, list[str], list[str]]:
    """完整校验链。返回 (code, ok, errors, warnings)。domain=catalog 声明的适用领域
    （A0-5）：仅影响领域诊断/修复层，语法/完整性/配色修复对任何领域恒执行。"""
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

    warnings = validate_critical_patterns(code, domain)
    code = auto_fix_patterns(code, domain)
    return code, not errors, errors, warnings
