"""py-engine 全局配置（T1.2）。

所有连接参数来自 .env（宪法 §10：密钥不入库不入码）。
"""

from __future__ import annotations

import os


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


# ---- 消息队列 / 结果后端（D-06）----
CELERY_BROKER_URL = _env("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//")
CELERY_RESULT_BACKEND = _env("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

# ---- 平台层回调（worker → workflow 状态推进）----
WORKFLOW_BASE_URL = _env("WORKFLOW_BASE_URL", "http://localhost:8082")
# 服务间身份：workflow 的 TrustedHeaderAuthFilter 按信任头还原（仅限内网直连）
SERVICE_AUTH_HEADERS = {"X-User-Id": "py-engine-worker", "X-User-Roles": "admin"}

# ---- 业务库（LLM 供应商热切换配置，D-12）----
DATABASE_URL = _env(
    "DATABASE_URL",
    "postgresql+psycopg://"
    f"{_env('POSTGRES_USER', 'sonarcad')}:{_env('POSTGRES_PASSWORD', '')}"
    f"@{_env('POSTGRES_HOST', 'localhost')}:{_env('POSTGRES_PORT', '5432')}"
    f"/{_env('POSTGRES_DB', 'sonarcad')}",
)

# ---- LLM 环境兜底供应商（业务库无 enabled 行时使用；私有化 vLLM 时改指向内网）----
LLM_BASE_URL = _env("LLM_BASE_URL")
LLM_API_KEY = _env("LLM_API_KEY")
LLM_MODEL = _env("LLM_MODEL")

# 供应商配置热加载 TTL（秒）：DB 改动后最多该时长内生效
PROVIDER_CACHE_TTL_S = int(_env("PROVIDER_CACHE_TTL_S", "30"))

# 重试上限（A-13 有界终止）：全栈共享，禁止死循环
TASK_MAX_RETRIES = int(_env("TASK_MAX_RETRIES", "3"))
LLM_MAX_RETRIES = int(_env("LLM_MAX_RETRIES", "2"))

# ---- 知识库（T1.6，A-14 三级降级检索）----
KNOWLEDGE_KB_DIR = _env("KNOWLEDGE_KB_DIR", os.path.join(os.path.dirname(__file__), "knowledge", "kb"))
