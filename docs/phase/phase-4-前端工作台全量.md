# 阶段 4 开发文档 · 前端工作台全量（完整版）

| 项 | 内容 |
|---|---|
| 文档编号 | PHASE-004 |
| 版本 | v1.0 完整版（2026-09-17 开工） |
| 关联 | CONST-001 §7.3（UI 专项约束）、D-02、P3 §5 功能清单、roadmap 阶段 4 |
| 阶段目标 | React 工作台覆盖全部核心场景：需求输入、任务进度、审阅裁决、参数表、校验报告、知识库、交付物、三维预览、系统管理基础 |
| 前置 | 阶段 1–3 的 API 全部就绪；**每个页面前先出 mockup 给用户确认再写码（宪法 §7.3，硬约束）** |

## 1. 阶段定位与边界

**定位**：React SPA（`frontend/`），经网关 8080 消费阶段 1–3 的全部既有 API；本阶段**不新增业务语义**，只做呈现与操作入口。唯一后端增量见 §2.6（三维预览的转换/下载端点）。

**范围内**：登录、主布局壳、仪表盘、需求输入、会话工作台（总览/审阅/参数表/校验报告/知识沉淀）、三维预览+交付物、知识库管理（文档管线+抽取确认+三库+供应商+图谱）、系统管理基础。

**范围外/如实待接（A-16）**：
- 「三维预览点击反向定位」——规划即明确暂缓（roadmap T4.3）；
- 用户/角色管理页——auth 服务无注册与用户管理 API（仅登录+自省），本阶段不做假页面；
- 日志检索/备份恢复——后端无对应服务，系统管理页如实标「待接」。

## 2. 阶段架构与设计决策

### 2.1 技术栈与工程约定（D-02 锁定）

- React 19 + TypeScript + Vite 6 + Ant Design 5 + Zustand + TanStack Query + axios + react-router-dom + dayjs；React Three Fiber（三维）、D3（图谱）、ECharts（图表）、Monaco（脚本查看）按页引入；测试 vitest。
- 目录：`frontend/src/{api,stores,components,pages,ws,utils}`。
- dev 代理：`/api → localhost:8080`（既有）；**新增 `/ws → localhost:8084`（ws:true）**，生产经 Nginx `/ws/`（deploy/nginx/nginx.conf 已配）。
- 统一响应包解包：`{code, message, data, traceId}`，`code != 0` 抛业务错误；401 清凭证跳登录。
- 角色守卫：admin / designer / reviewer / viewer（路由级 + 按钮级，评审裁决按钮仅 admin/reviewer 可见）。

### 2.2 页面清单与路由表

| 路由 | 页面 | 任务 | 备注 |
|---|---|---|---|
| `/login` | 登录页 | T4.1 | POST /auth/login |
| `/` | 仪表盘（项目/会话总览+任务进度+通知中心） | T4.1 | WS 实时 |
| `/sessions/new` | 需求输入（提交 IR0） | T4.2 | designer/admin |
| `/sessions/:id` | 工作台·总览（管线时间线+日志） | T4.2 | 默认 tab |
| `/sessions/:id/review` | 审阅（IR1/IR2/IR3 对照 + 参数表 + 裁决面板） | T4.2 | gate1/gate2 |
| `/sessions/:id/report` | 校验报告（三级风险）+ 知识沉淀候选 | T4.2 | IR4/IR5 |
| `/sessions/:id/model` | 三维预览 + 交付物下载 | T4.3 | 依赖 §2.6 |
| `/kb` | 知识库管理（文档管线 / 抽取确认 / 三库 / 供应商 / 图谱，5 个 tab） | T4.4 | |
| `/admin` | 系统管理基础（服务健康指标） | T4.4 | 日志/备份如实待接 |

### 2.3 页面 → API 映射契约（后端已全部就绪）

| 页面 | 端点 |
|---|---|
| 登录/守卫 | `POST /api/v1/auth/login`、`GET /api/v1/auth/me` |
| 仪表盘 | `GET /api/v1/projects`、`GET /api/v1/workflow/sessions`、WS `/api/v1/ws/sessions/{sid}` |
| 需求输入 | `POST /api/v1/projects`、`POST /api/v1/workflow/sessions`（requirement→IR0 快照）、`POST .../events {START}` |
| 工作台总览 | `GET .../sessions/{id}`、`GET .../allowed-events`、`POST .../events`（START/PAUSE）、WS 进度与日志 |
| 审阅+参数表 | `GET .../irs/{IR0..IR3}`、`GET /api/v1/trace/{traceId}`、`GET/POST .../reviews/{gate1\|gate2}`（body: `{decision, annotations[], reworkStage?}`） |
| 校验报告+知识沉淀 | `GET .../irs/IR4`（overall+issues，severity 三级）、`GET .../irs/IR5`（candidates+writeback_status）、`POST .../reviews/knowledge`、`GET .../reviews`（历史） |
| 三维预览+交付物 | `GET .../irs/IR3`（step_file/cadquery_script）→ §2.6 新端点 |
| 知识库 | `/api/v1/kb/documents*`（上传/列表/删除/级联清理回显）、`.../chunk`、`.../extract`+`/kb/extract/{task_id}`、`.../extractions`+`PATCH /kb/extractions/{id}`、`POST /kb/build`+`/kb/build/{task_id}` |
| 三库/供应商 | `GET/POST/PATCH/DELETE /api/v1/kb/registry/{formulas\|constraints\|standards}`、`/api/v1/kb/llm-providers*`（api_key 只写、返回脱敏） |
| 图谱 | `GET /api/v1/kb/graph?limit=300`、`GET /api/v1/kb/graph/neighborhood?key=&depth=2`（Neo4j 不可达 503 如实提示） |
| 系统管理 | `GET :8090/healthz`、`GET :8090/knowledge/status`（各层可用性+doc_stats，如实展示） |

### 2.4 WS 消费约定（contracts/ws/ws-message.schema.json）

- 连接：`/api/v1/ws/sessions/{sid}`（dev 经 vite 代理 `/ws` → 8084；生产经 Nginx）。仪表盘订阅「会话列表中所有活跃会话」；工作台订阅当前会话。
- 四类消息的处理：`task_progress`（stage ∈ A1..A5/review_gate_1/2，status 含 **degraded**，进度条+状态徽标，degraded 如实文案）；`log`（追加工作台日志面板，按 level 着色，不含密钥由后端保证）；`review_request`（通知中心红点 + 对应会话卡片高亮「待审阅」）；`ir_updated`（`queryClient.invalidateQueries` 对应 IR 缓存）。
- 重连：指数退避 1s→2s→4s…上限 30s；重连成功后拉会话列表/详情做 HTTP 对账；连接状态在 UI 显式展示（已连接/重连中/已断开-轮询降级 5s）。

### 2.5 视觉基调（mockup 阶段锁定，2026-09-17 用户已确认）

- **浅色中性（Notion/Zotero 式）**：白底卡片 + `#F5F6F8` 内容区 + 浅灰侧栏；文字 `#1F2329/#57606A` 两级灰阶；**主蓝 `#2563EB` 仅用于按钮/链接/激活态/可点击元素**，禁止装饰性用蓝。
- 语义色（非装饰）：成功绿 / 警告橙 / 错误红 / 中性灰；`degraded` 用橙灰。
- **source_type 四色徽标**（不含主蓝，避免与交互语义混淆）：`formula`=紫 `#6E56CF`、`case`=青 `#0E8088`、`standard`=绿 `#2B8A3E`、`estimated`=橙 `#D97706`；参数行可点击溯源，hover 出链接 affordance。
- 全中文界面；等宽字体仅用于代码/trace_id/对象键。

**内容降噪（2026-09-17 用户反馈，硬性）**：
- 每个卡片/区块正文最多 **1 句**说明文字；解释性长文案一律收进 tooltip（图标?）、帮助浮层或空态引导，不平铺在页面上；
- 表格默认只显示核心列，次要信息（推导式、依据说明、子状态）进展开行或点击后的抽屉/详情；
- 留白优先：卡片间距 ≥16px、表格行高宽松、避免同级超过两种字号加粗；无数据区域用一句空态文案，不堆砌说明。

**响应式布局（2026-09-17 用户反馈，硬性）**——基于 AntD 栅格断点：
- `≥1200px`：完整布局（侧栏 216px + 内容区，双栏卡片网格）；
- `768–1199px`：侧栏收窄为可展开抽屉（collapsed Sider），双栏网格全部纵向堆叠为单列，统计卡 2 列；
- `<768px`：单列堆叠，数据表格转为卡片列表（每个实体一张卡），顶栏操作收敛进「更多」菜单；
- 布局必须随窗口尺寸动态重排（flex/grid，禁止固定像素宽表格横向溢出不处理）；三维视口与图谱容器随父容器自适应。

### 2.6 T4.3 后端小增量（唯一新增端点，随 T4.3 commit）

- **spike 先行**：验证 cadquery `Shape.tessellate()` → 自组 glTF 2.0 GLB（无新 Python 依赖；量级小）。不可行再评估 assimp 管线，不引入新重服务。
- `py-engine/api/cad.py`（挂 `/api/v1`，复用 `api/authz.py` 信任头）：
  - `GET /api/v1/cad/sessions/{sid}/gltf` → 读 IR3 `step_file{bucket, object_key}` → MinIO 取 STEP → tessellate → GLB 字节流；转换结果缓存至 MinIO 桶 `cad-previews/{session_id}/{ir_version}.glb`，命中直接回（带 `X-IR-Version` 头）。
  - `GET /api/v1/cad/sessions/{sid}/download?kind=step|script` → 校验登录（session:read 语义）→ MinIO `presigned_get_object`（15 分钟）→ 302。
- gateway 增路由 `cad`：`/api/v1/cad/** → 8090`（改配置需重启 gateway；**严禁服务运行中 mvn package**）。
- 权限：两端点要求有效登录（网关 JWT + X-User-Id），无匿名下载。

### 2.7 状态管理与数据获取约定

- TanStack Query 管 server state，query key 约定：`['projects']`、`['sessions', filters]`、`['session', id]`、`['ir', sid, type]`、`['reviews', sid]`、`['trace', traceId]`、`['kb', ...]`；kb 异步任务（extract/build）用 `refetchInterval` 轮询任务端点。
- Zustand 管客户端状态：`authStore`（token/user/roles）、`wsStore`（连接状态、通知列表）。
- 通知中心：内存 + localStorage 持久化未读红点（后端无通知表，如实——刷新后保留未读标记，历史靠评审记录 API）。

## 3. 任务分解

### T4.1 登录 + 主布局壳 + 仪表盘 + WS 推送 + 通知中心

- **接口契约**：§2.3 前三行；WS §2.4。
- **要点**：登录页（记住用户名）；主布局壳（浅色侧栏 + 顶栏 + 通知铃铛）；仪表盘项目/会话双卡片列表（状态徽标 pending/running/paused/completed/failed + currentStage）；活跃会话进度条 WS 实时；通知中心（review_request 红点、点击跳会话）。
- **DoD**：双浏览器会话下任务进度实时可见；断线重连恢复（重连后数据对账一致）；vitest 覆盖响应包解包/角色守卫/ws 重连。

### T4.2 需求输入 + 审阅界面 + 参数表 + 校验报告 + 知识沉淀

- **接口契约**：§2.3 会话/IR/评审/trace 各行。
- **组件分解**：`PipelineTimeline`（A1→A2→gate1→A3→A4→gate2→A5 状态机可视化，当前节点高亮，暂停节点显示裁决入口）；`ParamTable`（source_type 四色徽标 + confidence + 点击行开 `TraceDrawer`）；`TraceDrawer`（trace 链步骤时间线 + IR0 原文 + IR2 参数全貌）；`GateDecisionPanel`（批准/驳回；驳回必填批注 + reworkStage 选择：gate1→A1|A2、gate2→A2|A3，IR4 有 suggestion 时预选并说明）；`ValidationReport`（error/warning/info 三级分组，error 显示回退建议，军标 0 条如实「待补」）；`IR5Candidates`（逐条确认/驳回提示 + writeback_status 徽标，pending 未确认不得入图文案 A-10）。
- **DoD**：正常流 + gate1/gate2 驳回回流 UI 走查通过；参数 100% 可反查（每行 trace_id 可开抽屉）。

### T4.3 三维预览 + 交付物下载

- **接口契约**：§2.6 新端点 + `GET .../irs/IR3`。
- **要点**：R3F 预览（轨道旋转/缩放/线框切换/重置视角）；加载态与转换失败如实报错（含 degraded 说明）；交付物卡片：STEP 下载、CadQuery 脚本（Monaco 只读查看 + 下载）。「点击反向定位」不做。
- **DoD**：标准样例 STEP 浏览器可交互预览；下载链路带权限校验（未登录 401）。

### T4.4 知识库管理 + 三库/供应商 + 图谱 + 系统管理基础

- **接口契约**：§2.3 kb 相关各行。
- **要点**：文档列表（格式/状态机 uploaded→parsed→chunked→indexed 徽标 + ocr_pending/ocr_pages 如实列 + 失败原因展示）；上传（拖拽，同步解析结果 toast 含 ocr 情况）；删除二次确认并回显级联清理结果（minio/vectors/graph）；抽取预览表（实体/关系逐条确认/驳回，按状态过滤）；构建触发（增量说明）+ 任务进度轮询；三库 CRUD 表格（启停开关 + confidence 徽标，军标 check=None 显示注册态「待自动化检查」）；LLM 供应商管理（api_key 只写不回显）；D3 力导向图（节点按类型着色 + source_doc 提示，点击节点加载邻域子图，Neo4j 不可达 503 如实提示）；系统管理页（py-engine 健康 + knowledge/status 各层可用性 + KB 统计；日志/备份行标「待接」）。
- **DoD**：知识库现状与后端完全一致（A-16，status 数字与 /knowledge/status 一致）；管理页 CRUD 走通。

## 4. 阶段管理

### 4.1 里程碑

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M4.0 | mockup 全量出齐 + 用户确认（§7.3 闸门） | 完成（2026-09-17 确认通过，附响应式+降噪两条意见已回填 §2.5） |
| M4.1 | T4.1 完成 | 完成（2026-09-18，含 notify 服务后端落地） |
| M4.2 | T4.2 完成 | 待开始 |
| M4.3 | T4.3 / T4.4 完成 | 待开始 |
| M4.4 | 真实样例走查 + 收官回填 | 待开始 |

### 4.2 顺序与并行

mockup 一次全量确认（用户已选定该方式）→ T4.1 → T4.2 → T4.3 → T4.4（T4.3 的后端 spike 可与 T4.2 并行验证）。

### 4.3 风险与对策

| 风险 | 对策 |
|---|---|
| STEP→glTF 转换依赖 | 优先 cadquery tessellate 自组 GLB（无新依赖）；备选 assimp；§2.6 spike 先行 |
| antd 5 + React 19 兼容 | 引入 `@ant-design/v5-patch-for-react-19`（官方补丁），T4.1 即接好并用 Modal/Message 冒烟 |
| WS 穿代理 | dev vite `ws:true` 提前验证；生产 Nginx `/ws/` 已有 upgrade 配置；HTTP 轮询降级兜底 |
| npm 安装慢 | HANDOFF 已记录（≈9 分钟/135 包）；一次 install 全量依赖，避免多次增量 |
| 网关无 CORS | dev 全走 vite 代理同源；生产同域经 Nginx，无需 CORS |

### 4.4 UI 验收流程（宪法 §9.4）

每页：mockup 确认 → 实现 → 真实样例走查（至少 2~3 组真实设计需求，覆盖正常流 + 审阅关回退流）→ 用户确认记录回填本文件 §5。

## 5. 阶段验收记录

| 日期 | 事项 | 结论 |
|---|---|---|
| 2026-09-17 | Mockup 全量 9 页确认（§7.3 闸门） | **通过**，附两条意见：①实现必须响应式动态布局；②页面文字偏多显杂乱，需内容降噪——已回填 §2.5 并修订 mockup |
| 2026-09-18 | T4.1 实机验收 | **通过**：notify 服务从零落地（JWT 握手验签 HandshakeInterceptor + 会话频道广播 + 内部发布信任头）；workflow 关键转移/worker 快照落库接线发布；真实管线走查（创建会话→A1→A2→gate1 暂停）浏览器实时可见，review_request 进通知中心（红点/未读高亮）；notify 重启后前端指数退避自动重连并重新注册订阅；分页 total=0 缺陷修复（MP 分页拦截器）。单测：notify 5 + workflow 19 + 前端 vitest 13 全绿。双浏览器并行会话走查归入 §4.4 收官走查 |

## 6. 修订记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v0.1 | 2026-09-17 | 规划版：任务分解框架 + DoD 框架 |
| v1.0 | 2026-09-17 | 完整版：页面/路由/API 契约、WS 消费约定、视觉基调锁定（浅色中性，用户确认）、T4.3 后端小增量设计、风险表 |
