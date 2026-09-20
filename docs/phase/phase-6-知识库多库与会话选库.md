# 阶段 6 开发文档 · 知识库多库管理与会话选库

| 项 | 内容 |
|---|---|
| 文档编号 | PHASE-006 |
| 版本 | v0.2（2026-09-19，决策版——开放问题已全部经用户拍板，待开工） |
| 关联 | CONST-001 §6（知识管线）、§7.3（UI 先 mockup 后写码）、A-14（三级降级）、A-16（诚实红线）、A-02（全链路溯源）；阶段 3（KB 管线地基）、阶段 4（工作台 UI） |
| 阶段目标 | 知识库从"全局单库"升级为**多库**：库可建/改/查/删（删库保留文档）；文档自由归库/换库；新建会话可选库，不选则 **Agent 自主决策**并留痕；检索全链路按库过滤且如实可溯源；会话总览可见"用了哪些库、为什么、命中了什么"；审阅时可看参数引用的知识原文 |
| 验收形态 | 双库实测：文档归库调整 → 会话选库 A 定向检索 / 不选库走 Agent 决策 → IR1 引用与决策依据留痕 → 总览卡片如实展示 → 审阅参数可溯源知识原文；e2e 固化 |

---

## 1. 阶段定位与边界

### 1.1 现状（为什么有这个阶段）

当前知识库是**单库形态**（阶段 3 落地）：

- `kb_documents / kb_chunks / kb_extractions` 全局一张表，无"库"维度；
- Milvus 单 collection `kb_chunks`（主键 chunk_key，无库字段）；Neo4j 节点只带 `source_doc`；
- 检索服务 `GraphRAGService` 进程内单例，`all_indexed_chunks()` 取**全部** indexed 文档；
- 知识库页（文档库/抽取确认/三库/供应商/图谱）平铺展示所有文档；
- 新建会话无任何知识库概念——检索永远打全库。

### 1.2 一个关键澄清：检索发生在 A1，不是 A2

用户视角"方案生成（A2）用哪些知识库"，但管线现状是：**A1 需求解析阶段**做子查询规划与并行检索（`A1Agent._retrieve` → IR1.retrieved_context），**A2 从 IR1.retrieved_context 取案例参数**生成方案（A2 自身不检索）。

因此选库点放在**新建会话时**（与需求输入同一处、一次选定全程生效）：选定库 → A1 检索只打选定库；不选库 → A1 的库路由步骤自主圈定相关库（§2.3 D6-3）。两种模式检索结果都固化进 IR1，A2 方案的知识原料即来源于此。**中途改库（驳回回退 A2 时换库）本期不做**——回退 A2 重算复用既有 IR1，检索不会重跑；要换库 = 新建会话（如实告知用户）。

### 1.3 边界（做什么 / 不做什么）

- **做**：多库数据模型与库 CRUD；**删库保留文档（转未归库）**；文档归库/换库（含新建库向导从任意库勾选 + 库内继续上传）；未归库区（可查看/归库，不参与检索）；Milvus/Neo4j/文本语料三层检索按库过滤；会话创建选库（多选）+ **不选库时 Agent 自主决策**（决策依据写入 IR1）；会话总览"知识检索"卡片（选择依据 + 命中明细可展开）；知识库管理页重构（mockup 先行）；A5 回写**手动选库**；审阅参数**命中原文预览**。
- **不做（后续按使用反馈再排）**：库级权限/成员管理、库启停、库导出/导入、检索命中的 chunk 级评分调节；GraphRAG SDK 级按库过滤（SDK 当前未装，见 §2.3 D6-5）；会话中途改库。
- **明确保持**：三库（公式/约束/军标）与 LLM 供应商是**全局资产**，不按库隔离（A4 校验用全局军标/公式库，与设计会话选库无关）。

## 2. 架构与设计决策

### 2.1 数据流总览（改造后）

```
库管理：kb_bases（CRUD；删库 = 文档转"未归库"，文档与全部管线产物保留）
  文档管线（不变，多带 kb_id）：上传(必属某库，在库视图内上传) → 解析 → 分块 → 抽取确认 → 三层构建
  归库/换库 assign_documents(doc_ids → kb)：PG + Milvus(存量向量重打标) + Neo4j(source_doc 批量 SET) 三层同步，零重建
  未归库（kb_id NULL）：知识库页可见可预览可归库，不参与任何检索（归库即恢复）

会话链路：
  NewSessionDrawer 选库(多选；不选 = Agent 自主决策，控件明示)
    → design_sessions.kb_ids (jsonb, null = Agent 模式) → MQ sonarcad.a1(session_id)（契约不变）
    → py-engine a1_task 直读 design_sessions.kb_ids（同 ir_store 直读模式）
    → [Agent 模式] GraphRAGService.route_bases(sub_queries)：逐库轻量相关度探测 → 选定库集合（零命中兜底全部库）
    → query_batch(queries, kb_ids) 三层过滤 → IR1 增 kb_selection（模式/逐库依据）+ retrieved_context 每条带 kb_id
  会话总览"知识检索"卡片：选定库（人工/Agent + 逐库命中依据）+ 实际命中聚合（库 → 文档 → 位置，可展开）
  审阅参数命中原文预览：source_ref(doc#loc) → 引用解析 API → 知识原文段落
  A5 回写：批准弹窗手动选目标库（默认会话首选库）→ 候选节点带 kb_id
```

### 2.2 数据模型（Flyway V9，biz 服务迁移，py-engine 读写）

```sql
CREATE TABLE kb_bases (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        varchar(128) NOT NULL UNIQUE,
    description varchar(512),
    is_default  boolean NOT NULL DEFAULT false,   -- 种子"默认知识库"，不可删（系统保底：上传兜底/回写默认值）
    created_by  varchar(64),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO kb_bases (name, is_default) VALUES ('默认知识库', true);

ALTER TABLE kb_documents ADD COLUMN kb_id uuid REFERENCES kb_bases(id);  -- 可空：NULL = 未归库（不参与检索）
UPDATE kb_documents SET kb_id = (SELECT id FROM kb_bases WHERE is_default);  -- 存量 4 文档归默认库（保持现检索行为不回退）
CREATE INDEX idx_kb_documents_kb ON kb_documents(kb_id);

ALTER TABLE design_sessions ADD COLUMN kb_ids jsonb;  -- 会话选定库；null = Agent 自主决策模式
```

- **kb_documents.kb_id 可空**：NULL = 未归库（删库自动产生 / 历史游离）。存量文档一次性归入默认库——否则升级后检索立刻变空（回归事故）。
- **kb_id 外键 RESTRICT**：删库 = 应用层先把成员文档 `SET kb_id = NULL`（连带 Milvus/Neo4j 归属清空），再删库行；DB 层兜底防脏删。
- **design_sessions.kb_ids 只存意图**（null = Agent 模式）；"实际用到哪些库"以 IR1.kb_selection + retrieved_context 聚合为准（A-02/A-16——选了库但零命中、Agent 圈定依据，都要如实展示）。

### 2.3 关键设计决策

| # | 决策 | 理由 |
|---|---|---|
| D6-1 | Milvus 单 collection + `kb_id` 标量字段（空串 = 未归库），检索 `filter: kb_id in [...]`；"全部库" = `kb_id != ""` | 现网 collection 无此字段需一次重建（4 文档 439 chunks，重刷分钟级）；未归库以空串表示，天然被任何过滤排除 |
| D6-2 | Neo4j 节点/关系加 `kb_id` 属性（空 = 未归库），检索 `WHERE n.kb_id IN`；存量一次性 backfill 默认库（幂等 CLI `python -m kb.migrate_multikb`，顺带处理 Milvus 重建） | 图谱层与向量层过滤语义对齐；`deprecate_by_source_doc` 清理逻辑不变 |
| D6-3 | **不选库 = Agent 自主决策**：A1 新增库路由 `route_bases(sub_queries)`——逐库用首条子查询做轻量探测（向量/关键词命中计数），命中 > 0 的库入选；全部零命中 → 如实兜底"全部库"并标注。决策写入 IR1 可选字段 `kb_selection: {mode: auto\|manual, bases: [{kb_id, kb_name, hits, selected}]}` | 用户拍板"让 Agent 决策"；库数量小（个位数）探测成本可控；依据入 IR1 = 决策可解释可追溯（A-02/A-16），总览卡片直接展示 |
| D6-4 | **删库 = 文档转未归库**：文档本体、解析/分块/抽取、向量与图谱数据全部保留，仅三层归属标签清空（退出检索）；无 force、无级联销毁，重新归库即恢复检索 | 用户拍板"设计师上传的文档继续保留"；删除变得安全可逆，彻底消除误删风险 |
| D6-5 | 选定库（kb_ids 非空）时 **SDK local/basic 级跳过**，degraded_from 如实记 `sdk_kb_scope_unsupported`（A-16）；Agent 模式圈定库后同理 | GraphRAG SDK 是单一全局索引，无法按库过滤；当前环境 SDK 未装此路径本就不生效。SDK 若未来启用需按库拆索引目录，另立决策 |
| D6-6 | 会话选库经 workflow 校验存在性（跨服务直查 kb_bases，复用 ProjectLookupMapper 先例——同库 PG），无效 id 400 | 创建时即报错优于运行时静默零命中 |
| D6-7 | IR1 契约扩展（向后兼容，均可选）：retrieved_context 条目 + `kb_id`；新增 `kb_selection` | 旧快照无新字段时前端容错显示 |
| D6-8 | **A5 回写手动选库**：批准回写弹窗加目标库下拉（默认 = 会话人工选定首选库 / Agent 圈定首选库 / 兜底默认库），`/knowledge/writeback` 请求带 kb_id，候选 Neo4j 节点写入归属 | 用户拍板；沉淀知识归属明确，图谱按库过滤可见 |
| D6-9 | **未归库语义**：不参与任何检索（含"全部库"）；知识库页"未归库"区可查看/预览/归库（A-16 如实——归库前不被检索到） | 用户拍板"归库后才检索"；删库意图（内容不再影响设计）得以贯彻 |
| D6-10 | **文档归库/换库** `assign_documents(doc_ids, kb_id)`：PG 改 kb_id + Milvus 按 doc_id 取存量向量重打标（取不到则从 kb_chunks 文本重嵌入）+ Neo4j `source_doc IN` 批量 SET；**零重建、零重嵌入（常态）**。入口：新建库向导（可从**任意库**及未归库勾选，挪出时轻提示来源库将减少 N 份）、库详情「归入文档」、文档行「移动到…」；库视图内可直接继续上传新文档 | 用户拍板"可以从任意库选，也可以再新上传"；文档 = 资源、库 = 分组；归属标签变更不动向量/图谱数据本体，成本低 |

### 2.4 服务与端口增量

| 服务/模块 | 变更 |
|---|---|
| py-engine api(8090) | `/kb/bases` CRUD（删 = 转未归库）+ 新建库带 initial_doc_ids；`/kb/documents/assign`；`/kb/documents?kb_id=`（含 unassigned）；`/kb/refs` 引用解析（命中原文预览）；`/kb/graph` 可选 kb_id 过滤 |
| py-engine knowledge | `GraphRAGService.query/query_batch(…, kb_ids)` 三层过滤 + `route_bases(queries)` 库路由；refs 带 kb_id |
| py-engine worker | `a1_task` 读会话 kb_ids（null → 路由）传入检索并写 IR1.kb_selection；`kb_build` 写向量/图谱带 kb_id |
| py-engine kb | Milvus collection schema v2（kb_id 字段 + 旧版检测重建）；assign 三层同步；`kb.migrate_multikb` 存量迁移 CLI |
| workflow | `design_sessions.kb_ids` 透传（create/get/list 回填 kb_names，同 projectName 模式）；KbBaseLookup 校验；writeback 透传 kb_id |
| biz | Flyway V9（kb_bases + kb_documents.kb_id 可空 + design_sessions.kb_ids） |
| frontend | 知识库页重构（左库列表 + 未归库区 + 库内管线 + 归库交互）；NewSessionDrawer 选库（明示 Agent 模式）；总览"知识检索"卡片；审阅命中原文预览；回写选库 |

## 3. 任务分解（T6.1–T6.7）

> 执行顺序：T6.1 → T6.2 → T6.3 → T6.4（mockup 可提前出）→ T6.5 → T6.6 → T6.7 收官。

### T6.1 多库数据模型与库管理 API

- **目标**：kb_bases 建模 + 库 CRUD（删库转未归库）+ 文档归库/换库 + 存量迁移。
- **设计要点**：
  - V9 迁移（§2.2）；`kb_store.py` 增 bases CRUD（create 重名 409，可带 initial_doc_ids / list 带 per-库统计：documents、indexed、chunks、extractions_pending / patch 改名描述 / delete：成员转未归库后删库行）；`list_documents(kb_id=|unassigned)`；`assign_documents(doc_ids, kb_id)`；`doc_stats(per_base=True)`。
  - API（经 gateway `/api/v1/kb/**`，鉴权沿用 require_user/require_writer）：

    | 方法 | 路径 | 说明 |
    |---|---|---|
    | GET | /kb/bases | 库列表 + 每库统计（默认库置顶） |
    | POST | /kb/bases | {name, description, initial_doc_ids?}（writer；重名 409） |
    | PATCH | /kb/bases/{id} | 改名/描述（writer） |
    | DELETE | /kb/bases/{id} | 默认库 400；其余 = 文档转未归库 + 删库（响应含转出文档数） |
    | POST | /kb/documents/assign | {doc_ids, kb_id?}（kb_id 空 = 转未归库）三层同步 |
    | POST | /kb/documents | multipart 增加 `kb_id`（必选——上传发生在库视图内） |
    | GET | /kb/documents | `?kb_id=`（uuid 或 `unassigned`） |
  - 抽取确认/构建端点不变（doc 维度）；`/kb/build` 增量语义不变。
  - 存量迁移 CLI `python -m kb.migrate_multikb`（幂等）：Neo4j 存量节点 backfill kb_id、检查 Milvus collection schema 版本并按需重建。
- **DoD**：
  - [ ] 库 CRUD 单测（重名 409 / 默认库保护 / 删库转未归库断言 / initial_doc_ids 归库）
  - [ ] assign 三层同步单测（fakes：PG/Milvus/Neo4j 归属一致变更；向量不重嵌入路径）
  - [ ] 未归库列表/统计单测；迁移 CLI 实机幂等跑两遍（第二遍 no-op）
  - [ ] 网关走查：建库（勾 2 份既有文档）→ 删库 → 文档进未归库 → 重新归库

### T6.2 检索三层按库过滤 + 未归库排除

- **目标**：向量/图谱/文本三层检索接受 `kb_ids`，空 = 全部已归库文档；未归库永远不命中；refs 带 kb_id。
- **设计要点**：
  - `ChunkVectorStore`：schema 加 `kb_id VARCHAR(64)`（空串 = 未归库）；`ensure_collection` 检测旧 schema（无 kb_id 字段）→ 记日志并 drop 重建（升级后触发一次全量 `POST /kb/build` 重刷——**升级步骤写入 HANDOFF**）；`upsert_chunks` 带 kb_id；`search(query_vector, top_k, kb_ids)`：非空 → `kb_id in [...]`，空（全部库）→ `kb_id != ""`。
  - `Neo4jStore.find_entities_matching` 加 kb 过滤（空 = `n.kb_id IS NOT NULL AND n.kb_id <> ''`）；`write_extractions_to_graph` 写入 kb_id。
  - `GraphRAGService`：动态语料 `(kb_id, doc_id, location, text)`，未归库不入语料；`query(query, kb_ids=None)` / `query_batch(queries, kb_ids=None)` 三层下推；SDK 级按 D6-5 跳过并如实记 degraded_from；`RetrievalResult.refs` 增 kb_id。
  - 契约：`contracts/ir/ir1.schema.json` retrieved_context 条目加可选 `kb_id` + 新可选根字段 `kb_selection`；ir_contract.py 校验器放行。
- **DoD**：
  - [ ] 三层过滤单测（fakes：指定 kb 只命中该库；"全部库"含默认库排除未归库；与现状行为一致性回归）
  - [ ] SDK 级跳过 + degraded_from 如实标注单测
  - [ ] Milvus 旧 collection 检测重建单测（fake client）
  - [ ] 实机：双库各构建 → 定向检索只命中目标库；删一库 → 未归库文档不再被"全部库"命中

### T6.3 会话选库链路（含 Agent 库路由 + 总览卡片）

- **目标**：新建会话选库（或 Agent 模式）→ A1 定向检索 → IR1 引用与决策留痕 → 会话 API 回带库名。
- **设计要点**：
  - workflow：`POST /sessions` 请求体 + `kb_ids: string[]`（可选，不传/空 = Agent 模式）；`KbBaseLookupEntity/Mapper`（@TableName("kb_bases")，同 ProjectLookup 先例）校验全部存在，无效 400；`DesignSessionEntity` + `@TableField(exist=false) kbNames`（get/list 批量回填，同 fillProjectNames 模式）。
  - py-engine：`storage/session_meta.py`（直读 design_sessions.kb_ids，同 ir_store 直读模式）；`GraphRAGService.route_bases(sub_queries)`：逐库轻量探测（首子查询向量/关键词命中计数）→ kb_ids 集合（全零命中兜底全部库 + 标注）；`a1_task`：manual 模式直接用会话 kb_ids，auto 模式先路由；IR1 增 kb_selection（D6-3 结构）+ retrieved_context 条目带 kb_id。
  - 前端：
    - `NewSessionDrawer` 增"知识库"多选 Select（options = GET /kb/bases，mode="multiple"，placeholder「不选则由 Agent 根据需求自动选定相关知识库」）。
    - `SessionPage` 总览新增**"知识检索"卡片**（OverviewTab）：
      - 上行 = 检索模式与选定库：人工模式（Tag 列表）/ Agent 模式（Tag + 每库依据「命中 N 条」，兜底全部库时如实标注"各库均无相关命中，已回退全部库"）；
      - 下行 = 实际命中聚合（IR1.retrieved_context 按 kb_id 分组 → 库名 + 命中 N 条 / 涉及 M 份文档 → **可展开文档级明细**（filename + location，doc filename 用现有 listDocuments 建映射））；检索降级（level=text / 全空）如实标注（复用 ir1Degraded 风格）。
    - `types.ts`：DesignSession + `kbIds?/kbNames?`；Ir1 + `kb_id?`（retrieved_context）+ `kb_selection?`。
- **DoD**：
  - [ ] workflow 单测：create 透传 / 无效 kb_id 400 / get·list 回填 kbNames（Mockito，26+ 用例基线）
  - [ ] py-engine 单测：manual/auto 两模式 kb_ids 传递；route_bases 选库与兜底逻辑（fakes）；IR1 留痕字段契约通过
  - [ ] 前端 vitest：命中聚合纯函数（kbHitSummary：分组/空容错/旧快照无 kb_id 容错/Agent 依据渲染）
  - [ ] 实机 e2e：双库 → 会话选库 A → IR1 引用全属 A；另建不选库会话 → Agent 圈定留痕展示；总览卡片两种模式走查
- **仪表盘说明**：会话**列表**不加库列（信息密度），"本次用到哪些库"在会话详情总览呈现。

### T6.4 知识库管理页重构（**mockup 先行，§7.3**）

- **目标**：知识库页从"平铺单库"重构为"多库管理 + 库内管线 + 未归库区"。
- **流程**：先出静态 mockup（库列表态 / 库详情态 / 新建库向导（含勾选既有文档）/ 归库·移动交互 / 删库确认 / 未归库空态与非空态）→ **用户确认后**再写码。
- **设计要点（mockup 评审稿，可调）**：
  - 页面结构：左侧库列表栏（库名 + 文档数徽标 + indexed 状态点，底部「＋新建知识库」，默认库带标识，**「未归库」固定入口带计数**）+ 右侧当前库内容；全局资产移顶部二级 Tab：「知识库｜三库管理｜LLM 供应商｜图谱」。
  - 新建库向导：名称/描述 → 勾选文档（全量文档多选，来源库列明；从其他库勾选时轻提示「将从来源库移出 N 份」）→ 创建并归库。
  - 库内右侧子 Tab =「文档｜抽取确认」（现 DocumentsTab/ExtractionsTab 加 kbId 参数化）；库详情提供「归入文档」（同勾选交互）与文档行「移动到…」。
  - 删库确认：明示「N 份文档将转为未归库（内容保留，暂不参与检索），可随时重新归库」；默认库删除入口禁用 + Tooltip。
  - 图谱 Tab 增库过滤下拉（GET /kb/graph?kb_id=，全部 = 不过滤）。
  - 视觉沿用全站 token（#e5e7eb 边框 / 石墨字阶 / 交互蓝 #2563EB 仅操作），库卡片风格对齐项目管理页声呐卡片网格语言。
- **DoD**：
  - [ ] mockup 六态经用户确认（§7.3 关）
  - [ ] 库 CRUD 全交互走查（建向导含跨库勾选 / 删除转未归库 / 统计刷新）
  - [ ] 归库/移动后总览统计与检索范围即时正确；未归库区预览可用
  - [ ] tsc/build/vitest 全绿；旧入口无死链

### T6.5 A5 回写手动选库

- **目标**：writeback 批准弹窗选目标库（D6-8），候选节点带归属。
- **设计要点**：审阅关②知识沉淀区批准交互加库下拉（默认值：会话人工选定首选库 / Agent 圈定首选库 / 默认库）；`/knowledge/writeback` 请求 + kb_id（后端必填校验，缺省默认库）；IR5 effects/annotation 记录归属库名；候选去重（find_name_matches）语料按目标库过滤。
- **DoD**：
  - [ ] 回写节点 kb_id 正确 + 图谱按库过滤可见（单测 + 实机抽查明细）
  - [ ] 前端弹窗默认值与提交体正确；去重语料按库过滤单测

### T6.6 审阅命中原文预览

- **目标**：审阅与参数页，A2 参数的 `source_ref`（retrieved: doc#loc）可一键查看知识库原文段落。
- **设计要点**：`GET /kb/refs?doc_id=&location=`（返回 chunk 原文 + 文档名 + 所属库）；ReviewTab 参数行加「查看知识原文」，弹 Drawer 展示原文 + 出处三件套（库 / 文档 / 位置），供设计人员核查方案依据；未归库/已删文档如实 404 提示「出处已不在检索范围」。
- **DoD**：
  - [ ] 引用解析 API 单测（命中/未命中/未归库）
  - [ ] ReviewTab 交互走查（retrieved 参数可溯源，estimated/derived 参数不显此入口）

### T6.7 e2e 与收官

- **e2e**（`tests/e2e/test_multikb_e2e.py`）：建双库（各归入文档）→ 构建 → 会话选库 A 跑 A1 → 断言 IR1 引用 kb_id 全属库 A + kb_selection mode=manual → 不选库会话断言 mode=auto + 依据留痕 → 总览数据核对 → 审阅命中原文预览 → 回写选库断言归属 → 清理（复用 _cleanup 模式）。
- 单测全量回归（py-engine / workflow 26 / biz 7 / frontend vitest）；HANDOFF 升级步骤（**必须跑 migrate CLI + 一次全量 build 重刷向量**）与 roadmap 回填。

## 4. 阶段管理

### 4.1 里程碑

| 里程碑 | 包含 | 判据 |
|---|---|---|
| M6-1 库模型与管理 API | T6.1 | 库 CRUD + 归库/删库转未归库 + 迁移 CLI 实机通过 |
| M6-2 定向检索 | T6.2 | 三层过滤 + 未归库排除单测 + 双库实机定向/全库对照 |
| M6-3 会话选库闭环 | T6.3 | e2e：选库 A / Agent 模式两路留痕；总览卡片如实 |
| M6-4 管理页 | T6.4 | mockup 确认后页面走查通过 |
| M6-5 回写与溯源 | T6.5+T6.6 | 回写归属正确 + 命中原文预览可用 |
| M6-6 收官 | T6.7 | 全量回归 + 文档回填 |

### 4.2 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| Milvus collection 重建窗口 | 升级期间向量级不可用 | 检索自动落文本级（A-14 既有降级）；重建+重刷分钟级（439 chunks），HANDOFF 写明升级顺序 |
| 存量数据迁移遗漏（Neo4j 节点无 kb_id） | 图谱过滤漏节点 | CLI backfill 幂等 + 过滤对空 kb_id 容错（空 = 未归库，不命中，语义自洽） |
| Agent 库路由选偏（漏相关库） | 方案知识原料不足 | 依据与命中数如实入 IR1 + 总览展示，设计人员可核可疑时改人工选库重跑；零命中兜底全部库 |
| 选库过窄导致检索零命中 | A2 方案缺知识原料 | A1 既有"检索全败如实补 missing"沿用；总览卡片如实显示零命中 + 建议换库重跑 |
| 跨库勾选把来源库抽空 | 库检索范围骤减 | 向导与移动交互轻提示「将从来源库移出 N 份」，无硬阻断（用户模型：文档可自由分组） |
| SDK 未来启用与多库冲突 | graphrag 级无法按库 | D6-5 已定性：定向检索跳过 SDK 级并如实标注；SDK 启用时另立按库索引决策 |

## 5. 决策记录（2026-09-19 用户拍板，原开放问题全部关闭）

| # | 问题 | 决策 |
|---|---|---|
| ① | 删非空库怎么处理 | **文档保留**：删库 = 成员转"未归库"（内容与索引数据全保留），新建库可从任意已上传文档（及未归库）勾选归入，也可在库内继续上传（D6-4/D6-10） |
| ② | 未归库文档是否参与检索 | **不参与**，归库后才恢复被检索（D6-9） |
| ③ | 不选库的语义 | **Agent 自主决策**：A1 库路由逐库评估相关度自主圈定，依据留痕可查（D6-3） |
| ④ | A5 回写归属 | **回写时手动选库**（默认会话首选库，可改）（D6-8） |
| ⑤ | 附加管理功能优先级 | 本期加做**命中原文预览**（T6.6）；库启停/导出导入/权限等按使用反馈再排 |
| ⑥ | 总览知识检索卡片粒度 | **展示 Agent/人工选择依据 + 命中文档明细可展开**（T6.3） |

## 6. 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-09-19 | 规划评审版：现状澄清（检索在 A1）、V9 数据模型、库 CRUD 与 force 级联、三层过滤方案、会话选库链路与总览卡片、管理页 mockup 先行、开放问题五项 |
| v0.2 | 2026-09-19 | 决策版（用户六项拍板）：删库改为**转未归库保留文档**（取消 force 级联）；kb_id 改可空 + 未归库语义（不参与检索）；新增文档归库/换库操作（新建库向导跨库勾选 + 三层零重建同步）；不选库改 **Agent 自主决策**（route_bases + IR1.kb_selection 留痕）；A5 回写改手动选库；新增 T6.6 命中原文预览；开放问题清零 |
