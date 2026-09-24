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
# ---- WS 推送（T4.1，worker → notify；失败仅记日志）----
NOTIFY_BASE_URL = _env("NOTIFY_BASE_URL", "http://localhost:8084")
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

# ---- 几何特征真实化（T7.2/T7.3，PHASE-007）----
# STEP 几何提取子进程超时；0 = 跳过提取（geometry_summary 如实标 disabled，管线继续）
CAD_INSPECT_TIMEOUT_S = int(_env("CAD_INSPECT_TIMEOUT_S", "60"))
# 长度类参数自动联动的粗匹配容差（参数值 vs 分段 bbox 维度相对偏差，百分比）
CAD_GEO_LINK_TOL_PCT = float(_env("CAD_GEO_LINK_TOL_PCT", "20"))
# A4 数值复核容差（linked 参数 vs 实测 bbox 维度相对偏差，百分比；对齐 mockup 06「2.1% 超容差」口径）
CAD_GEO_NUM_TOL_PCT = float(_env("CAD_GEO_NUM_TOL_PCT", "2"))

# ---- CAD 交互微调（T8.1，PHASE-008，ADR-004）----
BUCKET_CAD_TUNES = "cad-tunes"                            # 微调每轮产物（STEP/FCStd/脚本/截图）
# 工具调用等待插件回传的超时：超时标 timeout 并注入修复提示（主线程执行无法强制超时的服务端兜底）
CAD_TUNE_TOOL_TIMEOUT_S = int(_env("CAD_TUNE_TOOL_TIMEOUT_S", "180"))
# AgentLoop 后台线程池（D8-6：工具等待是 DB 轮询非 CPU 密集，不任务化；量大迁 celery）
CAD_TUNE_MAX_WORKERS = int(_env("CAD_TUNE_MAX_WORKERS", "2"))
# 单轮上下文 token 预算（迁移自 CadAgent token_budget 默认值）
CAD_TUNE_MAX_CONTEXT_TOKENS = int(_env("CAD_TUNE_MAX_CONTEXT_TOKENS", "24000"))
# 单轮 AgentLoop 最大工具调用次数（A-13 有界终止）
CAD_TUNE_MAX_ITERATIONS = int(_env("CAD_TUNE_MAX_ITERATIONS", "8"))

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
# LLM 抽取并行度（大文档异步抽取任务用；对供应商限流友好）
KB_EXTRACT_WORKERS = int(_env("KB_EXTRACT_WORKERS", "4"))

# ---- OCR 插件（D-14 落地）：扫描页 → PaddleOCR-VL-1.5（SiliconFlow）----
KB_OCR_ENABLED = _env("KB_OCR_ENABLED", "1").lower() in ("1", "true", "yes")
KB_OCR_MODEL = _env("KB_OCR_MODEL", "PaddlePaddle/PaddleOCR-VL-1.5")
KB_OCR_MAX_PAGES = int(_env("KB_OCR_MAX_PAGES", "20"))      # 单文档 OCR 页数上限（成本护栏，超出如实 pending）
KB_OCR_TIMEOUT_S = int(_env("KB_OCR_TIMEOUT_S", "60"))      # 单页识别超时
KB_OCR_RENDER_SCALE = float(_env("KB_OCR_RENDER_SCALE", "2.0"))  # pdfium 渲染倍率（清晰度/体积权衡）
