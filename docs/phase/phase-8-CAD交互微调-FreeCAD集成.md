# 阶段 8 开发文档 · CAD 交互微调（FreeCAD 集成）

| 项 | 内容 |
|---|---|
| 文档编号 | PHASE-008 |
| 版本 | v1.0（2026-09-24，完整版；决策依据 ADR-004） |
| 关联 | ADR-004（主决策）、ADR-003（否决项/留观项延续）、宪法 A-02/A-11/A-16、§5.4（API 基线）、§10.1（secrets）、§10.3（沙箱）；roadmap 阶段 8 |
| 阶段目标 | 打通「平台生成 → FreeCAD 内人工微调 → 定稿回平台 → 审阅关②」闭环：设计人员在 FreeCAD 中点击/查看平台生成的三维模型，用自然语言对话微调；每轮微调产生可追溯的模型版本，审阅的即交付的 |
| 验收形态 | 后端协议层：单测 + fake 插件客户端 e2e（无 FreeCAD 依赖）；插件侧：FreeCAD 真机人工验收清单（本机 D:\Download\FreeCAD 可装） |

---

## 1. 阶段定位与边界

- **为什么有这个阶段**：现有 A3 是「生成即定型」——LLM 生成 CadQuery 脚本、子进程沙箱出 STEP，前端只读预览。导师要求（用户原话转译）：*设计人员可以点击操作生成的三维图，然后用自然语言对话来微调*。这要求把「定型」变成「可对话迭代」，并把迭代现场放进真实 CAD 软件。
- **做什么**：FreeCAD 插件（薄客户端）+ py-engine `cad_agent` 模块（大脑）+ 微调会话/版本域模型 + `/api/v1/cad-agent/**` + WS `cad_tune_updated` + 前端微调入口与版本时间线 + gate2 衔接。
- **不做什么（一期）**：参数化特征树编辑（改尺寸不动拓扑——二期）；视觉自动校验闭环（ADR-003 留观，重启条件未触发）；商用 CAD（SOLIDWORKS/NX）适配层（文件级 STEP 互通天然可用，插件层后置）；Web 端微调（二期可选）；插件 Addon Manager 打包发布（package.xml 后置）；装配级语义编辑（一期编辑对象为单文档形体）。

## 2. 阶段架构与设计决策

### 2.1 调研结论（2026-09-24，支撑决策的证据）

**① 现有链路事实**（集成切入点）：
- A3 产物 = STEP（`step-models/{sid}/sonar_model.step`）+ CadQuery 脚本（`cad-scripts/{sid}/generated_script.py`，且全文内嵌 IR3）+ GLB 预览（`cad-previews/{sid}/{version}.glb`，按 IR3 版本缓存、异步转换）。前端 ModelTab 已有 R3F viewer（旋转/缩放/线框），无点选语义。
- `ir_snapshots` 是 (session, ir_type) 内 max+1 的**不可变追加版本链**（`ir_store.save_snapshot`）——微调每轮追加 IR3 新版本与既有机制零冲突；`ReviewRecord.ir_snapshot_id` 让 gate2 裁决天然绑定版本。
- `geometry_inspect.py`（阶段 7）已能从 STEP 实测整体/分段几何——微调版本复用同一提取链进 IR3，A4 数值复核自动覆盖微调结果。
- 全链路无 FreeCAD（cadquery 为 pip 依赖）；WS 推送基建（notify 8084，`ir_updated`）现成。

**② CadAgent 插件资产盘点**（`D:\Download\FreeCAD\Mod\CadAgent`，只读参考；~5300 行 + 576 测试用例）：

| 处置 | 模块 | 说明 |
|---|---|---|
| **复用（近原样）** | `agent/cq.py` + `agent/cad_helpers.py` | CQ 风格 Workplane → FreeCAD Part 翻译层，布尔安全封装，有专项测试 |
| | `core/quality.py`、`agent/code_fixes.py`、`agent/references.py` | 结构化质量门禁（pass/warn/fail）+ 错误分类修复提示 + 渐进式上下文 |
| | `core/doc_analyzer.py`、`core/geometry_analyzer.py` | 文档几何提取/分析（孔阵、对称、壁厚）——LLM 感知模型状态的依据 |
| **改造** | `agent/loop.py` | 纯逻辑状态机（无 Qt/FreeCAD 依赖）迁 py-engine；`core.config` 模块常量读取改注入式；本地工具执行改工具协议 |
| | `ui/panel*.py`、`chat_renderer.py`、`theme.py` | 聊天面板保留；`_LlmCallThread`（直连 LLM）换成后端长轮询客户端 |
| | `agent/tools.py` | 瘦身为纯执行端：`execute_edit_code` 保留；`undo_last`→平台版本回退；`export_step`→定稿上传；`capture_view`/`analyze_image` 一期移除 |
| **废弃** | `core/llm_client.py`、`vision_client.py` | LLM 直连废弃（插件零凭据）；`.env` 明文 API key 随之根除 |
| | `core/session_store.py`、`core/snapshot.py`（本地落盘部分） | 会话/快照改平台存储（PG + MinIO）——「会话-文档脱节、快照关机即删、无版本」三缺口由平台根治 |
| | `settings_dialog.py` 服务商预设 | 换成平台登录（URL + 账号） |

**③ 业界盘点**（要点；完整来源清单见调研底稿）：
- 厂商原生 AI：Onshape AI Advisor 官方书面否认能生成设计；AutoCAD AI 为块搜索/标记辅助；Zoo（KittyCAD）Text-to-CAD/Zookeeper 是唯一成熟的 NL 建模商业实现（绑定自有内核与 KCL）；中望 2026-09 刚发布 CAx AI Agent 平台。→「外部 Agent 接设计人员的 CAD + 自然语言微调」无厂商占领，自研空间真实存在。
- 集成范式：`CAD 内薄插件 + RPC + 后端大脑` 是 2025-2026 事实标准（freecad-mcp 2455★、cadpilot、各商用 CAD MCP 桥共 215+ 仓库）；**插件发起连接**（非平台直连桌面）是部署现实。
- 技术路线共识：结构化工具为主 + 代码执行回退；生成后必须执行验证（质量门禁/几何实测）；直接 `exec` 需沙箱/黑名单/撤销事务。参数化编辑可靠性最高（LLM4CAD-Editor，arXiv:2606.20607，参数级 96.3%，比脚本路线鲁棒 ~1.4×）但需特征树基建——一期以「增量编辑脚本 + 全链路留痕 + gate2 人工兜底」起步，参数化列二期。
- 【未核实项，如实记录】Dassault 3DEXPERIENCE Copilot、NX/Creo 原生 AI 的具体能力边界（官网反爬未能一手核实）——不影响本决策（目标 CAD 已定 FreeCAD）。

### 2.2 拓扑与数据流

```
设计人员桌面（FreeCAD ≥1.0）                服务器（现有平台）
┌──────────────────────────┐             ┌────────────────────────────────────┐
│ clients/freecad-plugin    │  HTTPS/JWT  │ gateway 8080                        │
│  (Mod: SonarCADLink)      │ ──────────→ │   └→ py-engine 8090                 │
│  · 登录（平台账号）        │  长轮询上行   │       └─ cad_agent/                 │
│  · 拉模型（STEP 导入）     │  事件下行    │          · loop.py（AgentLoop 迁移） │
│  · 聊天面板（ui/* 改造）   │             │          · quality/token_budget     │
│  · 工具执行器              │             │          · 工具调用状态机            │
│    (快照→exec→质量门禁)   │             │   PG: cad_tune_sessions/_rounds     │
│  · 定稿导出上传            │             │       /_tool_calls（V10）           │
└──────────────────────────┘             │   MinIO cad-tunes 桶（每版本产物）   │
        ↑ WS 事件（cad_tune_updated）     │   notify 8084 ← cad_tune_updated    │
        └──────────────── frontend 5173 ←─┘   （前端 ModelTab/ReviewTab 订阅）   │
                                                                          └（示意）
```

一轮微调的完整时序：

```
设计人员                插件                    py-engine                     LLM/存储
  │ 点选面+输入"这里加孔阵" │                        │                             │
  ├───────────────────────→│ POST /tune/{id}/messages│                             │
  │                        ├───────────────────────→│ 创建 round(running)          │
  │                        │                        ├────────────────────────────→│ AgentLoop：
  │                        │                        │   上下文=IR3 geometry_summary│ LLM 生成编辑代码
  │                        │   GET tool-calls 长轮询 │   +上轮 doc_summary+点选描述 │
  │                        │ ←──────────────────────┤ 工具调用 execute_edit_code   │
  │  视口模型即时变化        │ 快照→语法预检→exec→     │                             │
  │ ←──────────────────────┤ 质量门禁→cq_show        │                             │
  │                        ├───────────────────────→│ POST tool-calls/{id}/result  │
  │                        │                        │   (stdout/quality/doc_summary)│
  │                        │                        │ ←────────────────────────────┤ LLM 看结果，
  │  面板流式看到答复        │   （循环 0..N 次工具调用）│                             │ 继续修或结束
  │ ←──────────────────────┤ GET round（轮询状态）    ├────────────────────────────→│ 轮次结束→
  │                        ├───────────────────────→│ POST versions (STEP+FCStd+py)│
  │                        │                        │ geometry_inspect 实测         │
  │                        │                        │ → ir_snapshots IR3 v{n+1}    │
  │  WS: cad_tune_updated  │ ←──────────────────────┤ → round.result_ir3_version   │
  │ ←──────────────────────┼────────────────────────┤ → notify 推送                 │
```

### 2.3 数据模型（biz Flyway V10）

```sql
CREATE TABLE IF NOT EXISTS cad_tune_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    design_session_id uuid NOT NULL REFERENCES design_sessions(id) ON DELETE CASCADE,
    status varchar(16) NOT NULL DEFAULT 'active',     -- active|finalized|abandoned
    baseline_ir_version integer NOT NULL,             -- 建会话时的最新 IR3 版本
    note text,
    created_by varchar(64),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_cad_tune_sessions_design ON cad_tune_sessions(design_session_id);

CREATE TABLE IF NOT EXISTS cad_tune_rounds (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tune_session_id uuid NOT NULL REFERENCES cad_tune_sessions(id) ON DELETE CASCADE,
    round_no integer NOT NULL,
    baseline_ir_version integer NOT NULL,             -- 该轮微调的基线版本（回退后可非递增）
    prompt text NOT NULL,                             -- 设计人员自然语言输入
    selection jsonb,                                  -- 点选上下文（元素类型/bbox/名称）
    llm_trace jsonb,                                  -- 工具调用序列+结果摘要（A-02 溯源）
    script text,                                      -- 最终采纳的编辑脚本
    result_ir3_version integer,                       -- 定稿上传后回填
    status varchar(16) NOT NULL DEFAULT 'running',    -- running|awaiting_upload|done|failed
    error text,
    created_by varchar(64),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tune_session_id, round_no)
);

CREATE TABLE IF NOT EXISTS cad_tune_tool_calls (
    id bigserial PRIMARY KEY,
    round_id uuid NOT NULL REFERENCES cad_tune_rounds(id) ON DELETE CASCADE,
    name varchar(32) NOT NULL,                        -- 一期仅 execute_edit_code
    args jsonb NOT NULL,
    result jsonb,                                     -- {ok, stdout, quality, doc_summary, error}
    status varchar(16) NOT NULL DEFAULT 'pending',    -- pending|done|timeout
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);
```

MinIO 新桶 `cad-tunes`：`{sid}/{tune_id}/r{round_no}.step / r{round_no}.fcstd / r{round_no}.py / r{round_no}.png`（png 一期预留，selection 截图可选上传）。会话删除联动：`/cad/sessions/{sid}/artifacts` 清理清单加 `cad-tunes` 桶。

### 2.4 接口契约（py-engine，gateway 路由 `/api/v1/cad-agent/**` → 8090）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /cad-agent/sessions/{sid}/tune | 创建微调会话（校验 IR3 存在；baseline=当前最新 IR3 版本；同会话已存在 active 会话→409，返回既有 id） |
| GET | /cad-agent/sessions/{sid}/tune | 微调会话列表 + 轮次/版本链（前端时间线） |
| GET | /cad-agent/tune/{tid} | 会话详情（状态/轮次/版本/错误） |
| GET | /cad-agent/tune/{tid}/context?version= | 插件拉取模型上下文：当前（或指定）版本 STEP 预签名 URL + 几何摘要文本 + 版本号 |
| POST | /cad-agent/tune/{tid}/messages | {text, selection?} → 创建 round，返回 round_no；AgentLoop 转入后台线程执行 |
| GET | /cad-agent/tune/{tid}/rounds/{n} | 面板轮询：round 状态 + 已产出助手消息 + 工具调用进度 |
| GET | /cad-agent/tune/{tid}/tool-calls?cursor=&wait=25 | 插件长轮询：cursor 之后 pending 的工具调用；wait 秒内无新调用返回空数组（长轮询挂起） |
| POST | /cad-agent/tune/{tid}/tool-calls/{call_id}/result | 插件回传执行结果 {ok, stdout, quality, doc_summary, error?} |
| POST | /cad-agent/tune/{tid}/versions | 轮次收敛后插件上传：multipart(step_file, fcstd_file, script, round_no) → MinIO 落盘 → geometry_inspect → IR3 新版本 → 回填 round → notify 推送 |
| POST | /cad-agent/tune/{tid}/finalize | {note?} 定稿：status→finalized（gate2 可审最新版本） |
| POST | /cad-agent/tune/{tid}/close | 放弃：status→abandoned（已产生的版本保留，如实留痕） |

鉴权：全部 `Depends(require_user)`；写操作（创建/上传/定稿/放弃）要求 writer 角色（复用 `api/authz.py` 轻量角色检查）。**插件侧错误语义**：非法 round/version/会话状态 409/404 带中文 detail；服务重启时 running 轮次启动自愈扫描标记 failed（error=「服务重启中断，请重试」，A-16）。

工具调用协议（服务端→插件，`cad_tune_tool_calls` 行的 API 视图）：

```json
// 下发：GET tool-calls 返回项
{"call_id": "42", "round_no": 3, "name": "execute_edit_code",
 "args": {"code": "<LLM 生成的编辑脚本>"}, "created_at": "..."}

// 回传：POST tool-calls/{call_id}/result
{"ok": true, "stdout": "...", 
 "quality": {"status": "pass|warn|fail", "issues": ["..."]},
 "doc_summary": "当前模型几何摘要（doc_analyzer 产出，供下一轮上下文）",
 "error": null}
```

插件端执行契约（`execute_edit_code`）：预置命名空间 `model`（当前主形体）、`cq`（CQ 翻译层）、安全几何函数（safe_fuse/safe_cut 等）、`doc`；脚本必须赋值 `result`（Shape）；执行前 FCStd 快照 + `compile()` 语法预检；质量门禁 FAIL 不阻断轮次（结果如实回传，由 LLM 决定修复，与服务端 AgentLoop 的修复循环一致）。**诚实标注**：FreeCAD 主线程内执行代码无法强制超时（与 CadAgent 及业界 MCP 先例一致）——缓解=语法预检 + 面板 Stop 置中断标志（工具间检查）+ 服务端工具等待超时（`CAD_TUNE_TOOL_TIMEOUT_S`，默认 180s，超时标 timeout 并注入修复提示）。

WS 消息契约加法演进（`contracts/ws/ws-message.schema.json`）：type enum + `cad_tune_updated`；payload `{tune_id, round_no, ir_version, state}`（state ∈ round_done | version_ready | finalized | closed | failed）。复用 worker 既有 `_notify_ir_updated` 同路 HTTP→notify 发布。

### 2.4.1 服务端 AgentLoop 运行载体

- **API 进程线程池**（`ThreadPoolExecutor`，`CAD_TUNE_MAX_WORKERS` 默认 2）：工具等待是 DB 轮询（0.5s 间隔），非 CPU 密集；GLB 转换走 `subprocess.Popen` 已有「不任务化」先例。理由：低延迟（省 celery 派发）、实现简单；量大时迁 celery（演进项，不影响协议）。
- LLM 调用复用平台统一适配器（llm_providers 表热切换，与 A1~A5 同源）；上下文注入会话 kb_ids 的检索摘要（复用阶段 6 `GraphRAGService.query` scoped 语义），供微调参考领域知识。
- token 预算/消息裁剪：迁移 CadAgent `token_budget.py`（`CAD_TUNE_MAX_CONTEXT_TOKENS` 默认 24000）。

### 2.5 横切约定

- **溯源（A-02）**：每轮 prompt/selection/llm_trace（工具调用序列+结果）/script/版本号全部落 `cad_tune_rounds`；产物 MinIO 键含会话与轮次；IR3 新版本由 `save_snapshot` 追加（`created_by="cad_tune"`）。
- **诚实（A-16）**：STEP 导入丢参数历史、编辑脚本非参数化操作、主线程执行无强制超时——三处局限在插件 UI 与本文档如实标注；轮次失败/工具超时如实透因。
- **secrets（§10.1）**：插件零凭据（登录换 JWT）；`cad-tunes` 桶访问全部经服务端预签名。
- **交互蓝 #2563EB 仅操作语义**：前端微调入口/时间线遵循既有色彩规约。

## 3. 任务分解（T8.1–T8.7）

> 执行顺序：T8.1 → T8.2 → T8.3 → T8.4 → T8.5/T8.6（可并行）→ T8.7 收官。

### T8.1 数据模型与存储 ✅ 完成（2026-09-24）

- **目标**：微调域模型落库 + 产物桶 + 存储封装。
- **要点**：V10 三表（§2.3）；`app_config` 增 `BUCKET_CAD_TUNES`/`CAD_TUNE_TOOL_TIMEOUT_S`/`CAD_TUNE_MAX_WORKERS`/`CAD_TUNE_MAX_CONTEXT_TOKENS`；`storage/tune_store.py`（create_tune/get/list_rounds/create_round/update_round/create_tool_call/poll_tool_calls(cursor,wait)/complete_tool_call/expire_running_rounds 启动自愈）；`/cad/sessions/{sid}/artifacts` 清理加 cad-tunes 桶。
- **DoD**：
  - [x] V10 迁移实机过 + 幂等；三表 CRUD 单测 8 用例（真库，test_t81_tune_store.py）
  - [x] 工具调用 pending→done/timeout 状态机单测（长轮询 cursor/迟到回传拒收/超时注入/启动自愈）
  - [x] 会话删除联动清理加 cad-tunes 桶（端点桶清单扩展；MinIO 前缀清理逻辑随 e2e teardown 实测）

### T8.2 cad_agent 后端核心 ✅ 完成（2026-09-24）

- **目标**：大脑迁移——AgentLoop 在服务端跑起来，工具执行外包给协议。
- **要点**：`py-engine/cad_agent/`：`loop.py`（自 CadAgent `agent/loop.py` 迁移改造：配置注入、`tool_executor` 回调注入——服务端实现=写 tool_calls 行+轮询等待结果）、`quality.py`/`token_budget.py`/`code_fixes.py`/`references.py` 迁移（去 FreeCAD 依赖）；微调 system prompt（编辑脚本契约：`model`/`cq`/`result`、点选上下文解读、质量修复策略）；LLM 走平台适配器；kb_ids 检索摘要注入。
- **DoD**：
  - [x] fake 工具执行器单测 14 用例（test_t82_cad_agent.py）：happy path/问答轮/门禁阻断修复/逃逸 hatch/重复错误/未知工具协议层拦截
  - [x] LLM 不可用/超时如实降级（round=failed + error 透因，不伪造答复）
  - [x] token 裁剪与上下文构建（build_round_context：几何摘要+点选+kb 摘要）单测

### T8.3 API + gateway + 事件 ✅ 完成（2026-09-24）

- **目标**：§2.4 全部端点 + 网关路由 + WS 事件。
- **要点**：`api/cad_agent.py`；gateway `application.yml` 加路由；`ws-message.schema.json` 加法演进；versions 端点内联 geometry_inspect 子进程调用（复用 `CAD_INSPECT_TIMEOUT_S`）→ `_build_ir3` 同构组装（step_file 指向 cad-tunes 新键，cadquery_script=编辑脚本，features/geometry_summary 实测）→ `save_snapshot`；线程池与启动自愈。
- **DoD**：
  - [x] 端点级 API 实测（8090 直连 + 网关 JWT：创建/context 预签名/409/放弃/回填 + 单测 9 用例覆盖全部分支）
  - [x] 上传后 IR3 版本 +1（e2e 实测，真实 STEP 几何实测提取 features；GLB 走既有版本缓存键，转换本身未重复实测）
  - [ ] WS `cad_tune_updated` 浏览器走查——随人工验收（消息发布路径与 schema 校验已单测覆盖）
  - [x] 契约 schema 校验测试更新（jsonschema 正反用例）

### T8.4 FreeCAD 插件（clients/freecad-plugin/SonarCADLink）✅ 完成（2026-09-24，真机走查待人工验收）

- **目标**：CadAgent 迁移改造为平台客户端，三缺口根治。
- **要点**：目录 `clients/freecad-plugin/`（Mod 名 SonarCADLink；README 写安装=复制进 FreeCAD Mod 目录）；`platform_client.py`（登录/续期/长轮询/上传，urllib 无第三方依赖，沿用 CadAgent 风格）；面板登录态与设计会话选择（列本人 A3 完成的会话）；拉取 STEP 导入（`Import`/`Part.read`）；`_LlmCallThread`→`_BackendThread`（发消息+轮询 round/tool-calls+执行+回传，主线程执行 FreeCAD API 经队列转发，沿用 CadAgent 线程编排模式）；会话恢复：启动时按平台版本列表拉回 FCStd/STEP（**保存机制补全**——每版本自动落 MinIO，本地崩溃可恢复）；undo=重新导入上一版本。移除 llm_client/vision_client/本地 session_store/snapshot 落盘/服务商设置。
- **DoD**：
  - [x] 协议层单测 5 用例（stub HTTP server：登录门禁/错误透传/消息/长轮询/multipart 上传/下载）
  - [x] 真机人工验收清单 13 步交付（clients/freecad-plugin/README.md）——**真机执行待人工验收**
  - [x] 零凭据走查（仓库 grep 无 key/token 字面量；密码不落盘，仅平台 URL/用户名入 QSettings）

### T8.5 前端：微调入口 + 版本时间线 ✅ 完成（2026-09-24）

- **要点**：ModelTab：三维预览旁「在 FreeCAD 中微调」入口（无 active 会话→创建引导，含安装提示；有→显示会话状态/轮次）；版本时间线（轮次列表：prompt 摘要/脚本查看/STEP+FCStd 下载/IR3 版本号）；cad_tune_updated WS 订阅刷新预览。ReviewTab gate2：当前 IR3 版本展示 + active 微调会话警示条（「微调进行中，建议先定稿再裁决」）。
- **DoD**：
  - [x] tsc 零错 + vitest 23/23（tuneSummary 纯函数 5 用例）
  - [ ] 与插件+后端联调走查——随插件真机人工验收（后端协议闭环已由 e2e 覆盖）

### T8.6 workflow 衔接 ✅ 完成（2026-09-24）

- **要点**：会话详情回填微调状态（`CadTuneLookupEntity` 只读视图 + `fillTuneStatus`，同 `fillKbNames` 模式——status=active 的会话 id/轮次数）；gate2 驳回 back_to_A3 语义确认不变（驳回后 A3 重生成产生更新的 IR3 版本，微调会话按 baseline 失配提示重建）。
- **DoD**：
  - [x] 单测（有/无 active 微调会话两分支；workflow 32 测试全绿）
  - [x] 实机：网关建活跃微调会话 → 会话详情回填 tuneId/tuneStatus 实值（gate2 前端警示随联调走查）

### T8.7 收官 ✅ 完成（2026-09-24）

- **要点**：e2e `test_cad_tune_e2e.py`——fake 插件客户端驱动 HTTP 协议全流程：前置（登录/A3 完成的会话）→ 创建微调会话 → 消息（脚本断言含编辑契约）→ 长轮询取工具调用 → 回传伪造执行结果 → 轮次收敛 → 上传真实 STEP（取 A3 产物改名）→ 断言 IR3 版本+1、round 回填、GLB 新版本键、WS 推送 → finalize → gate2 冻结断言（review 记录 ir_snapshot_id=最新版）；全量回归（py + workflow + biz + frontend）；HANDOFF/roadmap/devlog/PHASE-008 验收记录回填；插件真机验收清单交付。
- **DoD**：
  - [x] e2e `test_cad_tune_e2e.py` 全绿（44s：mock LLM 自带随机端口+独立 provider 行，fake 插件客户端驱动协议全流程，gate2 冻结断言 ir_snapshot_id=微调版本快照）
  - [x] 四端全量回归绿（py 239+e2e / workflow 32 / biz 7 / frontend 23）
  - [x] 文档回填 + 分任务 Conventional Commits 推送；插件真机人工验收清单在 clients/freecad-plugin/README.md

## 4. 阶段管理

### 4.1 里程碑

| 里程碑 | 包含任务 | 判据 |
|---|---|---|
| M8-1 域模型与存储 | T8.1 | ✅ 达成（2026-09-24，V10 实机 + 8 用例） |
| M8-2 服务端大脑 | T8.2 | ✅ 达成（2026-09-24，14 用例） |
| M8-3 协议闭环（API 级） | T8.3 | ✅ 达成（2026-09-24，实机走查 + 9 用例 + e2e） |
| M8-4 插件可用 | T8.4 | ✅ 代码交付（真机执行待人工验收） |
| M8-5 全链路收官 | T8.5/T8.6/T8.7 | ✅ 达成（2026-09-24，e2e 44s 全绿 + 四端回归绿） |

### 4.2 依赖与顺序

T8.1→T8.2→T8.3 严格串行（协议逐层依赖）；T8.4 依赖 T8.3 定稿的协议；T8.5/T8.6 依赖 T8.3；T8.7 收口。现有服务无破坏性改动（V10 纯加法、新桶、新路由、契约加法演进）。

### 4.3 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 插件主线程执行代码无法强制超时 | 恶性脚本卡死 FreeCAD | 语法预检+快照+Stop 中断标志+服务端工具超时（§2.4 诚实标注）；文档建议设计人员保存工作习惯 |
| STEP 导入丢参数历史 | 微调只能「改形体」不能「改参数」 | 一期明示局限；几何实测进 IR3 保证数值真实；二期参数化路线已列入演进承诺 |
| LLM 编辑脚本质量参差（复杂拓扑成功率低，Text2CAD-Bench 结论） | 多轮修不完 | 质量门禁+修复循环+迭代上限；失败轮次如实 failed 可重试；gate2 人工兜底不变 |
| 长轮询穿透/服务重启丢轮次 | 插件挂死、状态悬空 | wait 上限 25s<网关超时；启动自愈 expire_running_rounds；插件断线重连幂等（cursor 语义） |
| 设计机环境差异（FreeCAD 版本/Python） | 插件装不上 | 目标 FreeCAD ≥1.0；插件零第三方 pip 依赖（urllib）；真机清单首列版本检查 |

### 4.4 验收流程（阶段末）

1. 逐任务 DoD 核对（本文档 + roadmap 双处一致）。
2. e2e `test_cad_tune_e2e.py` 一次走查；四端全量回归。
3. 插件真机人工验收（清单随 T8.4 交付）：设计人员视角从登录到定稿全流程。
4. 诚实性抽查：局限三处标注到位；失败轮次透因；A4 对微调版本数值复核生效。
5. 结论回填 §5 并同步 roadmap 与 HANDOFF。

## 5. 验收记录

| 日期 | 事项 | 结论 |
|---|---|---|
| 2026-09-24 | T8.1~T8.3（域模型/服务端大脑/API+网关+事件：19 单测 + 实机走查） | ✅ 通过 |
| 2026-09-24 | T8.4 插件代码交付（协议单测 5 用例 + 零凭据走查 + py_compile 全过） | ✅ 通过（真机执行待人工验收） |
| 2026-09-24 | T8.5/T8.6（前端卡片与警示 tsc+vitest 23 绿；workflow 回填实机验证 32 绿） | ✅ 通过 |
| 2026-09-24 | e2e `test_cad_tune_e2e.py`（44s：消息→工具回路→真实 STEP 上传→IR3 v2 实测提取→finalize→gate2 冻结断言） | ✅ 通过 |
| — | **阶段 8 整体验收**：自动化全过；**待人工确认**（插件真机 13 步清单走查 + gate2 浏览器 WS 走查 + 与插件联调） | ⏳ 待人工验收 |

## 6. 决策记录

| # | 决策 | 理由/备选 |
|---|---|---|
| D8-1 | 目标 CAD = FreeCAD ≥1.0 | 零许可+Python 全开放+既有资产；商用 CAD 列适配层（ADR-004 §2.1） |
| D8-2 | 大脑后置 py-engine，插件零凭据 | 复用 LLM 管理/溯源/存储；根治 CadAgent 明文 key 与状态三缺口 |
| D8-3 | 微调位置 = A3 后、gate2 前 | 审阅的即交付的；ir_snapshot_id 天然冻结 |
| D8-4 | 一期微调机制 = 增量编辑脚本 | 参数化特征树编辑列二期（需特征树基建；LLM4CAD-Editor 96.3% 数据支撑其优先级） |
| D8-5 | 通信 = 插件发起 HTTPS 长轮询 | NAT/防火墙友好；无入站依赖；WS 仅事件增强（复用 notify） |
| D8-6 | AgentLoop 载体 = API 进程线程池 | 工具等待为 DB 轮询非 CPU 密集；低延迟；GLB 子进程先例；量大迁 celery |
| D8-7 | 版本链复用 ir_snapshots（IR3 追加） | 零新版本机制；GLB 缓存/A4 复核/gate2 冻结全部自动兼容 |
| D8-8 | 产物桶 cad-tunes，键 `{sid}/{tune_id}/r{n}.*` | 与 A3 产物隔离；会话删除联动清理 |
| D8-9 | 插件代码迁入本仓库 clients/freecad-plugin/ | 协议可单测、同版本演进；原 CadAgent 仓库只读参考 |
| D8-10 | 不引入 CADDesigner/SimpleCADAPI 代码；vision 闭环不启动 | ADR-003 延续；重启条件未触发 |
| D8-11 | WS 新消息类型 cad_tune_updated（契约加法演进） | 前端预览自动刷新；信封/校验复用既有 schema |

## 7. 演进路线（承诺项，非本期范围）

1. **二期·参数化特征树编辑**：FreeCAD 侧维护参数化特征模型（Sketcher 约束/PartDesign 特征），LLM 输出参数变更而非脚本；预期可靠性显著提升（业界 96.3% vs 脚本路线）。
2. **二期·Web 端轻量微调**：ModelTab 内点选（需 GLB 分段命名导出）+ 参数级修改，复用同一 cad_agent 服务端与版本链。
3. **三期·视觉自动校验闭环**：截图→多模态复核（cadpilot/RA-CAD 验证的模式），视 ADR-003 §2.2 重启条件立项。
4. **三期·商用 CAD 适配层**：SOLIDWORKS（COM add-in）/NX（NXOpen）文件级互通先行；协议（工具调用/版本链）已按 CAD 无关设计。

## 8. 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-24 | 完整版定稿：调研结论（现有链路/CadAgent 资产三栏/业界盘点）、架构与契约（V10 三表、10 端点、工具协议、WS 事件）、T8.1~T8.7 任务分解、D8-1~D8-11 决策、演进路线；依据 ADR-004 |
| v1.1 | 2026-09-24 | 全任务收官回填：T8.1~T8.7 DoD 勾结、M8-1~M8-5 达成、e2e 全绿。执行期增补：① LLM 交互定案 ReAct 文本协议（共享 LlmAdapter 零改动、供应商无关；原版 tool calling 探测与 ReAct 回退收敛为 ReAct 单模式）；② quality.py/code_fixes.py 落插件侧（执行端关注点），服务端迁移 loop/prompts/references/token_budget/react_parser；③ 质量门禁放宽——纯问答轮合法结束（script=None 不触发上传），门禁只拦 FAIL 收尾；④ 微调版本 features 解除参数联动（人工编辑偏差交 gate2 裁决），脚本溯源=基线脚本+微调段拼接；⑤ AgentLoop 载体=API 线程池 + 启动自愈 expire_running_rounds |
| v1.2 | 2026-09-24 | 插件 UX 修正（用户真机反馈）：会话选择去掉手工复制粘贴 ID——登录后经网关拉 workflow 会话列表（GET /api/v1/workflow/sessions）客户端过滤可微调会话（paused@gate2 或 tuneStatus=active）自动填充下拉框，手输 ID 降为备用入口。仅插件侧改动（platform_client.list_sessions + panel 会话区），后端零改动 |
