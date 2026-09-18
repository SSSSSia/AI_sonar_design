"""军标条目核对框架（T2.1 占位 → T3.3 业务库接线）。

A-16：军标库为空时必须如实输出"待补"，禁止伪装成已核对。
核对条目两个来源：内存 REGISTRY_STANDARDS（带 check 函数的自动化规则）+
业务库 kb_standards（V7 建表；T3.3 管理面录入，check=None——以要求描述做提示性核对）。
阶段 3 军标库管理（T3.3）上线后，条目迁入业务库；本注册表是引擎侧装载点。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class StandardSpec:
    """一条军标核对规则。

    code: 军标编号（如 "GJB 585"）；applies_to: 参数名包含匹配（结构化匹配待 T3.3 后升级）；
    check: (param) -> str | None，返回违例说明；None 表示仅登记（提示性核对）。
    """

    code: str
    name: str
    applies_to: str
    check: Callable[[dict[str, Any]], str | None] | None = None


# 引擎侧内存规则（自动化核对）；业务库条目经 load_enabled_standards 叠加
REGISTRY_STANDARDS: list[StandardSpec] = []

# kb_standards 库内条目缓存（T3.3；库不可达/为空→[]，维持"待补"诚实语义）
_db_cache: tuple[float, list[StandardSpec]] | None = None
_DB_TTL_S = 30


def load_enabled_standards(max_age_s: int = _DB_TTL_S) -> list[StandardSpec]:
    """读业务库 kb_standards（enabled）→ StandardSpec（check=None）。

    库不可达/为空返回 []；失败也写缓存（TTL 内不重复打库）。
    """
    global _db_cache
    import time as _time

    now = _time.time()
    if _db_cache is not None and now - _db_cache[0] < max_age_s:
        return _db_cache[1]
    specs: list[StandardSpec] = []
    try:
        from storage.registry_store import list_rows_cached

        for row in list_rows_cached("standards"):
            specs.append(StandardSpec(
                code=str(row["code"]),
                name=str(row["name"]),
                applies_to=str(row.get("applies_to") or ""),
                check=None,  # 库内条目以要求描述做提示性核对（自动核对逻辑待实现时如实说明）
            ))
    except Exception:
        specs = []
    _db_cache = (now, specs)
    return specs


def reset_cache() -> None:
    """业务库变更后清缓存（管理 API 调用）。"""
    global _db_cache
    _db_cache = None


def _all_standards() -> list[StandardSpec]:
    return REGISTRY_STANDARDS + load_enabled_standards()


def check_param(param: dict[str, Any]) -> dict[str, Any]:
    """对单个 source_type=standard 的参数给出核对结论。

    返回 {status, message, standards?}：
    - status="pass"：命中条目且 check 通过
    - status="violation"：命中条目且 check 给出违例说明
    - status="pending"：无可自动核对的条目（库为空/仅登记未实现核对）——A-16 如实待补
    """
    name = param.get("name") or ""
    matched = [s for s in _all_standards() if s.applies_to in name]
    if not matched:
        return {
            "status": "pending",
            "message": (
                f"军标核对待补：军标库暂无覆盖「{name}」的条目"
                f"（当前库共 {len(_all_standards())} 条），无法自动核对"
            ),
        }
    violations: list[str] = []
    registered: list[str] = []
    for spec in matched:
        if spec.check is None:
            registered.append(f"{spec.code}《{spec.name}》已登记，自动核对逻辑待实现")
            continue
        issue = spec.check(param)
        if issue:
            violations.append(f"{spec.code}《{spec.name}》：{issue}")
    if violations:
        return {"status": "violation", "message": "；".join(violations),
                "standards": [s.code for s in matched]}
    if registered:
        return {"status": "pending", "message": f"参数「{name}」：" + "；".join(registered)}
    return {"status": "pass", "message": f"已按 {', '.join(s.code for s in matched)} 核对通过"}
