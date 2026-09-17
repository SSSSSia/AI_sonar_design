"""FastAPI 应用入口（骨架）。

阶段 1 T1.2 将扩展为：Celery worker 消费入口 + 内部 FastAPI（同步算力请求，
如公式即时计算、单次检索试探，P3 §8.3）+ 统一 LLM 适配器挂载。
"""

from fastapi import FastAPI

app = FastAPI(
    title="Sonar-CAD v2 算力层",
    version="0.1.0",
    description="A1–A5 Agent / CAD 执行 / 约束求解 / 检索（骨架期：仅健康检查）",
)


@app.get("/healthz")
def healthz() -> dict:
    """存活探针：容器编排与健康检查使用。"""
    return {"status": "ok", "service": "py-engine", "version": "0.1.0"}
