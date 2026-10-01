# -*- coding: utf-8 -*-
"""LLM 前置内容键值脱敏（T11.6，设计 §9.3 纵深防御；宪法 §10 红线扩展到 LLM 出站内容）。

出站 prompt/消息在送供应商（含未来 vLLM 私有化实例）前做键值脱敏：
- 密钥/token 形态：sk- 前缀 key、Bearer 后随 token、api_key/token 赋值；
- 密码赋值：password/passwd 形态；
- 连接串：带凭据的 URL（user:pass@host）；
- .env 键值对形态（KEY 语义名 + 高熵值）。
命中替换为「[REDACTED:类别]」占位并记审计日志（只记类别与计数，不记原值）。

口径：**默认开启**（SANITIZE_LLM_CONTENT 缺省=1），关闭需显式 env=0——纵深防御
宁可多脱敏；脱敏对象是「出站内容」，供应商返回内容不再加工（响应侧无密钥回显面）。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger("sonarcad.llm.sanitize")

def _enabled() -> bool:
    return os.environ.get("SANITIZE_LLM_CONTENT", "1").strip().lower() not in ("0", "false", "no")

# 类别 → 出站文本中的形态（按序匹配，先专后泛）
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("api_key", re.compile(r"sk-[A-Za-z0-9_-]{12,}")),
    ("bearer_token", re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{12,}")),
    ("api_key", re.compile(r"(?i)(api[_-]?key|apikey)\s*[=:：]\s*[\"']?[A-Za-z0-9._/~+-]{8,}")),
    ("token", re.compile(r"(?i)\btoken\s*[=:：]\s*[\"']?[A-Za-z0-9._/~+-]{12,}")),
    ("password", re.compile(r"(?i)(password|passwd|pwd)\s*[=:：]\s*[\"']?[^\s\"']{4,}")),
    ("connection_url", re.compile(r"[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@[^\s]+", re.I)),
    ("env_pair", re.compile(
        r"(?im)^([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)S?)\s*=\s*(\S{8,})$")),
]

_PLACEHOLDER = "[REDACTED:{kind}]"


def sanitize_text(text: str) -> tuple[str, dict[str, int]]:
    """单段文本脱敏。返回 (脱敏后文本, {类别: 命中数})。"""
    hits: dict[str, int] = {}
    out = text
    for kind, pattern in PATTERNS:
        def _sub(m: re.Match[str], _kind: str = kind) -> str:
            hits[_kind] = hits.get(_kind, 0) + 1
            return _PLACEHOLDER.format(kind=_kind)
        out = pattern.sub(_sub, out)
    return out, hits


def sanitize_messages(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """chat() 出站消息整体脱敏（默认开启）。字符串值逐段处理，其他类型原样保留。

    审计：命中仅记类别与次数（不记原值，宪法 §10——日志不得含密钥）。
    """
    if not _enabled():
        return messages, {}
    total: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    changed = False
    for m in messages:
        new_m: dict[str, Any] = {}
        mutated = False
        for k, v in m.items():
            if isinstance(v, str):
                new_v, hits = sanitize_text(v)
                if hits:
                    mutated = True
                    for kind, n in hits.items():
                        total[kind] = total.get(kind, 0) + n
                new_m[k] = new_v
            else:
                new_m[k] = v
        out.append(new_m if mutated else m)
        changed = changed or mutated
    if total:
        logger.warning("LLM 出站内容脱敏命中：%s（已替换为占位符，原值不落日志）",
                       ", ".join(f"{k}×{v}" for k, v in sorted(total.items())))
    return out, total
