"""骨架冒烟测试：FastAPI 应用可导入、健康检查可用。

阶段 1 起扩展为：每 Agent "合法 IR 输入 → 合法 IR 输出"契约测试（宪法 §9.2）。
"""

import sys
from pathlib import Path

# 允许从 py-engine 目录直接运行 pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402


def test_healthz() -> None:
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "py-engine"
    # 单测环境不要求真实 broker 连通（T1.2 起健康检查含依赖探测，A-16 如实上报）
    assert body["status"] in ("ok", "degraded")
    assert "broker" in body
