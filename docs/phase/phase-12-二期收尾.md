# 阶段 12 开发文档 · 二期收尾（v0.5）

| 项 | 内容 |
|---|---|
| 文档编号 | PHASE-012 |
| 版本 | v1.0（2026-10-01，编制；二期闭环强化承诺的最后一轮清账） |
| 关联 | 总体设计方案 §5.1/§5.4/§5.6/§8.2/§10.5/§11.1（二期定义）、[GAP-001](../设计实现对照与优化路线.md) §3 P2 剩余项、[PHASE-011](phase-11-业务泛化与生产上线.md)（前置）、[ADR-004](../adr/ADR-004-CAD交互微调集成路线.md)（E1）、系统实施方案 :279（Origin Lock/Max Depth）、[PHASE-010](phase-10-生产就绪与M4验收.md) :145（机制认领来源）、roadmap 阶段 12 |
| 阶段目标 | 认领 E1「参数双向联动」另一半（正向参数更新入口 + 变更抑制三件套）并清账 P2 剩余项（P2-6/8/9/10/12）、日志检索、§10.5 第 5 条告警、用户指南（M5 培训交付），收官打 tag `v0.5.0` |
| 验收形态 | e2e（含 E1 正向新链路）+ 单测 + bundle 体积对比 + GraphRAG 实测记录 + 告警触发证据 + 用户指南 + 文档回填 |

---

## 1. 阶段定位与边界

### 1.1 为什么有这个阶段

总体设计 §11.1 把二期（闭环强化）定义为「**参数双向联动（变更抑制）**、约束推理校验精度优化、多层缓存与查询调优、压力测试通过」——压力测试（v0.3）、缓存（v0.2/v0.3）已交付；**双向联动只交付了反向半边**（v0.4 E1 反向回写），正向（文档→CAD：改 IR2 参数 → 仅重算 A3 受影响段）与变更抑制机制（Origin Lock/Max Depth，系统实施方案 :279）至今零实现，且 phase-10 :145 将其划给 v0.4、phase-11 实际未含——**本阶段正式认领**。同时清账 GAP-001 P2 剩余待办（P2-6/8/9/10/12）、AdminPage 日志检索「待接」、§10.5 五条告警缺一（缓存命中率）、M5「培训」交付物（用户指南）。

### 1.2 本期范围（做）

| 任务 | 对应 |
|---|---|
| T12.1 LLM 重试指数退避（429/5xx 退避 + 4xx 快速失败） | P2-6 |
| T12.2 E1 正向联动（参数更新入口 + 变更抑制三件套 + A3 重算复审闭环） | 总体设计 §5.6/§8.2 + 二期「变更抑制」 |
| T12.3 IR1 需求解析人工核对界面（修订→追加版本→重跑 A2） | P2-10（§5.1） |
| T12.4 方案正文风险质检（CHK-DOC，warning 级） | P2-12（§5.4） |
| T12.5 跨会话日志检索（AdminPage 日志中心） | AdminPage 三待接之日志 |
| T12.6 前端 bundle 分割（路由 lazy + manualChunks） | P2-8 |
| T12.7 GraphRAG SDK 安装与验证（SDK 级自动优先实测） | P2-9（A-14） |
| T12.8 缓存命中率告警 | §10.5 第 5 条 |
| T12.9 用户指南（按角色操作手册） | M5「培训」交付物 |
| T12.10 收官 + tag v0.5.0 | — |

### 1.3 非目标（明确不做）

- PDF 导出（用户确认继续后置；正式军标模板到位后一并考虑）；
- 真实 vLLM 部署、真实 GJB 全量录入、双活实施（外部依赖，按 v0.4 既有手册/入口/ADR 执行）；
- ELK 日志中心、独立审计库、涉密语义脱敏+占位回填（§9.3/§9.4 生产化纵深项，留生产落地）；
- 成员级项目数据权限、交叉编码器重排序、元数据管理界面、DWG/DXF、PDM/PLM（三期/外围，GAP-001 保留待办）。

## 2. 设计要点

### 2.1 E1 正向联动与变更抑制（T12.2）

- **入口**：`POST /api/v1/cad-agent/sessions/{sid}/parameters/{pid}/update`（body: value, reason）——仅 `paused@gate2` 可发起（completed 409，与 v0.4 反向回写同口径；gate1 前改需求走 T12.3 的 IR1 修订）。
- **链路**：IR2 追加新版本（value + derivation **原地同步**——复用 `param_proposals.sync_derivation`，A4 符号表依赖）→ 定向失效 **IR3/IR4/IR5** → 重派 `a3_task` → A3 重跑（`geometry_link` 正向联动新 IR3）→ STAGE_DONE(a3) 续派 A4 → 自动重挂 gate2 复审（既有 pauseAtGate 乐观条件，零 Java 改动）。
- **变更抑制三件套**（biz V16 `cad_param_updates` 表）：
  1. **传播深度**：forward 更新记 depth=1；其引发的 A3 重算所产反向提案记 depth=2；确认回写产生的下游事件 depth 递增，**>3 拒绝**（系统实施方案 Max Depth=3）。
  2. **回写拦截器**：反向事件一律「挂起待人工确认」——即 v0.4 提案确认制（确认制天然满足实施方案 :279 的「反向事件被回写拦截器挂起、标注待人工确认」）。
  3. **单飞行变更**：仅 paused 态可发起，A3 重算进行中（running）天然不可再发起——串行化窗口即「发起方原子锁」的务实实现；乒乓不成环的根因是反向 confirm 只重派 A4 不重派 A3（几何即真相，T11.3 设计决策）。
- **前端**：ReviewTab 参数表加「修改」操作（designer/admin）——弹窗改值+理由，提示将重跑 A3 并回 gate2 复审。

### 2.2 IR1 人工核对（T12.3）

- 设计 §5.1「解析后进入人工核对界面，分模块校验修订」：实现为 ReviewTab IR1 卡编辑模式（objectives 数值行内编辑 + missing_items 文本修订），设计人员/管理员可用。
- 端点 `POST /cad/sessions/{sid}/irs/IR1/revisions`：追加 IR1 新版本（created_by="user"，A-12 不可变快照语义）→ 定向失效 IR2..IR5 → 重派 `a2_task` → gate1 复审（复用局部回环；gate1 驳回 A1 通道已存在，本任务补「不重解析、人工修订后直接重算下游」路径）。
- 编辑窗口：`running`（A1 完成后、gate1 暂停前）与 `paused@gate1`——其他状态 409（A-16 口径写死注释）。

### 2.3 正文风险质检（T12.4）

- A4 新增 `_check_document`（warning 级，不阻断 overall/CSR——CSR 分母本就不含 warning，T9.2 口径）：CHK-DOC-DEGRADED（降级横幅残留）、CHK-DOC-PARAM-REF（正文提及参数 ID 与参数表一致性）、CHK-DOC-SOURCE-MISSING（formula/standard 类参数 source_ref 缺失）。
- `constraint_id` 为自由字符串，无需改 ir4 契约；warning 不带 suggestion，枚举不动。

### 2.4 GraphRAG SDK 验证（T12.7）

- 前置事实（2026-10-01 核实）：graphrag~=2.0 包**未安装**（两个 Python 环境均无）；已有的是自建 Neo4j 实体层（graph 级检索）+ `knowledge/sdk.py` 封装壳 + `kb/settings.yaml`（读 SILICONFLOW key）。SDK 级（local/basic）一直如实降级（`degraded_from: graphrag_sdk_unavailable`）。
- 验证路径：安装 → 确认 `kb/output/` 索引产物（缺则先构建）→ 未 scoped 查询走 SDK（local/basic 自动优先，A-14）→ scoped 场景仍如实降级（D6-5：SDK 单一全局索引无法按库过滤）→ 性能对比如实记录。失败则关账维持降级（不造假，A-16）。

## 3. 任务分解（T12.1–T12.10）

> 执行顺序：T12.1 → T12.2 → T12.3 → T12.4 → T12.5 → T12.6 → T12.7 → T12.8 → T12.9 → T12.10（每任务独立 commit）。

### T12.1 LLM 重试指数退避 ✅ 已完成（2026-10-01）

- **DoD**：
  - [x] `_chat_single` except：指数退避+jitter（`LLM_BACKOFF_BASE_S=2` / `LLM_BACKOFF_MAX_S=30` env 可调）；**分级口径**：仅显式 4xx（401/422 等，429 除外）不退避快速失败进转移链，429/5xx/网络/未知错误一律退避重试（保持既有「transient 可重试」语义，P2-6 只新增节律——初版把未知错误也排除重试，被 T1.2 既有用例 test_retry_upper_bound 当场纠正）
  - [x] 单测 4 例新增（test_t121_backoff.py：429 退避后成功/404 快速失败/网络错误退避/分类断言）；adapter 全族 23 用例回归绿

### T12.2 E1 正向联动 ⬜

- **DoD**：
  - [ ] `POST /cad-agent/sessions/{sid}/parameters/{pid}/update` + biz V16 `cad_param_updates`（origin/depth/状态）
  - [ ] 变更抑制三件套：depth 记录与 >3 拒绝；反向事件确认制挂起；单飞行窗口（paused@gate2 only）
  - [ ] 前端参数表「修改」弹窗（designer/admin）
  - [ ] e2e `test_e1_forward_e2e.py` 全闭环（改参→A3 重算→新 IR3 正向联动→gate2 复审→批准）+ 抑制语义断言；单测 depth/origin

### T12.3 IR1 人工核对 ⬜

- **DoD**：
  - [ ] `POST /cad/sessions/{sid}/irs/IR1/revisions`（追加版本+失效下游+重派 A2；编辑窗口 running-after-A1/paused@gate1）
  - [ ] 前端 IR1 卡编辑模式（objectives 行内改值 + missing_items 修订）
  - [ ] 单测（修订→版本追加→重派语义）；既有 phase2 e2e 回归

### T12.4 正文风险质检 ⬜

- **DoD**：
  - [ ] `_check_document` 三检查（CHK-DOC-DEGRADED / CHK-DOC-PARAM-REF / CHK-DOC-SOURCE-MISSING，warning 级）
  - [ ] 单测三场景；overall/CSR 口径不回归

### T12.5 日志检索 ⬜

- **DoD**：
  - [ ] log_store 跨会话查询（session/level/kind/关键字/时间窗+分页）+ `GET /logs/search`
  - [ ] AdminPage「日志中心」卡（过滤+表格+详情）替换「待接」行；vitest

### T12.6 bundle 分割 ⬜

- **DoD**：
  - [ ] App.tsx 路由级 React.lazy+Suspense；vite manualChunks（three 系/d3/antd/react vendor）
  - [ ] build 产物体积前后对比入任务书 §5（主包显著缩小，数字如实）；tsc+vitest 回归

### T12.7 GraphRAG SDK 验证 ⬜

- **DoD**：
  - [ ] 安装 graphrag~=2.0（连带 litellm）；kb/output 索引就绪（缺则构建）
  - [ ] 未 scoped 查询 SDK 级生效实测（local/basic 优先 + graphrag_sdk=true + 性能对比）；scoped 仍如实降级（D6-5）
  - [ ] requirements 可选依赖声明升级 + 验证记录落 §5；失败则 A-16 关账

### T12.8 缓存命中率告警 ⬜

- **DoD**：
  - [ ] alerts.yml `CacheHitRateLow`（<60% 持续 5m）；临时调低阈值触发证据入 docs/reviews/evidence/
  - [ ] Grafana 面板核对；恢复正式阈值确认回落

### T12.9 用户指南 ⬜

- **DoD**：
  - [ ] `docs/ops/用户指南.md`：按角色（设计人员/审阅者/管理员）走查式手册，覆盖登录/建会话/审阅/E1 双向联动/批量军标/导出/用户管理/通知；引用 PHASE-011 自检⑤补走查清单

### T12.10 收官 ⬜

- **DoD**：
  - [ ] 全量回归全绿（py 单测 + e2e 全套含新增 + Java + vitest/tsc）
  - [ ] GAP-001 P2 表全清账（含 P2-3 过期状态行修正——knowledge-dumps/GraphML 已于 T11.7 建成）；HANDOFF 过期待决勾销 + 入口 9；附录 E.4 v0.5 行；roadmap 阶段 12 全 ✅；devlog
  - [ ] 宪法附录 A 自检 → tag `v0.5.0` + push

## 4. 阶段管理

### 4.1 风险与对策

| 风险 | 对策 |
|---|---|
| E1 正向改动波及审阅关（T10.5 幂等语义） | 严守既有语义复用（定向失效/乐观条件/重派）；e2e 全量回归含 phase2/cad_tune/e1_reverse 三条链路 |
| A3 重跑时长（真 LLM 155s 级）拖慢正向体验 | 如实提示「A3 重算中」进度（WS task_progress 天然覆盖）；不做假进度 |
| graphrag 安装失败/依赖冲突（litellm 重） | 独立 venv 试装先行；失败则 A-16 关账维持降级，不阻塞收官 |
| bundle 分割破坏既有行为 | lazy 仅切加载时机不改路由结构；manualChunks 仅影响分包不影响语义；vitest+人工冒烟 |

### 4.2 验收流程（阶段末）

全量回归绿 → 各任务 DoD 证据齐 → 文档回填 → 附录 A 自检 → tag v0.5.0。

## 5. 验收记录

| 任务 | 实测证据 | 时间 |
|---|---|---|
| T12.1 LLM 退避 | 单测 4 新 + adapter 族 23 绿（t116/t121/t12_worker）；分级口径被既有用例纠正一次（未知错误保持可重试） | 2026-10-01 |

## 6. 决策记录

- **范围主轴 = 二期收尾完整版**（含 E1 正向联动认领）——用户 2026-10-01 确认（选项：完整版/轻量打磨版）。
- **GraphRAG = 安装并验证**——用户确认（澄清：graphrag 包此前未装，自建 Neo4j 层一直承担图谱检索；SDK 级为锦上添花，失败如实关账）。
- **PDF 继续后置**——用户确认。
- **E1 正向窗口 = 仅 paused@gate2**（gate1 前改需求走 IR1 修订；completed 走驳回返工）——与反向回写同口径，避免状态机新增。
- **变更抑制 = 务实三件套**（depth 字段 + 提案确认制 + 单飞行窗口），非引入独立联动引擎——二期口径内满足「变更发起方原子锁+最大传播深度」语义，实现复杂度与团队现状匹配（A-14 务实原则）。

## 7. 演进路线（承诺项，非本期范围）

- 生产落地（v0.6+/生产环境）：真实 vLLM（按 v0.4 手册）、真实 GJB 全量录入（按批量入口）、双活实施（按 ADR-005）、ELK/审计库、涉密语义脱敏+占位回填（§9.3 完整形态）、成员级数据权限、PDF 导出（配正式军标模板）、交叉编码器重排序、元数据管理界面。

## 8. 修订记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v1.0 | 2026-10-01 | 初版编制（ZCode）。前置发现（探索实证）：①E1 正向半边零实现——`POST /parameters/{pid}/update`（总体设计 §8.2 :700）全库无路由，Origin Lock/Max Depth=3（系统实施方案 :279）代码零命中，phase-10 :145 划给 v0.4 而 phase-11 未含——三处口径打架，本阶段正式认领；②乒乓天然受控——反向 confirm 只重派 A4 不重派 A3（几何即真相），forward→reverse 单向不成环，确认制即「回写拦截器挂起」；③P2-10 复用点齐备（gate1 驳回 A1 通道已在），缺 IR1 修订端点+前端编辑；④P2-12 落点：design_document 在 A4 零校验，constraint_id 自由字符串无需改契约；⑤P2-6 插入点 `_chat_single` except（adapter.py :256），失败后现无 sleep、4xx/5xx 同权；⑥P2-8：App.tsx 七页面全静态、three/d3 全在主包、vite 无 build 段；⑦日志检索：log_store 仅单会话游标查询；⑧§10.5 五条告警只落四条（缺缓存命中率）；⑨P2-3 状态行过期（knowledge-dumps/GraphML 已于 T11.7 建成）；⑩HANDOFF 两条过期待决未勾（挂死根因已由 T9.1 闭环、llm_providers 已核实）；⑪graphrag~=2.0 包确认未安装（两 Python 环境核实），SDK 级一直如实降级 |
