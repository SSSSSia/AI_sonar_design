# 阶段 2 开发文档 · 人在回路与校验

| 项 | 内容 |
|---|---|
| 文档编号 | PHASE-002 |
| 版本 | v1.0（2026-09-17，由 v0.1 规划版升级为完整版） |
| 关联 | CONST-001 §2/§3/§5/§7（A-06/A-09/A-10/A-11/A-12/A-13/A-16）、P1 五步主线、P2 编排控制流、roadmap 阶段 2、阶段 1 产物（T1.1 状态机插入点 / T1.4 IR2 / T1.5 IR3） |
| 阶段目标 | 补齐 A4 合规校验、A5 知识沉淀与两道人工审阅关，形成"**需求解析 → 方案生成 → CAD 出图 → 合规校验 → 知识沉淀**"完整五步闭环 + 全链路溯源 |
| 验收形态 | 无 UI；审阅/裁决/溯源全部经 gateway（8080）API 走查；`tests/e2e/` 固化"通过流 + 驳回回流"样例 |

---

## 1. 阶段定位与背景

- **为什么有这个阶段**：阶段 1 已把"需求→STEP"在完整架构上跑通，但 A-11 要求的两道审阅关、A-09/A-10 要求的 A4/A5 尚缺，管线从 IR3 直接终止——人还无法在关键产物（参数表、建模结果）上把关，参数出处（trace_id）也无法反查。本阶段补齐五步主线中剩余两步与全部人工控制点。
- **输入**（阶段 1 遗留插入点）：
  | 插入点 | 现状 | 本阶段动作 |
  |---|---|---|
  | 状态机 `paused` + PAUSE/RESUME | 已定义、无触发方（T1.1 注释留待 T2.2） | STAGE_DONE 分支接线 gate1/gate2 |
  | `review_records` 表（gate1/gate2/knowledge、decision、annotations） | V1 已建表、零代码 | 实体/Mapper/Service/API 全量落地 |
  | `traceability_chain` 表（trace_id/step_no/actor/action/payload） | V1 已建表、零代码 | worker 写入 + 反查 API |
  | `contracts/ir/ir4.schema.json`、`ir5.schema.json` | 已有契约 + 契约测试，无生产者/消费者 | A4/A5 落地（IR4 契约微扩，见 T2.1） |
  | `gate:decide` 权限（seed 给 reviewer/admin） | 代码零引用 | 裁决 API 以 `hasAnyRole('admin','reviewer')` 落实该矩阵 |
- **阶段边界（做什么/不做什么）**：
  - 做：T2.1–T2.4（见 §3）；gate 通过流与驳回回流实测；溯源反查。
  - 不做：知识库文档管线与军标首样入库（阶段 3，本期军标框架占位）；审阅/溯源 UI（阶段 4，本期 API + curl 走查）；修复策略自学习/多方案比选（暂缓项）；WebSocket 审阅通知推送（ws-message 契约已含 `review_request`，服务端推送随阶段 4 前端一起激活，本期以 API 轮询代替——对 v0.1 规划的一处收敛，理由：无前端消费者的推送是无验收口的死代码）。

## 2. 阶段架构与服务拓扑

### 2.1 数据流总览（阶段 2 后的完整管线）

```
                                     workflow(8082) 状态机
  START ──→ running ── A1(IR1) ── STAGE_DONE ──派 A2──→ A2(IR2)
                                                │ currentStage=A2
                                                ▼
                                     ★审阅关① gate1：running→paused（人工裁决 IR2）
                                                │ approve → RESUME + 派 A3
                                                ▼
   A3(IR3,STEP) ── STAGE_DONE ──派 A4──→ A4(IR4 校验报告)
                                                │ currentStage=A4
                                                ▼
                                     ★审阅关② gate2：running→paused（人工裁决 IR3+IR4）
                                                │ approve → RESUME + 派 A5
                                                ▼
                                     A5(IR5 pending 候选) ── COMPLETE ──→ completed
                                                │
                                                ▼
                                     知识审阅关（gate=knowledge，会话已完成态）
                                        人工确认候选 → py-engine /knowledge/writeback
                                        → Neo4j MERGE（冲突旧实体标 deprecated）→ IR5 v2 written

  驳回（gate1/gate2）：RESUME + 按重跑目标重派（gate1→A2；gate2→按 IR4 suggestion 定 A2/A3）
                       + 下游 ir_snapshots 批量标记 invalidated_at（内容不变，A-12）
  溯源链：A2/A3/A4/A5 落快照时逐参数/特征写 traceability_chain；裁决写 confirm 行
          GET /api/v1/trace/{trace_id} 反查 → 链路步骤 + IR0 原文 / IR1 检索引用 / IR2 参数
```

- **门语义**：每次新生成 IR2（gate1）/IR3+IR4（gate2）都重新触发对应审阅关——每份新产物都必须人工过闸，无"上次批过这次免检"。驳回产生的局部回算到达同一道关再次暂停。
- **gate2 驳回的重跑目标**：默认取 IR4 issues 中 `suggestion`（back_to_A2 优先于 back_to_A3）；请求方可显式指定 `rework_stage` 覆盖。gate1 驳回默认回 A2（IR1 一般无需重析；确需时可显式 `rework_stage: "A1"`）。

### 2.2 服务与端口增量

| 服务/模块 | 变更 | 引入任务 |
|---|---|---|
| workflow(8082) | STAGE_DONE 分支扩展（gate 暂停/派 A4）；ReviewController/Service；TraceController；TaskPublisher 增 a4/a5；IR 失效标记 | T2.2 / T2.4 |
| biz(8083) | Flyway V4：`ir_snapshots.invalidated_at`（其余表 V1 已备好） | T2.2 |
| py-engine worker | 新任务 `sonarcad.a4` / `sonarcad.a5`（队列 sonarcad.a4/a5，DLX 同配置）；a3 改发 STAGE_DONE；trace 写入 | T2.1/T2.3/T2.4 |
| py-engine api(8090) | 新端点 `POST /knowledge/writeback`（Neo4j 回写） | T2.3 |
| py-engine solver | SymPy 表达式底座 + 区间传播 + 军标框架占位 | T2.1 |
| py-engine storage | `neo4j_store.py`（driver + MERGE/deprecated）；`trace_store.py` | T2.3/T2.4 |
| Neo4j(17474/17687 覆盖端口) | 首次被业务代码直连（此前仅 compose 就绪） | T2.3 |

### 2.3 横切约定（增量）

- 沿用阶段 1 全部横切约定（统一响应包 / 鉴权链 / IR 契约 / 乐观并发）；本阶段新增：
- **裁决权限**：JWT 只携带角色，故 `gate:decide` 在角色层落实——裁决端点 `hasAnyRole('admin','reviewer')`，与 V2 种子的角色-权限矩阵一致；designer/viewer 403。
- **快照失效语义（A-12 细化）**：`ir_snapshots` 内容保持不可变（无 UPDATE content）；`invalidated_at` 是生命周期元数据，由驳回批量标记（重跑目标 T → T 的产物类型及其下游全部标记）。`GET /irs/{type}` 恒返回最新版本（含失效标记），`ir_store.get_latest_snapshot`（worker 侧读）只取未失效版本。
- **A4 确定性原则**：A4 不调用 LLM——合规校验必须可审计、可复现；LLM 只出现在 A5 候选整理（A-07 白名单）。
- **知识回写诚实原则（A-10/A-16）**：IR5 `writeback_status` 只能 pending→written（经人工确认）；Neo4j 不可达时回写如实失败、IR5 保持 pending 可重试，绝不伪造成功。

## 3. 任务分解（T2.1–T2.4）

> 每个任务给出：目标 / 设计要点 / 接口契约 / 完成定义（DoD）/ 验收方法。状态与 roadmap 同步。
> 执行顺序：T2.1 → T2.3（引擎）→ T2.2（接线）→ T2.4 → 阶段验收。理由：gate2 批准要直接发布 A5 任务，先备好引擎再接线，避免"半接线"中间态（详见 §4.2）。

### T2.1 A4 合规校验引擎（IR3 → IR4）

- **目标**：对 IR2 参数表 + IR3 建模产物做分流校验与自洽检查，输出 IR4 结构化报告；**只出报告不改参数**（A-09）。
- **设计要点**：
  - **纯确定性实现（不调 LLM）**：校验结论必须可审计复现；规则底座全部落在 `solver/`。
  - `formula` → **SymPy 等式精确代入复算**：`FormulaSpec` 增加 `expr`（SymPy 可解析表达式，5 条注册公式全部补齐，D-15）；A4 从参数 `derivation` 解析代入值（"n=16, …" 约定，阶段 1 e2e 已固化该格式）→ SymPy 代入 `evalf` → 与参数值容差比对（相对 1e-6）。无法定位注册公式的 formula 参数出 info（"无法自动复算，请人工复核"，不硬失败——注册表会持续增长）。
  - `case` → **区间传播校验**：按符号别名收集全部 case/estimated 候选值 → 对每条公式做候选组合的区间计算 → IR2 采纳值若落在区间外出 warning（first-wins 策略可能掩盖了与其他案例候选的冲突，须让人看见）；同一符号的多个 case 值差异 >10% 出 warning 冲突项。
  - `standard` → **军标框架占位**：`solver/standards.py`（StandardSpec + 空 REGISTRY + 加载器，阶段 3 首样入库即插即用）；standard 参数在校验报告中标"军标核对待补（军标库暂空）" info（A-16 如实）。
  - `estimated` → 汇总为一条 info："N 个估算参数未经公式验证，请人工复核"，related_params 列全。
  - **几何自洽**：features.linked_param_id ⊆ IR2 参数 id（悬空引用 error→back_to_A3）；feature_id 唯一性；step_file/script 引用完整性。数值级几何量比对待 IR3 features 携带几何量后增强（阶段 3/4，本期如实说明）。
  - **IR4 契约微扩**（同 commit 改 `contracts/ir/ir4.schema.json` + 契约测试）：severity 枚举 `error|warning|info`（↔ 三级风险 高/中/低）；issue 增可选 `message`（人读说明 + 依据引用）；`suggestion` 改为可选（信息级问题无回环目标）。overall = 有 error 即 fail。
- **接口契约**（py-engine 内部 + MQ）：
  | 项 | 契约 |
  |---|---|
  | Agent | `A4Agent(llm=None).run(ir2: dict, ir3: dict) -> dict`（末尾 `validate_ir("IR4", ir4)`） |
  | 任务 | `sonarcad.a4`（队列 `sonarcad.a4`，DLX 同配置；读 IR2+IR3 最新未失效快照 → 落 IR4（created_by=A4）→ update_stage("A4") → STAGE_DONE） |
  | CLI | `python -m agents.a4_validation.cli`（样例 IR2/IR3 脱离管线单独调试，§9.1） |
- **DoD**：
  - [ ] 四类 source_type 分流各有单测，**含故意注入违例参数**（formula 值被改→error；case 候选冲突→warning；standard→待补 info；estimated→info）
  - [ ] IR4 过契约校验（扩展后 schema）；"合法 IR2+IR3 → 合法 IR4"契约测试
  - [ ] A4 独立 CLI 可跑
  - [ ] SymPy 复算与阶段 1 math 闭包结果互证（同输入同输出）
- **验收方法**：`python -m pytest tests/test_t21_a4.py -v`；CLI 实跑一次样例。

### T2.2 审阅关①②（状态机暂停/裁决 + 局部回环）

- **目标**：gate1（IR2 后）/gate2（IR3+IR4 后）暂停—裁决—恢复/回环全流程落地；驳回走局部回算 + 下游失效，禁止整链路重跑（A-11/A-12）。
- **设计要点**：
  - **不加新状态/事件**：复用 `paused` 与 PAUSE/RESUME。STAGE_DONE 分支扩展（`SessionService.fireEvent`，T1.1 预留插入点）：A1→派 A2；**A2→gate1 暂停**；A3→派 A4；**A4→gate2 暂停**。暂停 = 单次乐观更新 `{pipeline_status: paused, current_stage: gate1|gate2}`（WHERE pipeline_status=running）。a3_task 由发 COMPLETE 改发 STAGE_DONE；a5_task（T2.3）为链尾发 COMPLETE。
  - **裁决 API**：见接口契约。approved：RESUME（乐观 paused→running）→ 派下一棒（gate1→A3；gate2→A5）；rejected：RESUME → 确定重跑目标 → 按目标重派（A1/A2/A3 对应任务）→ 下游失效。全部数据库操作单事务，投递在事务内最后一步（失败整体回滚，用户重试，无"消息先于记录"）。
  - **下游失效**：重跑目标 T（A1/A2/A3）→ IR(T 产物)及其下游类型全部 `invalidated_at=now()`（如 gate2 驳回回 A3 → 失效 IR3/IR4/IR5；回 A2 → 失效 IR2–IR5）。
  - **gate2 驳回目标推断**：读最新 IR4 issues 的 suggestion（back_to_A2 优先）；请求 `rework_stage` 可覆盖。
  - **知识审阅关（gate=knowledge）**：会话 completed 且最新 IR5 pending 时可裁决；approved → workflow 同步调用 py-engine `POST /knowledge/writeback`，失败如实记 annotations（决策已落库，IR5 保持 pending 可重试）；rejected → 仅落裁决记录，不回写。
  - 裁决记录同步写 `traceability_chain`（actor=review，action=confirm，合成 trace_id `rv-{8hex}` 记入 annotations）。
- **接口契约**：
  | 方法 | 路径 | 说明 |
  |---|---|---|
  | POST | /api/v1/workflow/sessions/{sid}/reviews/{gate} | gate ∈ gate1\|gate2\|knowledge；body `{decision: approved\|rejected, annotations?: [], rework_stage?: "A1"\|"A2"\|"A3"}`；权限 admin/reviewer；gate1/2 要求 paused 且 current_stage=gate（否则 409）；返回裁决记录 + 后续动作（resume/publish/invalidate） |
  | GET | /api/v1/workflow/sessions/{sid}/reviews | 裁决历史（四角色可读） |
  | GET | /api/v1/workflow/sessions/{sid}/irs/{type} 与 /versions | 增返回 invalidated 标记（改造现有端点） |
- **DoD**：
  - [ ] 通过流实测：START→…→gate1 暂停→approve→…→gate2 暂停→approve→A5→completed
  - [ ] 驳回回流实测：gate2 驳回（back_to_A2）→ 下游失效标记可查 → 局部回算重跑到 gate2 再次暂停 → IR 版本递增、全程快照可追溯
  - [ ] 非审阅角色（designer/viewer）裁决 403；非暂停态裁决 409；并发双裁决一次成功一次 409
  - [ ] `mvn -pl workflow,biz test` 全绿（gate 分发/裁决服务单测，mock 持久层）
- **验收方法**：pytest e2e（§3 T2.7 样例）+ curl ASCII 负载走查。

### T2.3 A5 知识沉淀（IR5 候选 + 知识审阅关回写）

- **目标**：从本次设计的经验中筛选可沉淀知识 → IR5 pending 候选（去重）→ 人工确认后才写 Neo4j（A-10）；图谱中已被新值取代的旧实体标 deprecated。
- **设计要点**：
  - **候选生成**：LLM 整理（A-07 白名单：沉淀候选整理）为主路径——输入 IR2 参数表 + IR4 结论 + IR1 目标，产出 `{type: relation|case|constraint, payload}` JSON；LLM 不可用/解析失败走**确定性兜底**（通过校验的 formula 参数→constraint 候选；case 参数组→case 候选），保证链路不断（A-13）。
  - **去重**：两级——KB 文本比对（现态检索底座，候选名规范化后查语料）+ Neo4j 查重（可达时，按规范化名 MERGE key）；结果写 `dedup_status: new|duplicate`（IR5 契约既有字段）。
  - **回写**：`storage/neo4j_store.py`（neo4j Python driver，连接配置 NEO4J_URI/USER/PASSWORD 走 `.env`，指向 17687 覆盖端口）：constraint→(:DesignConstraint)、case→(:DesignCase)、relation→两端实体 + 关系边；MERGE 幂等；命中同名旧实体且取值不同 → 旧实体 `deprecated: true` 并记录取代关系。**未进确认名单的候选绝不入图（代码路径上不可能：writeback 只接收选定候选）**。
  - **writeback 端点**：py-engine `POST /knowledge/writeback {session_id, candidate_indexes?}`（默认全部 dedup_status=new 的候选）→ Neo4j 写入 → 落 IR5 v2（writeback_status=written，candidates=已写入子集）→ 返回写入统计；Neo4j 不可达 → 如实失败（HTTP 503 + 原因），IR5 不动。
  - requirements 增加 `neo4j>=5`。
- **接口契约**：
  | 项 | 契约 |
  |---|---|
  | Agent | `A5Agent(llm=None, kb=None, graph=None).run(ir1, ir2, ir4) -> dict`（末尾 `validate_ir("IR5", ir5)`，writeback_status 恒 pending） |
  | 任务 | `sonarcad.a5`（队列 `sonarcad.a5`；读 IR1+IR2+IR4 → 落 IR5（created_by=A5）→ update_stage("A5") → **COMPLETE**（链尾）） |
  | HTTP | `POST /knowledge/writeback`（py-engine 8090，内网；body `{session_id, candidate_indexes?: int[]}`） |
  | CLI | `python -m agents.a5_knowledge.cli` |
- **DoD**：
  - [ ] 候选去重单测（KB 命中→duplicate；无命中→new；Neo4j 不可达只降文本级）
  - [ ] **未确认候选绝不入图谱**（单测断言 writeback 只处理选定下标；IR5 pending 态图谱无新增）
  - [ ] 确认回写 + deprecated 标记实测（Neo4j 容器实连）；回写后 IR5 v2 written
  - [ ] LLM 断供走确定性兜底仍产出合法 IR5（契约测试）
- **验收方法**：`python -m pytest tests/test_t23_a5.py -v`；Cypher 手查节点/deprecated 标记。

### T2.4 溯源链（trace_id 贯穿 + 反查）

- **目标**：参数级 trace 全程可记录、可反查：任一参数从 IR2 一路追到 IR0 需求原文与 IR1 检索引用。
- **设计要点**：
  - **生成规则**（沿阶段 1）：`tr-{uuid4.hex[:12]}`，A2 每参数独立生成；无 trace_id 的参数进不了 IR2（schema 必填 + a2_task 四件套运行时断言，T1.4 已落地，本任务补链路写入）。
  - **链路写入**（worker 落快照时同步写 `traceability_chain`，`storage/trace_store.py` 批量插入）：
    | actor | 时机 | action | payload |
    |---|---|---|---|
    | A2 | 落 IR2 后逐参数 | derive | param_id/name/source_type/value/unit |
    | A3 | 落 IR3 后逐特征 | transform | feature_id/linked_param_id（trace 继承参数） |
    | A4 | 落 IR4 后逐 issue | validate | constraint_id/severity/related_params |
    | A5 | 落 IR5 后逐候选 | assign | candidate_index/type |
    | review | Java 裁决时 | confirm | gate/decision/reviewer |
  - **反查 API**：workflow `TraceController`——按 trace_id 取链路步骤（step_no 升序，关联快照 ir_type/version）+ 组装上下文（IR0 raw_text、IR1 objectives 与 retrieved_context 引用、IR2 参数全貌）。
- **接口契约**：
  | 方法 | 路径 | 说明 |
  |---|---|---|
  | GET | /api/v1/trace/{trace_id} | 四角色；返回 `{trace_id, steps[], context:{ir0, ir1, ir2_param}}`；404 如实 |
- **DoD**：
  - [ ] worker 四类写入各有单测；批量插入原子
  - [ ] 反查 API 实测：IR2 参数 trace_id → 返回链路 + IR0 原文 + IR1 检索引用（e2e 断言）
  - [ ] 审阅裁决 confirm 行可经合成 trace_id 反查
- **验收方法**：单测 + e2e（gate 流程中取任一参数 trace_id 反查）。

## 4. 阶段管理

### 4.1 里程碑

| 里程碑 | 包含任务 | 判据 |
|---|---|---|
| M2-1 A4 引擎 | T2.1 | 四类分流单测全绿（含违例注入）+ IR4 契约扩展落地 + CLI 可跑 |
| M2-2 A5 沉淀引擎 | T2.3 | 去重/未确认不入图/确认回写单测与实测通过 |
| M2-3 审阅关与回环接线 | T2.2 | 通过流 + 驳回回流实测；失效标记与 RBAC 生效 |
| M2-4 溯源链 | T2.4 | 参数级反查到 IR0 原文与检索引用 |
| M2-5 阶段收官 | 验收 | 五步闭环 e2e（含驳回流）全过，§5 回填，roadmap/HANDOFF 同步 |

### 4.2 依赖与顺序

T2.1 → T2.3 → T2.2 → T2.4 → 验收。

> **对 v0.1 顺序的调整说明**：v0.1 按 T2.1→T2.2→T2.3 排列；完整版调整为 A5 引擎（T2.3）先于接线（T2.2）——gate2 批准事件直接发布 `sonarcad.a5`，若 A5 任务未就绪则审批动作会投递出无消费者的消息。先引擎后接线保证任何 commit 点上管线都是完整可跑的。T2.4 依赖 T2.2 的裁决写入，收尾。

### 4.3 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 局部回环下游失效范围判定复杂 | 失效漏标/过标 | 规则收敛为"重跑目标 T → T 产物及下游整批标记"（§2.3），不做细粒度依赖分析；先固化快照失效语义再写回环 |
| 军标库为空，standard 校验无对象 | 校验覆盖不完整 | 如实"待补"（A-16）+ 框架占位，阶段 3 首样入库即插即用 |
| gate2 驳回后 A2 重跑经 gate1 再次暂停，流程变长 | 人工等待 | 门语义即"新产物必须重新过闸"（安全优先）；文档明示；UI 期可加"自动续批"策略再议 |
| Neo4j 覆盖端口/凭据连接失败 | 回写失败 | 连接参数集中 `.env`（HANDOFF 已标注 17687）；不可达如实失败且 IR5 保持 pending 可重试 |
| 无 UI 期的裁决体验 | 走查繁琐 | curl ASCII 脚本 + e2e 固化；WS 推送随阶段 4 激活（v0.1 规划收敛，见 §1 边界） |

### 4.4 验收流程（阶段末）

1. 逐任务核对 DoD 勾选项（本文件 + roadmap 双处一致）。
2. 跑阶段 2 e2e：完整通过流 + gate2 驳回回流（失效→局部回算→重跑到 gate2）+ 知识确认回写，一次脚本内固化。
3. 单测全量：`python -m pytest tests/ -v`（不含 e2e）+ `mvn -pl gateway,auth,workflow,biz test`。
4. 人工抽查：IR4 报告风险定级合理性（error/warning/info 与实际违例匹配）；Neo4j 中 deprecated 标记正确。
5. 验收结论写入 §5，同步 roadmap 与 HANDOFF。

## 5. 阶段验收记录

| 日期 | 事项 | 结论 |
|---|---|---|
| — | 待阶段开工后回填 | — |

## 6. 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-09-17 | 规划版：范围边界、四任务框架、风险识别 |
| v1.0 | 2026-09-17 | 升级为完整版：任务级接口契约与 DoD、门语义与 STAGE_DONE 接线设计、下游失效规则（invalidated_at）、A4 确定性原则与 IR4 契约微扩、A5 回写端点与 deprecated 语义、溯源链写入矩阵、执行顺序调整（T2.3 提前于 T2.2）及理由、WS 通知收敛到阶段 4 |
