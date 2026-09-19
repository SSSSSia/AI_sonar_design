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

**实现落地（2026-09-18 视觉打磨，依据 anthropics/skills · frontend-design）**：
- `frontend/src/styles/theme.css`：mockup.css token 同源落地（CSS 变量 + AntD 细节校准 + 工具类 `.mono/.page-head/.row`）；系统字体栈不引网络字体；`prefers-reduced-motion` 全局尊重；`:focus-visible` 焦点可见。
- `main.tsx` ConfigProvider：灰阶/语义色/边框发丝线/13px 密度/controlHeight 30 整体映射进 AntD token（Layout/Menu/Table/Card/Statistic/Tabs 等组件级）。
- 品牌细节：石墨底声呐同心环标记（装饰非蓝）、mono `v2` 章、顶栏 sticky、内容区 max-width 1320 居中；`<768` 侧栏收进 Drawer 抽屉导航（对齐 mockup 批注）；通知铃铛图标化。
- 登录页身份时刻：左侧品牌面板 = 中性灰声呐扫描纹样（同心环 + 9s 缓扫，reduced-motion 静止）+ 一句产品陈述 + mono 管线链徽章；`≤1100px` 品牌回退进卡片。蓝色仅「登录」按钮。
- 管线时间线（产品身份所在）：颜色即状态语义（蓝=进行/通过，琥珀=待裁决+gate-pulse 脉冲，红=失败，灰=未到），节点码/百分比 mono，连接线 pending 降为发丝灰。
- 自审对齐 skill 反模板清单：无渐变、无 ALL-CAPS 眉题、无阴影卡片套件（边框优先）、无装饰蓝、间隔点元信息改为逗号句读。

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
| M4.2 | T4.2 完成 | 完成（2026-09-18，真实管线全流程 UI 走查通过：gate1 批→A3/A4→gate2 驳回回流→gate1 二次过闸→gate2 批→A5→知识关写入） |
| M4.3 | T4.3 / T4.4 完成 | 完成（2026-09-18） |
| M4.4 | 真实样例走查 + 收官回填 | 走查 1 组完整真实管线（含驳回回流）已在 T4.2 完成；其余 2 组待人工验收（§5） |

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
| 2026-09-18 | T4.2 实机验收 | **通过**：需求输入页（项目快建/IR0 固化/自动 START）；会话工作台四页签（总览时间线/审阅参数/校验报告/三维占位）；参数表 source_type 四色徽标 + 行点击溯源抽屉（真实 trace API：IR0 原文 + A1 检索引用 + A2 推导 payload）；gate 裁决面板（批准/驳回+回退目标+批注必填）。**真实管线全流程走查**：gate1 批→A3/A4 真实运行→gate2 驳回（回退 A2，失效 IR2–IR5 四份下游快照，Toast 如实回显）→A2 局部回算→gate1 二次过闸（IR2 v2）→A3 重跑→gate2 批→A5→知识关批准（5 条 new 写入 Neo4j，duplicate 放弃，writeback: written）。修复：裁决批注 jsonb 字符串归一化渲染（曾致报告页签崩溃）；会话 id 双形态归一化（setId 去横线） |
| 2026-09-18 | T4.3 实机验收 | **通过**：STEP→glTF 转换服务（py-engine /api/v1/cad/*，gateway 新路由）+ R3F 预览 + 预签名下载。**性能实测驱动架构**：OCC 导入仅 1.6s，但大装配镶嵌对容差不敏感（0.5mm→18min/394万面；25.9mm→12min/169万面）→ 采用「POST 触发后台线程转换 + GET 轮询 + MinIO cad-previews/{sid}/{version}.glb 按版本缓存」，首次转换数分钟如实提示，之后秒开。实机走查：48 元拖曳阵模型（86m 全展开）浏览器可交互预览（旋转/线框），STEP 预签名下载（15min）与脚本 data-url 下载带 JWT 校验。依赖决策：改用 trimesh 组 GLB（原计划手写，可靠性优先，轻量纯库）；⚠ gateway yml 改动必须重新 package（jar 内配置不热更） |
| 2026-09-18 | T4.4 实机验收 | **通过**：知识库管理五页签实机走查——文档库（4 个真实文档 indexed、OCR 如实列、级联删除、增量构建）、抽取确认（按文档/状态过滤 + 逐条确认驳回）、三库管理（公式库真实 SymPy 表达式 + 启停 + 军标空态如实）、LLM 供应商（掩码回显 + 热重载）、D3 力导向图谱（真实 Neo4j 采样 + 邻域查询 + 图例）；系统管理页（经网关真实探测四服务 + /api/v1/kb/status 知识库现状 + 待接项如实）。后端补 GET /api/v1/kb/status（路由前缀曾双写 /kb/kb 已修）。图谱节点 type 着色未完全映射（数据键名差异）记优化项 |
| 2026-09-18 | 阶段 4 收官 | 代码/测试/文档全部推送至 306e254+；测试基线：notify 5 + workflow 19 + 前端 vitest 13 全绿；**阶段 4 待人工验收**（建议走查项见 HANDOFF） |
| 2026-09-18 | 视觉打磨（frontend-design skill） | **通过**：安装 anthropics/skills（vendor 克隆 + frontend-design/webapp-testing/theme-factory 装入用户 skills 目录），按其原则落地 §2.5「实现落地」清单——主题 token 全量映射、品牌声呐环标记、登录页扫描纹样（中性灰非蓝、reduced-motion 静止）、管线时间线状态配色、`<768` 抽屉导航。浏览器实测 1440/1024/800/640 四档截图走查（登录/仪表盘/工作台/审阅参数表）；tsc + vitest 13 + vite build 全绿。修复页头 `.row` 工具类缺失导致的换行 |
| 2026-09-18 | 优化项清账（图谱着色 + 实时日志流） | **通过**：①图谱节点着色——根因是前端读 `type` 而后端类型在 `label`（实测 300 节点采样后按真实 10 类重排色板，节点文本改用 name）；②worker 日志流——`_publish_log` 布点 a1–a5（开始/完成/降级/失败），前端 logStore + 总览「实时日志」面板，实机验证 A2 完成行实时入面板；契约 log.level 加法演进补 `ok`（schema+类型+worker 三侧同 commit）。顺手修 IR 快照「vv1」渲染。tsc + vitest 13 全绿 |
| 2026-09-18 | §4.4 真实样例走查预跑（2 组） | **通过**：组1 正常流全链（新需求 44 元/500Hz–8kHz，A1→…→A5→知识关写入→3D GLB 31.2MB 缓存命中）+ 组2 驳回回流（32 元/2m，gate1 驳回→A2 局部回算→下游失效→二次过闸→A5 completed）。实时日志面板全程可见步骤推送。人工复核仍待用户（验收建议见 HANDOFF） |
| 2026-09-19 | 设计审查报告偏离项裁定（报告 P2-11/P2-13，`docs/reviews/2026-09-19-全站设计审查.md`） | **合理偏离，记录在案**：①需求输入页无「存为草稿」——草稿需后端持久化支撑，当前无该 API，按 A-16 不做前端暂存式假草稿；②系统管理页将 mockup 12 卡健康网格简化为 4 行服务可达表——gateway/notify/worker 等基础设施健康属 deploy 编排健康检查域，前端逐卡探测徒增噪音，已在页内注明以 deploy 编排为准 |

## 6. 修订记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v0.1 | 2026-09-17 | 规划版：任务分解框架 + DoD 框架 |
| v1.0 | 2026-09-17 | 完整版：页面/路由/API 契约、WS 消费约定、视觉基调锁定（浅色中性，用户确认）、T4.3 后端小增量设计、风险表 |
| v1.1 | 2026-09-18 | 视觉打磨：§2.5 增「实现落地」（anthropics/skills · frontend-design 原则 + theme.css/token 映射清单）；§5 增验收行 |
