"""军标条目核对框架（T2.1 占位，阶段 3 首样入库）。

A-16：军标库为空时必须如实输出"待补"，禁止伪装成已核对。
阶段 3 军标库管理（T3.3）上线后，条目迁入业务库；本注册表是引擎侧装载点——
每条 StandardSpec 声明适用的参数匹配规则与核对函数，A4 逐 standard 参数调用 check_param。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class StandardSpec:
    """一条军标核对规则。

    code: 军标编号（如 "GJB 585"）；applies_to: 参数名包含匹配（阶段 3 升级为结构化匹配）；
    check: (param) -> str | None，返回违例说明；None 表示核对通过。
    """

    code: str
    name: str
    applies_to: str
    check: Callable[[dict[str, Any]], str | None] | None = None


# 阶段 3 首样入库前为空——check_param 必须如实报"待补"，不得伪装核对
REGISTRY_STANDARDS: list[StandardSpec] = []


def check_param(param: dict[str, Any]) -> dict[str, Any]:
    """对单个 source_type=standard 的参数给出核对结论。

    返回 {status, message, standards?}：
    - status="pass"：命中条目且 check 通过
    - status="violation"：命中条目且 check 给出违例说明
    - status="pending"：无条目可核对（军标库为空/未覆盖该参数）——A-16 如实待补
    """
    name = param.get("name") or ""
    matched = [s for s in REGISTRY_STANDARDS if s.applies_to in name]
    if not matched:
        return {
            "status": "pending",
            "message": (
                f"军标核对待补：军标库暂无覆盖「{name}」的条目"
                f"（当前库共 {len(REGISTRY_STANDARDS)} 条），无法自动核对"
            ),
        }
    violations: list[str] = []
    for spec in matched:
        if spec.check is None:
            violations.append(f"{spec.code}《{spec.name}》已登记，核对逻辑待实现")
            continue
        issue = spec.check(param)
        if issue:
            violations.append(f"{spec.code}《{spec.name}》：{issue}")
    if violations:
        return {"status": "violation", "message": "；".join(violations), "standards": [s.code for s in matched]}
    return {"status": "pass", "message": f"已按 {', '.join(s.code for s in matched)} 核对通过"}
