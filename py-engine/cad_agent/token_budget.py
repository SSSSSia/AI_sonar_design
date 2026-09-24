"""Token 预算管理（T8.2，迁移自 CadAgent core/token_budget.py，去 core.config 依赖改为参数注入）。

中英混合 token 估算 + 三阶段消息裁剪（截断 / 原子删对 / 摘要替换），
防止多轮微调历史撑爆上下文窗口。
"""

from __future__ import annotations

import json


def _is_cjk(cp: int) -> bool:
    return (0x4E00 <= cp <= 0x9FFF      # CJK 统一表意文字
            or 0x3400 <= cp <= 0x4DBF   # 扩展 A
            or 0xF900 <= cp <= 0xFAFF   # 兼容表意文字
            or 0x3000 <= cp <= 0x303F   # CJK 符号标点
            or 0xFF00 <= cp <= 0xFFEF)  # 半全角形式


def estimate_tokens(text: str) -> int:
    """粗估：英文 ~4 字符/token，中文 ~1.5 字符/token，混合分段计。"""
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        if _is_cjk(ord(ch)):
            cjk += 1
        else:
            other += 1
    return max(1, int(cjk / 1.5) + int(other / 4))


def _estimate_message_tokens(msg: dict) -> int:
    total = 4  # role/分隔符开销
    for value in msg.values():
        if isinstance(value, str):
            total += estimate_tokens(value)
        elif isinstance(value, list):
            total += estimate_tokens(json.dumps(value, ensure_ascii=False))
    return total


def trim_messages(messages: list[dict], max_tokens: int = 24000) -> list[dict]:
    """裁剪消息历史至预算内（返回新列表，不改原列表）。

    策略：
    1. 永远保留首条（system prompt）与尾部近 N 条；
    2. 阶段一：截断中部 tool/assistant 长文本；
    3. 阶段二：按"assistant(tool 请求)+连续 tool 结果"原子单元删除（不留孤儿）；
    4. 阶段三：中部历史替换为一行摘要。
    """
    if not messages:
        return []
    total = sum(_estimate_message_tokens(m) for m in messages)
    if total <= max_tokens:
        return list(messages)

    result = [dict(m) for m in messages]

    def _tail_size() -> int:
        return min(6, max(1, len(result) - 2))

    # 阶段一：截断中部超长内容
    tail = _tail_size()
    end = len(result) - tail
    for i in range(1, end):
        msg = result[i]
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role in ("tool", "user", "assistant") and content and len(content) > 500:
            result[i] = {**msg, "content": content[:500] + "\n...[truncated]"}

    total = sum(_estimate_message_tokens(m) for m in result)
    if total <= max_tokens:
        return result

    # 阶段二：原子删除 assistant(tool 调用)+连续工具结果组（ReAct 下 assistant 文本含
    # <tool> 标签且结果以 user 消息跟随——同样按相邻对删除）
    tail = _tail_size()
    i = 1
    while i < len(result) - tail and total > max_tokens:
        msg = result[i]
        if msg.get("role") == "assistant" and "<tool" in (msg.get("content") or ""):
            pair_end = i + 1
            if pair_end < len(result) and result[pair_end].get("role") == "user":
                removed = sum(_estimate_message_tokens(result[j]) for j in range(i, pair_end + 1))
                del result[i:pair_end + 1]
                total -= removed
                continue
        i += 1

    if total <= max_tokens:
        return result

    # 阶段三：中部整体替换为摘要
    tail = _tail_size()
    mid_end = len(result) - tail
    if mid_end > 1:
        middle = result[1:mid_end]
        summary_msg = {"role": "user", "content": summarize_old_messages(middle)}
        if _estimate_message_tokens(summary_msg) < sum(_estimate_message_tokens(m) for m in middle):
            result = [result[0], summary_msg] + result[mid_end:]

    return result


def token_summary(messages: list[dict], max_tokens: int = 24000) -> tuple[int, int]:
    used = sum(_estimate_message_tokens(m) for m in messages)
    return used, max_tokens


def summarize_old_messages(messages: list[dict]) -> str:
    """多轮历史压缩为一行摘要（保留最近用户意图与工具规模感）。"""
    if not messages:
        return "No prior design history."
    tool_count = 0
    user_actions: list[str] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "") or ""
        if role == "user" and "[Tool Result" not in content and content:
            user_actions.append(content.strip()[:50])
        elif "[Tool Result" in content:
            tool_count += 1
    parts = []
    if user_actions:
        parts.append(f"user request: {user_actions[-1]}")
    if tool_count:
        parts.append(f"executed {tool_count} edit calls")
    return ("Design history: " + ", ".join(parts)) if parts else "Design history: no prior actions"
