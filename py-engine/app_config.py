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
# LLM 单次调用超时（秒）：公网供应商排队/长生成可能远超 60s
LLM_TIMEOUT_S = int(_env("LLM_TIMEOUT_S", "300"))
# Celery 任务时限（秒）：A2 两次 LLM 调用、A3 最多 4 次（生成+3 轮修复），需覆盖最坏情况
TASK_SOFT_TIME_LIMIT_S = int(_env("TASK_SOFT_TIME_LIMIT_S", "1800"))
TASK_HARD_TIME_LIMIT_S = int(_env("TASK_HARD_TIME_LIMIT_S", "2400"))

# 重试上限（A-13 有界终止）：全栈共享，禁止死循环
TASK_MAX_RETRIES = int(_env("TASK_MAX_RETRIES", "3"))
LLM_MAX_RETRIES = int(_env("LLM_MAX_RETRIES", "2"))

# ---- 知识库（T1.6，A-14 三级降级检索）----
KNOWLEDGE_KB_DIR = _env("KNOWLEDGE_KB_DIR", os.path.join(os.path.dirname(__file__), "knowledge", "kb"))

# ---- MinIO 对象存储（T1.5，D-10：step-models / cad-scripts 分桶）----
MINIO_ENDPOINT = _env("MINIO_ENDPOINT", f"localhost:{_env('MINIO_API_PORT', '19000')}")
MINIO_ACCESS_KEY = _env("MINIO_ROOT_USER", "sonarcad")
MINIO_SECRET_KEY = _env("MINIO_ROOT_PASSWORD", "")
BUCKET_STEP_MODELS = "step-models"
BUCKET_CAD_SCRIPTS = "cad-scripts"

# CAD 沙箱（A-13 有界）
CAD_EXECUTE_TIMEOUT_S = int(_env("CAD_EXECUTE_TIMEOUT_S", "300"))
CAD_FIX_MAX_ROUNDS = int(_env("CAD_FIX_MAX_ROUNDS", "3"))

# ---- Neo4j 图谱（T2.3 A5 知识回写；compose 覆盖端口见 HANDOFF：BOLT 默认 7687，本机 17687）----
NEO4J_URI = _env("NEO4J_URI", f"bolt://localhost:{_env('NEO4J_BOLT_PORT', '7687')}")
_neo4j_auth = _env("NEO4J_AUTH")
NEO4J_USER = _env("NEO4J_USER") or (_neo4j_auth.split("/", 1)[0] if "/" in _neo4j_auth else "neo4j")
NEO4J_PASSWORD = _env("NEO4J_PASSWORD") or (_neo4j_auth.split("/", 1)[1] if "/" in _neo4j_auth else "")
# 连接/查询超时（秒）：不可达时快速失败、如实上报（A-16）
NEO4J_TIMEOUT_S = int(_env("NEO4J_TIMEOUT_S", "5"))

# ---- 知识文档管线（T3.1–T3.3，宪法 §6）----
BUCKET_KB_DOCS = "kb-docs"                                # 知识库原文件桶
KB_UPLOAD_MAX_BYTES = int(_env("KB_UPLOAD_MAX_BYTES", str(50 * 1024 * 1024)))  # 上传大小上限（§10.4）
KB_CHUNK_SIZE = int(_env("KB_CHUNK_SIZE", "800"))         # 分块目标字符数（章节优先）
KB_CHUNK_OVERLAP = int(_env("KB_CHUNK_OVERLAP", "150"))   # 分块重叠字符数（§6 ③ 可调）
KB_EMBED_MODEL = _env("KB_EMBED_MODEL", "BAAI/bge-m3")    # 向量模型（settings.yaml 同款）
KB_EMBED_DIM = int(_env("KB_EMBED_DIM", "1024"))          # bge-m3 维度
KB_MILVUS_URI = _env("KB_MILVUS_URI", f"http://localhost:{_env('MILVUS_PORT', '19530')}")
KB_MILVUS_COLLECTION = _env("KB_MILVUS_COLLECTION", "kb_chunks")
# 检索 KB 扩展级开关（graph=自建 Neo4j 实体层 / vector=Milvus）：A-14 三级降级的完整形态
KB_RETRIEVAL_LEVELS = _env("KB_RETRIEVAL_LEVELS", "1")
