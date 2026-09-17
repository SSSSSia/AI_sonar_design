# 阶段 0 开发文档 · 工程地基（已收官）

| 项 | 内容 |
|---|---|
| 文档编号 | PHASE-000 |
| 版本 | v1.0（2026-09-17，收官回写：实施记录 + 验收结论） |
| 关联 | CONST-001、ADR-001、ADR-002、roadmap 阶段 0 |
| 状态 | **全部完成并实机验收**（T0.1–T0.4，2026-09-16 ~ 09-17，WorkBuddy 与 ZCode 协作） |

## 1. 阶段目标（回顾）

搭建 v2 全量开发的四块地基：**仓库目录骨架**（宪法附录 C 物理分离）、**数据底座容器化**（六件套一键起）、**IR 契约初版**（A-04 单一事实源）、**平台表结构**（P3 §7.7 + Flyway）。

## 2. 任务实施记录

### T0.1 仓库骨架 ✅

- `java-platform/`：Maven 多模块（parent + gateway/auth/workflow/biz/notify 五模块，各自 Spring Boot 启动类与端口占位 8080–8084）；父 pom 锁 Spring Boot 3.3.5 + Spring Cloud 2023.0.3 + spring-statemachine 4.0.0（BOM 显式 import、version 写死——property 插值在 BOM import 处失效的坑）。
- `py-engine/`：FastAPI 骨架 + agents（a1–a5 目录占位）/ knowledge / solver / cad_engine / tests。
- `frontend/`：Vite + React 19 + TS 脚手架（`tsc -b && vite build` 验证过）。
- `contracts/`、`deploy/`、`docs/` 按附录 C 就位；`.gitignore` 先于首个功能提交建立。

### T0.2 数据底座容器化 ✅

- `deploy/docker-compose`：PG 17 / Neo4j 5 / Milvus（含 etcd、minio）+ Redis 7.2 + RabbitMQ + Nginx，健康检查全配，**9/9 容器 healthy 实机验收**。
- 宿主机端口覆盖（本机 openspg 常驻占 7474/7687/9000/9001）：Neo4j → 17474/17687，业务 MinIO → 19000；connection 参数全部走 `.env`。
- MinIO 供应链：官方镜像已从 Docker Hub 下架（2026-02），切换 **Chainguard digest 固定镜像（0 CVE）**，ADR-002 记录决策与升级流程；`minio-volume-init` 一次性服务做卷属主初始化。
- 国内网络：daocloud registry-mirrors（系统代理对 Docker daemon 不自动生效，以 mirrors 为准）。

### T0.3 IR 契约初版 ✅

- `contracts/ir/ir0–ir5.schema.json`（JSON Schema 2020-12，`additionalProperties: false` 严格模式）：IR0 原始需求 / IR1 设计目标+子查询+检索引用 / IR2 带出处参数表（系统枢纽）/ IR3 CAD 产物 / IR4 校验报告 / IR5 沉淀候选。
- `contracts/ws/ws-message.schema.json`：WebSocket 消息契约（宪法 §5.5）。

### T0.4 平台表结构 + Flyway ✅

- `biz/src/main/resources/db/migration/V1__init_platform_tables.sql`：12 张表（users/roles/permissions/user_roles/role_permissions/projects/design_sessions/ir_snapshots/parameter_schemes/review_records/tasks/artifacts 等，按 P3 §7.7）。
- 实机验收：flyway 一次性容器对运行中 PG 17.11 执行 migrate，schema 至 v1，12 表建成。

## 3. 阶段验收结论

| 检查项 | 结论 |
|---|---|
| 目录物理分离（contracts / java-platform/workflow / py-engine/agents） | ✅ |
| 六件套数据底座一键起 + 健康 | ✅ 9/9 healthy（2026-09-17） |
| IR0–IR5 + WS 契约入库 | ✅ |
| V1 建表实机通过 | ✅ |
| `.env` 不入库、无密钥泄露 | ✅ |

**阶段 0 于 2026-09-17 验收通过**，遗留注意事项（MinIO digest 升级流程、端口覆盖）已移交 HANDOFF「环境注意」。

## 4. 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-17 | 初版（ZCode）：按实际实施回写收官文档 |
