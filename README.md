# AI Sonar Design（Sonar-CAD v2）

声呐装备智能辅助设计系统 v2 —— 知识库 + 方案/CAD 代码生成工作流的前后端分离 demo。

## 一句话定位

以参数化 CAD 设计为核心、知识图谱增强检索为依据：需求智能解析 → 技术方案生成 → 参数化 CAD 建模出图 → 设计合规校验 → 知识沉淀复用，全程带审阅关与溯源。

## 毕设协作入口

本科毕设课题「**面向设计文档生成的知识增强对话智能体设计与实现**」——**全部任务要求以任务书为唯一依据**：[`docs/本科毕业设计任务说明_知识增强对话智能体.md`](docs/本科毕业设计任务说明_知识增强对话智能体.md)。

课题为完全独立开发（技术建议 Pi Agent 与 GraphRAG 见任务书）：**无需阅读、参考或修改本仓库的代码与文档**，本仓仅作课题背景了解。

## 技术基线

| 层 | 选型 |
|---|---|
| Java 平台层 | Java 17 + Spring Boot 3 + Spring Cloud Gateway + Spring Security(JWT/RBAC) |
| Python 算力层 | Python 3.12 + FastAPI + Celery（A1–A5 Agent / CadQuery / 约束求解 / 检索） |
| 前端 | React 19 + TypeScript + Vite + Ant Design（Three.js / D3 / ECharts / Monaco） |
| 数据底座 | PostgreSQL 17 + Neo4j 5 + Milvus + MinIO + Redis 7.2 + RabbitMQ（docker-compose） |
| 知识库 | GraphRAG + Neo4j，文档预处理入库管线（MinerU/PP-Structure） |
| CAD | CadQuery（OCP/OpenCascade）沙箱执行 → STEP |
| LLM | 统一适配器：私有化 vLLM 为目标，开发期公网 OpenAI 兼容 API fallback |

## 启动与部署（双轨）

| 模式 | 方式 | 适用 |
|---|---|---|
| **开发模式** | `scripts/start-all.cmd`（PS/cmd）或 `bash scripts/start-all.sh`（Git Bash）；幂等，宿主机进程热更新 | 日常开发 |
| **全栈容器模式** | `cd deploy && docker compose --profile app up -d --build`；入口 `http://localhost:${FRONTEND_PORT:-3000}` | 交付/干净机器验收 |

- 两种模式共用数据底座（`docker compose up -d` 默认只起底座 10 容器）；`.env` 由 `.env.example` 复制填写（`set -a; source .env; set +a` 注入）。
- 容器模式三镜像：`sonarcad-frontend`（node:20 多阶段 → nginx:alpine）/ `sonarcad-java`（temurin-17 一镜像五服务，`SERVICE_JAR` 区分）/ `sonarcad-py`（python:3.12-slim；uvicorn(8090) 与 celery worker 分容器，convert_worker 按需子进程随容器拉起）。
- py-api 容器 healthcheck 打 `/healthz`（含事件循环心跳，503 即不健康 → `restart: unless-stopped` 自动拉起）。
- 干净克隆验收：新目录 `git clone` → `cp .env.example .env` 填密钥 → `docker compose --profile app up -d --build` → 全部 healthy → 浏览器登录（admin/admin123，上线前必改）。

## 监控告警

| 组件 | 访问 | 说明 |
|---|---|---|
| Prometheus | `http://localhost:${PROMETHEUS_PORT:-9090}` | 抓取 15s；目标 = 容器化 app 栈（五 Java `/actuator/prometheus`、py-api `/metrics`、py-worker `:9101`、RabbitMQ `:15692`、postgres-exporter） |
| Alertmanager | `http://localhost:${ALERTMANAGER_PORT:-9093}` | 4 条核心告警 → webhook 桥接 notify `/internal/alerts`（全播 WS，type=alert） |
| Grafana | `http://localhost:${GRAFANA_PORT:-3300}`（admin/`${GRAFANA_ADMIN_PASSWORD:-admin}`） | 两块预置面板：平台总览 / Agent 管线（provisioning 自动装载） |

- 启动：与 app 配套 `cd deploy && docker compose --profile app --profile monitoring up -d`（纯开发模式无对应目标，监控栈会显示 target down——监控面向容器化栈设计）。
- 4 条告警规则在 `deploy/prometheus/alerts.yml`：Agent 任务 >15min 占比 / CadQuery 失败率>10% / 检索 P95>30s / Celery 队列>100。
- 压测：`docker compose --profile loadtest up -d mock-llm` 起 mock LLM（:8091），k6 场景脚本见 `tests/load/k6/`。

## 目录结构

```
AI_sonar_design/
├── java-platform/   # Java 17 + Spring Boot 3 平台层（gateway/auth/workflow/biz/notify）
├── py-engine/       # Python 3.12 + FastAPI 算力层（api/agents/knowledge/solver/cad_engine）
├── contracts/       # IR0–IR5 JSON Schema 单一事实源（ir/ + ws/）
├── frontend/        # React 19 + TS + Vite + AntD
├── deploy/          # docker-compose 数据底座
├── clients/         # FreeCAD 插件（SonarCADLink，薄插件 + 后端大脑架构）
├── tests/           # 压测场景（k6）
└── docs/            # 毕设任务书（内部开发与设计文档本地维护，不入库）
```

> 内部开发管理文档（路线图、架构决策记录、验收记录等）与设计文档定稿存于维护方本地，不在本仓库范围内。
