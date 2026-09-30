# AI Sonar Design（Sonar-CAD v2）

声呐装备智能辅助设计系统 v2 —— 知识库 + 方案/CAD 代码生成工作流的前后端分离 demo。

> **开发前必读**：[`docs/00_开发约束与协作规范.md`](docs/00_开发约束与协作规范.md)（宪法文档：架构硬约束、文档驱动流程、Git 纪律、裁剪决策）。

## 一句话定位

以参数化 CAD 设计为核心、知识图谱增强检索为依据：需求智能解析 → 技术方案生成 → 参数化 CAD 建模出图 → 设计合规校验 → 知识沉淀复用，全程带审阅关与溯源。

## 技术基线（完整版五层架构，ADR-001）

| 层 | 选型 |
|---|---|
| Java 平台层 | Java 17 + Spring Boot 3 + Spring Cloud Gateway + Spring Security(JWT/RBAC) |
| Python 算力层 | Python 3.12 + FastAPI + Celery（A1–A5 Agent / CadQuery / 约束求解 / 检索） |
| 前端 | React 19 + TypeScript + Vite + Ant Design（Three.js / D3 / ECharts / Monaco） |
| 数据底座 | PostgreSQL 17 + Neo4j 5 + Milvus + MinIO + Redis 7.2 + RabbitMQ（docker-compose） |
| 知识库 | GraphRAG + Neo4j，文档预处理入库管线（MinerU/PP-Structure） |
| CAD | CadQuery（OCP/OpenCascade）沙箱执行 → STEP |
| LLM | 统一适配器：私有化 vLLM 为目标，开发期公网 OpenAI 兼容 API fallback |

## 启动与部署（双轨，T9.5）

| 模式 | 方式 | 适用 |
|---|---|---|
| **开发模式** | `scripts/start-all.cmd`（PS/cmd）或 `bash scripts/start-all.sh`（Git Bash）；幂等，宿主机进程热更新 | 日常开发 |
| **全栈容器模式** | `cd deploy && docker compose --profile app up -d --build`；入口 `http://localhost:${FRONTEND_PORT:-3000}` | 交付/干净机器验收 |

- 两种模式共用数据底座（`docker compose up -d` 默认只起底座 10 容器）；`.env` 由 `.env.example` 复制填写（`set -a; source .env; set +a` 注入）。
- 容器模式三镜像：`sonarcad-frontend`（node:20 多阶段 → nginx:alpine）/ `sonarcad-java`（temurin-17 一镜像五服务，`SERVICE_JAR` 区分）/ `sonarcad-py`（python:3.12-slim + pip 清华源装 cadquery/OCP；uvicorn(8090) 与 celery worker 分容器，convert_worker 按需子进程随容器拉起）。
- py-api 容器 healthcheck 打 `/healthz`（含 T9.1 事件循环心跳，503 即不健康 → `restart: unless-stopped` 自动拉起）。
- 干净克隆验收：新目录 `git clone` → `cp .env.example .env` 填密钥 → `docker compose --profile app up -d --build` → 全部 healthy → 浏览器登录（admin/admin123，上线前必改）。完整清单见 `docs/phase/phase-9-稳定性与核心承诺补强.md` §2.5。
- 默认 `docker compose up` 行为不变（只起底座），开发模式脚本保留——双轨互不影响。

## 开发约定（速记）

- 文档先行：无文档不开工；文档与代码同一 commit 同步。
- 小步提交：每完成一个子任务即 commit + push，Conventional Commits。
- 架构硬约束 A-01~A-16 不可违背（见宪法文档 §3）。
- UI 改动先 mockup 确认再落地；蓝色仅用于交互语义。
- 打 tag 前走发布检查清单（宪法文档附录 A）。

## 目录结构（宪法附录 C）

```
AI_sonar_design/
├── java-platform/   # Java 17 + Spring Boot 3 平台层（gateway/auth/workflow/biz/notify）
├── py-engine/       # Python 3.12 + FastAPI 算力层（api/agents/knowledge/solver/cad_engine）
├── contracts/       # IR0–IR5 JSON Schema 单一事实源（ir/ + ws/）
├── frontend/        # React 19 + TS + Vite + AntD
├── deploy/          # docker-compose 数据底座
└── docs/            # 宪法 + roadmap + phase + api + adr + devlog + reference
```

## 设计文档来源

权威设计文档副本位于 `docs/reference/`（项目自包含），优先级与裁决规则见宪法文档 §1。
