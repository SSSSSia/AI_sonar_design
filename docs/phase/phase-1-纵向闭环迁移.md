# 阶段 1 设计 · 纵向闭环迁移（API 级，无 UI）

| 项 | 内容 |
|---|---|
| 文档编号 | PHASE-001 |
| 版本 | v1.0（2026-09-17） |
| 关联 | CONST-001 §2/§4/§5、ADR-001 迁移映射表、roadmap 阶段 1 |
| 目标 | A1–A3 在新架构（双后端 + MQ + 状态机 + IR 契约）下重组，API 级端到端跑通 需求 → STEP |

## 1. 本期范围

按 roadmap T1.1–T1.7 全量执行。阶段 1 结束时：自然语言需求经 HTTP 进入 → 网关鉴权 → Java 编排状态机派发 MQ 任务 → Python worker 执行 A1→A2→A3 → CadQuery 沙箱出 STEP 落 MinIO，全程 API 可查（无 UI，curl/pytest 冒烟）。

## 2. 服务拓扑与端口约定（T1.1 起生效）

| 服务 | 模块 | 端口 | 职责 |
|---|---|---|---|
| gateway | java-platform/gateway | 8080 | 唯一外部入口；路由 + JWT 校验 + 白名单；下游信任网关注入的 `X-User-Id` / `X-User-Roles` 头 |
| auth | java-platform/auth | 8081 | `/api/v1/auth/login`（users 表 + BCrypt 校验，签发 HS256 JWT，含 roles claim）、`/api/v1/auth/me` |
| workflow | java-platform/workflow | 8082 | 会话 API + 编排状态机（本模块是 D-05 唯一状态机宿主） |
| biz | java-platform/biz | 8083 | 项目/任务/交付物等业务管理（本期先做项目最小 CRUD） |
| py-engine | py-engine | 8090 | T1.2 起接入（Celery worker + 内部 FastAPI） |

JWT 密钥存 `.env`（`JWT_SECRET`），网关与 auth 共享；密钥不入库不入码。统一响应包 `{code, message, data, trace_id}`（宪法 §5.3）。

## 3. T1.1 详细设计（本文档首批落地项）

### 3.1 编排状态机（对应 D-05 / A-05）

- 状态：`pending → running → paused → completed / failed`（终态 completed/failed；paused 由审阅关触发，阶段 2 落地暂停语义，本期先建转移）。
- 事件：`START / PAUSE / RESUME / COMPLETE / FAIL`。
- 实现：Spring StateMachine（starter 已在 BOM）；每会话一实例（StateMachineFactory + 按 sessionId 重建），转移裁决后把终值写 `design_sessions.pipeline_status`。编排层只做"调 Agent 传 IR / 审阅关控制 / 维护 IR 版本"三件事（A-05），本期不写业务决策。
- API：`POST /api/v1/workflow/sessions` 创建（pending）；`GET /api/v1/workflow/sessions/{id}`；`GET /api/v1/workflow/sessions`（分页）；`POST /api/v1/workflow/sessions/{id}/events`（事件驱动转移，非法转移返回 409）。

### 3.2 认证与 RBAC

- 登录成功签发 JWT（sub=username，roles claim），网关全局过滤器校验签名与过期；白名单：`/api/v1/auth/login`。
- 下游服务用轻量过滤器把网关头还原为 Authentication，方法级用 `@PreAuthorize("hasRole('...')")`（T1.1 先覆盖 admin/designer/reviewer/viewer 四角色）。
- 播种：Flyway `V2__seed_rbac.sql` 建 4 角色 + 权限点 + admin 用户（初始口令经 BCrypt，写死开发默认值并在本文档声明"上线必改"，真实口令不进 git）。

### 3.3 验收标准（T1.1 DoD）

1. `mvn -pl gateway,auth,workflow,biz test` 全绿；
2. 实机四服务起后：login 得 token → 无 token 访问 401 → 创建项目/会话 → 走 START/PAUSE/RESUME/COMPLETE 转移落库 → 非法转移 409；
3. roadmap/devlog/HANDOFF 同 commit 回填。

## 4. T1.2–T1.7 要点（开工前各自细化，此处记边界）

- T1.2 worker：Celery + RabbitMQ（宿主 5672），LLM 适配器 OpenAI 兼容多供应商、配置热切换（D-12），密钥走 `.env`/业务库且序列化脱敏。
- T1.3–T1.5 按 ADR-001 复用映射迁移旧代码（★/★★标注），Agent 间禁止互调（A-03），IR 走 `contracts/ir/` 校验（A-04）。
- T1.7 冒烟样例：拖曳线列阵标准需求，固化 `tests/e2e/`。

## 5. 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-17 | 初版（ZCode）：范围、拓扑端口、T1.1 详细设计与验收标准 |
