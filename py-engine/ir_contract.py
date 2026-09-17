"""IR 契约校验（T1.3，A-04）。

`contracts/ir/*.schema.json` 是单一事实源；py-engine 侧用 jsonschema 直接校验，
保证产出/消费的 IR 与契约不漂移。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import jsonschema

CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "contracts" / "ir"

# 各 IR 当前契约版本（快照 schema_version 字段用；契约演进时在此升版）
SCHEMA_VERSION = "v1"


@lru_cache(maxsize=None)
def load_schema(ir_type: str) -> dict:
    """加载 IR 契约（ir_type 形如 'IR0'..'IR5'）。"""
    path = CONTRACTS_DIR / f"{ir_type.lower()}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


class IrContractError(ValueError):
    """IR 不符合契约。"""


def validate_ir(ir_type: str, data: dict) -> dict:
    """校验 IR 数据；不合法抛 IrContractError（含可读错误信息）。"""
    schema = load_schema(ir_type)
    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "(root)"
        raise IrContractError(f"{ir_type} 契约校验失败 @{where}: {exc.message}") from exc
    return data
