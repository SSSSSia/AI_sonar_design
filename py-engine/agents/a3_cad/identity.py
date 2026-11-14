# -*- coding: utf-8 -*-
"""A3 对象身份与显示变换声明解析（T15.1/T15.2，ADR-014）。

契约来源：ADR-014 §1/§2/§3——A3 生成的 CadQuery 脚本内嵌声明注释，随脚本缓存/
版本化（A0-3 envelope 整体缓存，无需额外 LLM 调用）；解析在**最终脚本**上执行
（修复循环可能改写脚本，中途注释丢失则以最终态为准，如实降级为无声明=legacy）。

语法（B-1/B-2，通用层，任何领域可用）：
    # @obj <object_id> role=<角色> [params=<参数id:尺寸类型,...>]
    # @display param=<参数id|none> physical_mm=<物理值mm> visual_mm=<视觉值mm> [rule=<规则>] [reason=<说明>]

- 键值内不得含空白（rule/reason 用紧凑表达式或下划线）。
- 畸形行（缺键/非法数值）跳过并 warning，不阻断管线；无分数不造值（A-16）。
- LLM 声明只是意图不是真值：@obj 声明须经 build_param_mapping 与实测几何互证后
  才能成为 verified 映射（T15.2）；@display 声明落 IR3 display_transforms 由 A4
  显性呈现（CHK-DISPLAY-TRANSFORM），不再静默缩小工程参数（A-16）。
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("sonarcad.a3.identity")

# 行头：# @display ...
_DISPLAY_LINE_RE = re.compile(r"^\s*#\s*@display\b(?P<body>.*)$")
# 行头：# @obj <object_id> ...（T15.2 使用）
_OBJ_LINE_RE = re.compile(r"^\s*#\s*@obj\b\s+(?P<oid>[^\s]+)(?P<body>.*)$")
# 键值对：key=value（value 不含空白与 #）
_KV_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s#]+)")
# params 值：param_id:dim_type 逗号分隔（T15.2 使用）
_PARAM_SPEC_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_\-\.]*):([a-z_]+)$")

REQUIRED_DISPLAY_KEYS = ("physical_mm", "visual_mm")


def parse_display_transforms(script: str) -> list[dict[str, Any]]:
    """解析脚本内全部 ``# @display`` 声明 → IR3 display_transforms 条目列表。

    - param=none 或缺省 → param_id=None（纯视觉说明，无对应 IR2 参数）。
    - physical_mm/visual_mm 必需且必须可解析为有限正/负数值（振幅可为负）；
      违规行整行跳过并 warning（A-16：缺数据不造值，不产出半截契约对象）。
    - 同一参数多条声明全部保留（如实呈现，不去重合并——多重缩放本身即异常信号，
      由审阅人工裁决）。
    """
    out: list[dict[str, Any]] = []
    for lineno, line in enumerate((script or "").splitlines(), 1):
        m = _DISPLAY_LINE_RE.match(line)
        if not m:
            continue
        kv = dict(_KV_RE.findall(m.group("body")))
        missing = [k for k in REQUIRED_DISPLAY_KEYS if k not in kv]
        try:
            physical = float(kv.get("physical_mm", ""))
            visual = float(kv.get("visual_mm", ""))
        except ValueError:
            logger.warning(
                "A3 @display 声明第 %d 行数值非法（缺：%s），整行跳过: %s",
                lineno, ",".join(missing) or "number", line.strip(),
            )
            continue
        entry: dict[str, Any] = {"param_id": None, "physical_mm": physical, "visual_mm": visual}
        pid = kv.get("param")
        if pid and pid.lower() != "none":
            entry["param_id"] = pid
        if kv.get("rule"):
            entry["rule"] = kv["rule"]
        if kv.get("reason"):
            entry["reason"] = kv["reason"]
        out.append(entry)
    return out
