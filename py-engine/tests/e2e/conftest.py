"""e2e 前置：加载仓库根 .env（宪法 §10：连接凭据只存 .env，测试进程同样从那里取）。"""

from pathlib import Path

import sys

_ROOT = Path(__file__).resolve().parents[3]  # 仓库根（.env 所在）
sys.path.insert(0, str(_ROOT / "py-engine"))


def _load_env() -> None:
    import os
    import re

    env_path = _ROOT / ".env"
    if not env_path.exists():
        return
    # .env 存在同名键覆盖段（端口冲突覆盖），与 shell source 语义一致：后值覆盖先值
    collected: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        collected[key.strip()] = value.strip()

    for key, value in collected.items():
        # 展开 ${VAR} / ${VAR:default} 引用（已收集的同名键优先）
        value = re.sub(
            r"\$\{(\w+)(?::-([^}]*))?\}",
            lambda m: collected.get(m.group(1)) or os.environ.get(m.group(1), m.group(2) or ""),
            value,
        )
        os.environ.setdefault(key, value)


_load_env()
