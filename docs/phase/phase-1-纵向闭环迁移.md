# 阶段 1 开发文档 · 纵向闭环迁移（API 级，无 UI）

| 项 | 内容 |
|---|---|
| 文档编号 | PHASE-001 |
| 版本 | v2.0（2026-09-17，由 v1.0 轻量版升级为完整版） |
| 关联 | CONST-001 §2/§4/§5/§7、ADR-001 迁移映射表、roadmap 阶段 1 |
| 阶段目标 | A1–A3 在新架构（双后端 + MQ + 状态机 + IR 契约）下重组，**API 级端到端跑通：自然语言需求 → STEP** |
| 验收形态 | 无 UI；全部能力经 gateway（8080）以 curl/pytest 冒烟验证；`tests/e2e/` 固化标准样例 |

---

## 1. 阶段定位与背景

- **为什么有这个阶段**：旧仓库 `../Sonar_cad_design` 已端到端实现三阶段闭环（需求→检索→方案→CadQuery→STEP），但它是同步、进程内、字符串传递的单机 workflow（ADR-001 走查结论）。阶段 1 的本质是**把已验证的方法链路迁移到完整版五层架构上**，不是重新发明。
- **迁移输入**（ADR-001 复用映射）：
  | 旧资产 | 去向 | 等级 |
  |---|---|---|
  | `prompts.py` 提示词模板 | 提示词模板管理 | ★ 直接入库 |
  | `graphrag_service/sdk.py + service.py` | `py-engine/knowledge/` | ★ 直接迁移 |
  | KB 索引产物（307 实体）+ `Ref_code/` 范例库 | `py-engine/` | ★ 直接复制 |
  | `stages/codegen_stage.py`（多层静态校验） | A3 | ★★ 内脏复用 |
  | `stages/executor_stage.py`（执行+修复循环） | `cad_engine/` | ★★ 内脏复用 |
  | `stages/knowledge_stage.py` | A1 + A2 拆用 | ★★ 逻辑拆用 |
  | `pipeline.py` / `llm.py` / `ui/` | — | ☆ 仅参考/弃用 |
- **阶段边界（做什么/不做什么）**：
  - 做：T1.1–T1.7（见 §3），覆盖 C1–C3 能力 + 平台骨架 + 检索底座。
  - 不做：A4/A5/审阅关/溯源（阶段 2）、知识治理管线（阶段 3）、一切 UI（阶段 4）、多方案比选等暂缓项。
  - 阶段 1 期间**所有中间产物如实可见**：missing_items、降级事件、失败明细（A-16）。

## 2. 阶段架构与服务拓扑

### 2.1 数据流总览

```
用户需求文本
  │ HTTP(经 gateway 8080, JWT)
  ▼
workflow(8082) ── 会话/项目(biz 8083, PG)
  │ 创建任务 → RabbitMQ(5672) ──────────┐
  │ 状态机驱动 pending→running→…         │ 异步
  ▼                                     ▼
PG(ir_snapshots 参数/状态)        py-engine worker(Celery)
                                     │ A1 需求解析(IR1)
                                     │ A2 方案生成(IR2) ←─ 检索：Neo4j→Milvus→文本 三级降级(A-14)
                                     │ A3 CAD 建模(IR3) ←─ CadQuery 沙箱子进程(A-13/§10)
                                     ▼
                              STEP/文档 → MinIO(19000)
                                     │ 回调/轮询
                                     ▼
                            workflow 更新状态 → 会话 completed
```

### 2.2 服务与端口

| 服务 | 模块 | 端口 | 职责 | 引入任务 |
|---|---|---|---|---|
| gateway | java-platform/gateway | 8080 | 唯一外部入口；路由 + JWT 校验 + 信任头注入 | T1.1 ✅ |
| auth | java-platform/auth | 8081 | 登录签发 JWT、/me 自省 | T1.1 ✅ |
| workflow | java-platform/workflow | 8082 | 会话 API + 编排状态机（D-05 唯一宿主） | T1.1 ✅ |
| biz | java-platform/biz | 8083 | 项目/任务/交付物管理（本期项目最小 CRUD） | T1.1 ✅ |
| py-engine api | py-engine/api | 8090 | worker 内部 FastAPI：健康检查、任务回调、调试入口 | T1.2 |
| py-engine worker | py-engine（Celery） | — | 无状态消费 MQ 任务，执行 A1–A3 | T1.2–T1.5 |

数据底座（阶段 0 已就绪）：PG 5432 / Redis 6379 / RabbitMQ 5672,15672 / Neo4j 17474,17687 / Milvus 19530 / MinIO 19000。

### 2.3 横切约定

- **统一响应包** `{code, message, data, trace_id}`（宪法 §5.3）；4xx 客户端 / 5xx 服务端；业务错误码表 `docs/api/error_codes.md` 随 T1.2 首个 Python 端点建立。
- **鉴权链**：客户端 → gateway（校验 JWT，剥伪造头，注入 `X-User-Id`(UUID)/`X-User-Roles`）→ 下游 TrustedHeaderAuthFilter 还原 Authentication → `@PreAuthorize`。auth/workflow/biz 端口不得直接对外暴露。
- **JWT**：HS256，sub=用户 UUID，username/roles claim，TTL 12h；密钥 `.env JWT_SECRET`（宪法 §10）。
- **IR 契约**：所有 IR 读写以 `contracts/ir/*.schema.json` 为准（A-04）；py-engine 侧 Pydantic 模型生成自 schema 并有对齐测试，Java 侧仅透传与存储，不做字段解释。
- **任务模型**：任务表 + MQ 消息双层（D-06）；失败进死信队列，重试上限可配（A-13）。

## 3. 任务分解（T1.1–T1.7）

> 每个任务给出：目标 / 设计要点 / 接口契约 / 完成定义（DoD）/ 验收方法。状态与 roadmap 同步。

### T1.1 Java 平台层骨架 ✅ 完成（2026-09-17）

- **目标**：gateway 路由 + JWT/RBAC 基础 + 会话 API + 会话状态机。
- **设计要点**：
  - 状态机：`pending →(START)→ running →(PAUSE/COMPLETE/FAIL)→ paused →(RESUME)→ running`；completed/failed 终态。Spring StateMachine（工厂）与 `PipelineTransitions` 转移表**单测互证**防漂移；落库用乐观 `WHERE pipeline_status=<旧值>` 防并发。审阅关触发的 PAUSE 语义留待 T2.2。
  - RBAC：Flyway V2 播种 4 角色（admin/designer/reviewer/viewer）+ 8 权限点 + admin 用户（开发默认口令 `admin123`，上线必改）。
  - 鉴权链见 §2.3；网关白名单仅 `/api/v1/auth/login`。
- **接口**：
  | 方法 | 路径 | 说明 |
  |---|---|---|
  | POST | /api/v1/auth/login | {username,password} → token/roles |
  | GET | /api/v1/auth/me | Bearer 自省 |
  | POST/GET | /api/v1/projects[/{id}] | 项目最小 CRUD |
  | POST/GET | /api/v1/workflow/sessions[/{id}] | 会话创建（pending）/分页/详情 |
  | GET | /api/v1/workflow/sessions/{id}/allowed-events | 当前状态可触发事件自省 |
  | POST | /api/v1/workflow/sessions/{id}/events | {event} 驱动转移；非法 409 |
- **DoD / 验收**（已全部通过，2026-09-17 实机）：
  - [x] `mvn -pl gateway,auth,workflow,biz test` 全绿（状态机↔转移表互证 2 用例）
  - [x] 无 token 访问 401；伪造 X-User-* 头 401
  - [x] login → /me 返回 userId+roles
  - [x] 建项目 → 建会话（pending）→ START→PAUSE→RESUME→COMPLETE 落库（design_sessions 逐状态可查）
  - [x] 终态再 START 返回 409
- **遗留/已知限制**：项目 API 仅最小 CRUD（完整 §5.7 随阶段 2/3）；任务表与 MQ 派发在 T1.2/T1.7 接入；devlog 记录 5 个环境坑（uuid/stringtype、JWT sub、GBK curl、jar 锁、statemachine 4.0 API）。

### T1.2 Python worker 骨架（Celery + FastAPI + LLM 适配器）✅ 完成（2026-09-17）

- **目标**：`py-engine/` 具备无状态消费 MQ 任务的能力，LLM 接入可配置热切换。
- **设计要点**：
  - Celery broker=RabbitMQ（宿主 5672，凭据 `.env`）、backend=Redis；worker 无状态（宪法 §4），任务结果只写共享存储（PG/MinIO）。
  - 内部 FastAPI（8090，仅网关/本机可达）：`/health`、`/tasks/{id}` 查询、任务回调接收（供 workflow 轮询/回调更新状态）。
  - 统一 LLM 适配器（D-12）：OpenAI 兼容协议多供应商；配置存业务库 + 热切换；api_key 序列化必须脱敏（宪法 §10.2）；并发限流；失败重试上限可配（A-13）。目标形态 vLLM 私有化的接口位预留。
  - 骨架任务样例：echo 任务全链路（workflow 投递 → worker 消费 → 回调 → 会话状态推进）打通 MQ 与状态机的连接，为 T1.3 铺路。
- **DoD / 验收**：
  - [ ] worker 起停健康，`/health` 200
  - [ ] echo 任务经 MQ 全链路往返，会话状态 pending→running→completed 由真实任务驱动（而非手工 POST events）
  - [ ] LLM 适配器 mock 供应商切换测试通过；真实 key 不出现在任何日志/输出
  - [ ] 死信队列：制造必失败任务，验证进入 DLX 且任务表状态如实 failed
  - [ ] pytest 单测 + roadmap/devlog 回填同 commit

### T1.3 A1 需求解析（迁移 knowledge_stage 拆用 → IR1）✅ 完成（2026-09-17）

- **目标**：自然语言需求 → 结构化目标 + 4~8 条子查询 + missing_items，输出 IR1。
- **设计要点**：
  - 迁移旧 `knowledge_stage.py` 的查询规划/兜底/并行检索逻辑（★★拆用）；LLM 仅用于需求理解与子查询规划（A-07 白名单）。
  - 边界（A-02/§3.1）：不推导几何参数、不赋值；知识缺口如实进 `missing_items`（A-16）。
  - 检索调用经 T1.6 的三级降级封装；A1 不直接 import 其他 Agent（A-03）。
  - IR1 落 `ir_snapshots`（不可变快照，A-12），schema 对齐 `contracts/ir`。
- **DoD / 验收**（已通过，2026-09-17 实机）：
  - [x] 标准样例需求解析出结构化目标 + 子查询，IR1 通过契约校验（worker 内 jsonschema 硬校验）
  - [x] 知识缺失如实展示：LLM 不可用时 fallback 规划 8 维度全部进 missing_items；检索全败追加"知识库检索无可用结果"
  - [x] 检索三级降级单测 10 个（T1.6）+ A1 契约/兜底单测 10 个
  - [x] Agent 独立 CLI 实测（`python -m agents.a1_requirement.cli`，含真实 KB 检索）
- **实现备注**：会话创建即固化 IR0 快照（requirement 字段，created_by=user）；START 投 `sonarcad.a1`（新队列 sonarcad.a1，DLX 同配置）；worker 读 IR0 → A1Agent → IR1 快照（created_by=A1）→ current_stage=A1 → COMPLETE。文本级检索引用带 doc_id+段落位置（E2 溯源最小单元，T3 升级为页码/章节）。
- **环境事实**：`.env` 的 LLM_API_KEY 仍为占位符 your_api_key（401 已实锤），LLM 规划自动走兜底——填真实 key 后无需改码即恢复 LLM 规划。

### T1.4 A2 方案生成（★系统枢纽 → IR2）✅ 完成（2026-09-17）

- **目标**：产出设计文档 + 带出处参数表（source_type 四分类 + source_ref + derivation + trace_id），输出 IR2。
- **设计要点**：
  - 重写旧整合逻辑为带出处参数表生成（★★）；**IR2 是全系统枢纽**，字段最严。
  - A-06：每个参数必须有 `source_type ∈ {formula, case, standard, estimated}` 四件套，缺一不得进 IR2；estimated 必须标注依据。
  - A-07：公式类参数取值必须由求解代码（SymPy 等）计算，LLM 只可在约束区间内提议案例类参数并标 estimated。
  - 设计文档与参数表经 MinIO/PG 双落；trace_id 溯源留钩子（阶段 2 全量）。
- **DoD / 验收**（已通过，2026-09-17）：
  - [x] 标准样例产出 IR2：四件套 100%（worker 断言 + e2e 断言双保险）；schema 校验通过
  - [x] 公式参数由 solver 代码计算且可复算（e2e 断言 DI=10lg(16) 精确吻合）
  - [x] 契约测试"合法 IR1 → 合法 IR2"（含 LLM 断供降级路径）；CLI 可跑（agents.a2_scheme.cli）

### T1.5 A3 CAD 建模（IR2 → IR3，CadQuery 沙箱出 STEP）✅ 完成（2026-09-17）

- **目标**：范例匹配 → 生成 CadQuery 脚本 → 多层静态校验 → 沙箱执行出 STEP，输出 IR3。
- **设计要点**：
  - 迁移 `codegen_stage.py` 内脏（★★：多层静态校验规则、关键建模模式检查、修复 prompt）；`Ref_code/` 范例库与 catalog 直接复制（★）。
  - 迁移 `executor_stage.py` 内脏到 `cad_engine/`（★★）：子进程 + 独立工作目录 + 执行时限（宪法 §10.3）；修复循环次数上限可配（A-13），超限降级输出并如实说明。
  - A-08：严格继承 IR2 参数，发现矛盾抛回（本期为抛错+failed，回环机制阶段 2）。
- **DoD / 验收**（已通过，2026-09-17）：
  - [x] 标准样例产出合法 STEP 落 MinIO（step-models/{sid}/sonar_model.step），e2e 用 OCC 重读 Volume>0
  - [x] 修复循环上限 CAD_FIX_MAX_ROUNDS 可配；沙箱超时/异常单测覆盖（超限如实失败，无死循环）
  - [x] 沙箱子进程隔离（宪法 §10.3，弃用旧进程内 exec）；IR3 过 schema 校验
- **实现备注**：catalog 双信号选范例 + 多层校验链 + sin² 自动修复（旧内脏 ★★迁移）；CADQUERY/FIX prompt（★迁移）。
- **待真实 LLM 验证项**：LLM 生成脚本的修复循环收敛性——当前 e2e 用 mock LLM 脚本化响应验证管线；真实 key 就绪后需重跑 e2e 观察 A3 真实生成/修复行为

### T1.6 GraphRAG 检索封装迁移 + 三级降级 ✅ 完成（2026-09-17）

- **目标**：`py-engine/knowledge/` 提供统一检索接口：图谱(local) → 向量(basic) → 文本关键词，任一级失败降级不中断（A-14）。
- **设计要点**：
  - `sdk.py + service.py` 直接迁移（★）+ 异步包装；旧 KB 索引产物复制入 `py-engine/knowledge/kb/`（★，注意不含密钥）。
  - Neo4j/Milvus 连接走覆盖端口（17474/17687、19530，见 HANDOFF）；降级事件必须记录并可查（诚实交代）。
- **DoD / 验收**（已通过，2026-09-17）：
  - [x] 三级各返回结果的单测；逐级模拟故障的降级单测（local→basic→text、异常不中断）——10 用例
  - [x] 标准查询在真实 KB 上返回可用上下文；降级轨迹出现在响应元数据（`degraded_from`，含 `graphrag_sdk_unavailable` 如实标注）
- **实现备注**：sdk.py 原样迁移（★，仅改日志路径）；service.py v2 化——`RetrievalResult`（content/level/degraded_from）、GraphRAG lazy import（未装 graphrag 自动文本级，测试轻量可跑）、KB 目录 env 可配、中文 2-gram 文本检索。kb/ 资产（input/output/prompts/settings.yaml，2.9MB）已入库；`.gitignore` 对 `py-engine/knowledge/kb/output/` 显式反排除。graphrag 为可选依赖（requirements 注释说明）。

### T1.7 端到端冒烟与固化 ✅ 完成（2026-09-17）

- **目标**：自然语言需求 → 经 MQ 与状态机 → STEP + 设计文档落 MinIO，全链路打通并固化为 `tests/e2e/`。
- **设计要点**：
  - 标准样例：拖曳线列阵需求（与宪法 §9.3 一致）；断言覆盖：会话终态 completed、IR1/IR2/IR3 快照存在且 schema 合法、STEP 非空且可被 OCC 重新读入、设计文档/参数表四件套完整、全程无密钥泄漏。
  - 冒烟脚本可重复执行（幂等清理或独立项目）。
- **DoD / 验收（阶段出口判据，已通过）**：
  - [x] `tests/e2e/test_towed_array_e2e.py` 完整通过（~21s）：需求 → IR0–IR3 快照（created_by 核对）→ IR2 四件套与公式复算断言 → STEP 落 MinIO 且 OCC 重读
  - [x] 失败注入：LLM 不可用（T1.3 实测 401→A1 兜底/A2 降级）、沙箱超时与运行异常（单测）、检索全降级（T1.6 单测）——均如实降级或失败，无静默
  - [x] roadmap 阶段 1 全部任务回填完成，验收记录写入 §5
- **e2e 基础设施**：本地 mock LLM 供应商（tests/e2e/mock_llm_server.py，OpenAI 兼容）注册进 llm_providers 即被 D-12 热切换采用——验证"除 LLM 创作外的全部真实环节"（检索/求解/沙箱/MinIO/编排全真实）。**真实 key 就绪后删除该行即自动切换回真实供应商重跑**

## 4. 阶段管理

### 4.1 里程碑

| 里程碑 | 包含任务 | 判据 |
|---|---|---|
| M1-1 平台骨架 | T1.1 | ✅ 已达成（2026-09-17） |
| M1-2 算力骨架 | T1.2 | ✅ 已达成（2026-09-17） |
| M1-3 认知层 | T1.3 + T1.6 | ✅ 已达成（2026-09-17） |
| M1-4 枢纽与出图 | T1.4 + T1.5 | ✅ 已达成（2026-09-17） |
| M1-5 阶段收官 | T1.7 | ✅ 已达成（2026-09-17，e2e 固化通过） |

### 4.2 依赖与顺序

T1.2 → {T1.3、T1.6 可并行} → T1.4 → T1.5 → T1.7。T1.6 建议先于 T1.3 完成检索接口（A1 依赖检索）。

### 4.3 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 公网 LLM 不稳/限流 | 阻塞 A1–A3 | 适配器多供应商 + 重试上限 + mock 测试解耦（A-13） |
| 旧代码内脏与 IR 契约不匹配 | T1.4/T1.5 返工 | 先写契约测试再迁移；外壳全部重写只搬内脏 |
| CadQuery 子进程在 Windows 沙箱的稳定性 | 出图失败 | 独立工作目录 + 时限 + 重试上限；必要时容器内执行（预留 deploy 侧 cad-runner） |
| Neo4j/Milvus 覆盖端口被误当默认 | 连接失败排障耗时 | 连接参数集中 `.env`；HANDOFF 已标注 |
| 阶段 1 无 UI，进度不可视 | 演示/验收体验差 | 以 curl 脚本 + 统一响应包自省端点弥补；不做临时 UI（宪法 §7.3 先 mockup） |

### 4.4 验收流程（阶段末）

1. 逐任务核对 DoD 勾选项（本文件 + roadmap 双处一致）。
2. 跑 `tests/e2e/` 全量 + 三种失败注入演练（T1.7）。
3. 人工抽查：IR2 参数表至少 10 条参数逐条核对出处真实性（A-06/A-16 抽查）。
4. 验收结论（通过/条件通过+遗留清单/不通过）写入 §5，条件通过项登记 roadmap 备注与 HANDOFF。

## 5. 阶段验收记录

| 日期 | 事项 | 结论 |
|---|---|---|
| 2026-09-17 | T1.1 实机验收（DoD 全过，见 §3 T1.1） | ✅ 通过 |
| 2026-09-17 | T1.2 实机验收（echo 全链路 + DLQ + 热切换） | ✅ 通过 |
| 2026-09-17 | T1.6 验收（三级降级单测 + 真实 KB 文本级检索） | ✅ 通过 |
| 2026-09-17 | T1.3 实机验收（MQ 全链路 IR0→IR1） | ✅ 通过 |
| 2026-09-17 | T1.4/T1.5/T1.7 验收（e2e：需求→STEP 落 MinIO，OCC 重读通过） | ✅ 通过 |
| — | **阶段 1 整体验收**：自动化验收全过；**待人工确认**（建议补：真实 LLM key 就绪后重跑 e2e 观察 A3 真实生成/修复行为） | ⏳ 待人工验收 |

## 6. 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-17 | 初版（ZCode）：范围、拓扑端口、T1.1 详细设计与验收标准 |
| v2.0 | 2026-09-17 | 升级为完整阶段开发文档：任务级 DoD/验收、里程碑 M1-1~M1-5、依赖顺序、风险对策、阶段验收流程与记录；回填 T1.1 验收结论 |
| v2.1 | 2026-09-17 | T1.2–T1.7 全部任务 DoD 回填；里程碑 M1-1~M1-5 达成；阶段验收记录：自动化全过、待人工确认（真实 LLM key 后复验 A3） |
