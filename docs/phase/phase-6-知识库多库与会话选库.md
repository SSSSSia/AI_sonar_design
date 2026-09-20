# 阶段 6 开发文档 · 知识库多库管理与会话选库

| 项 | 内容 |
|---|---|
| 文档编号 | PHASE-006 |
| 版本 | v0.1（2026-09-19，规划评审版——**待用户确认后开工**） |
| 关联 | CONST-001 §6（知识管线）、§7.3（UI 先 mockup 后写码）、A-14（三级降级）、A-16（诚实红线）、A-02（全链路溯源）、§6 ⑦（删除同步清理）；阶段 3（KB 管线地基）、阶段 4（工作台 UI） |
| 阶段目标 | 知识库从"全局单库"升级为**多库**：库可建/改/查/删，文档按库上传治理，新建会话时选定本次设计使用的知识库，检索全链路按库过滤且如实留痕，会话工作台可见"本次用到了哪些库、命中了什么" |
| 验收形态 | 双库实测：两库各传文档并构建 → 新建会话只选库 A → IR1 检索引用只含库 A 文档 → 会话总览"知识检索"卡片如实展示；e2e 固化 |

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

因此本阶段把**选库点放在新建会话时**（与需求输入同一处、一次选定全程生效）：选定库 → A1 检索只打选定库 → A2 方案的知识原料即来自选定库。**中途改库（如驳回回退 A2 时换库）本期不做**——回退 A2 重算复用既有 IR1，检索不会重跑；要换库 = 新建会话（如实告知用户）。

### 1.3 边界（做什么 / 不做什么）

- **做**：多库数据模型与库 CRUD；文档上传/列表/抽取按库隔离；Milvus/Neo4j/文本语料三层检索按库过滤；会话创建选库（多选）；会话总览"知识检索"卡片（选定库 + 实际命中）；知识库管理页重构（mockup 先行）；A5 知识候选回写归属库。
- **不做（待定，见 §5 开放问题）**：库级权限/成员管理、库启停、库导出/导入、文档跨库移动、检索命中 chunk 原文预览、GraphRAG SDK 级按库过滤（SDK 当前未装，见 §2.3）、会话中途改库。
- **明确保持**：三库（公式/约束/军标）与 LLM 供应商是**全局资产**，不按库隔离（A4 校验用全局军标/公式库，与设计会话选库无关）。

## 2. 架构与设计决策

### 2.1 数据流总览（改造后）

```
库管理：kb_bases（CRUD，默认库不可删） ── 文档管线（不变，多带 kb_id）：
  上传(kb_id 必填，缺省默认库) → 解析 → 分块 → 抽取确认 → 三层构建
    ① Milvus：collection 增加 kb_id 字段（旧 collection 检测到无此字段 → drop 重建 → 全量重刷向量）
    ② Neo4j：节点/关系增加 kb_id 属性（存量 backfill 默认库）
    ③ 文本语料：动态语料结构扩为 (kb_id, doc_id, location, text)

会话链路：
  NewSessionDrawer 选库(多选，不选=全部) → POST /sessions {kb_ids}
    → design_sessions.kb_ids (jsonb, null=全部) → MQ sonarcad.a1(session_id)（契约不变）
    → py-engine a1_task 直读 design_sessions.kb_ids（同 ir_store 直读模式）
    → GraphRAGService.query_batch(queries, kb_ids) 三层过滤
    → IR1.retrieved_context 每条带 kb_id（契约加可选字段，向后兼容）
  会话总览：选定库（session.kb_names）+ 实际命中聚合（IR1.retrieved_context 按 kb 分组）
```

### 2.2 数据模型（Flyway V9，biz 服务迁移，py-engine 读写）

```sql
CREATE TABLE kb_bases (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        varchar(128) NOT NULL UNIQUE,
    description varchar(512),
    is_default  boolean NOT NULL DEFAULT false,   -- 种子"默认知识库"，不可删
    created_by  varchar(64),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO kb_bases (name, is_default) VALUES ('默认知识库', true);

ALTER TABLE kb_documents ADD COLUMN kb_id uuid REFERENCES kb_bases(id);  -- RESTRICT：删库必须走应用层级联清理（§6 ⑦）
UPDATE kb_documents SET kb_id = (SELECT id FROM kb_bases WHERE is_default) WHERE kb_id IS NULL;
ALTER TABLE kb_documents ALTER COLUMN kb_id SET NOT NULL;
CREATE INDEX idx_kb_documents_kb ON kb_documents(kb_id);

ALTER TABLE design_sessions ADD COLUMN kb_ids jsonb;  -- 会话选定的库；null = 全部（存量会话语义不变）
```

- **kb_documents.kb_id 外键不带 CASCADE**：删库的级联清理（MinIO 原件 + 向量 + 图谱 deprecated）必须走应用层复用现有 `delete_document` 清理逻辑，DB 层 RESTRICT 兜底防脏删。
- **design_sessions.kb_ids 只存意图**；"实际用到哪些库"以 IR1.retrieved_context 聚合为准（A-02 溯源、A-16 诚实——选了库但零命中要如实显示零命中）。

### 2.3 关键设计决策

| # | 决策 | 理由 |
|---|---|---|
| D6-1 | Milvus 单 collection + `kb_id` 标量字段 + 检索 `filter: kb_id in [...]`，不按库分 collection | 现网 collection 无此字段需一次重建（现存量 4 文档 439 chunks，重刷分钟级）；分 collection 的管理/嵌入成本高收益低 |
| D6-2 | Neo4j 节点/关系加 `kb_id` 属性，检索 `WHERE n.kb_id IN`；存量一次性 backfill 默认库（幂等 CLI `python -m kb.migrate_multikb`，顺带处理 Milvus 重建标记） | 图谱层与向量层过滤语义对齐；`deprecate_by_source_doc` 清理逻辑不变 |
| D6-3 | **不选库 = 全部库**（null 语义），选了 = 定向 | 与存量会话（无 kb_ids）行为完全一致，向后兼容；前端控件显式提示"不选则检索全部知识库" |
| D6-4 | 删非空库：默认 409 引导先清空；`?force=true` 走应用层级联（逐文档清理向量/图谱/原件 + 删文档行 + 删库行），前端二次强确认列明后果 | 与项目"仅空可删"心智一致，但用户明确要求"每个库都能删"——force 通道满足，默认通道防误删 |
| D6-5 | 选定库（kb_ids 非空）时 **SDK local/basic 级跳过**，degraded_from 如实记 `sdk_kb_scope_unsupported`（A-16） | GraphRAG SDK 是单一全局索引，无法按库过滤；当前环境 SDK 未装此路径本就不生效。SDK 若未来启用需按库拆索引目录，另立决策 |
| D6-6 | 会话选库经 workflow 校验存在性（跨服务直查 kb_bases，复用 ProjectLookupMapper 先例——同库 PG），无效 id 400 | 创建时即报错优于运行时静默零命中 |
| D6-7 | IR1.retrieved_context 条目加**可选** `kb_id`（契约 ir1.schema.json 同步），RetrievalResult.refs 同步 | 向后兼容：旧快照无此字段前端容错显示 |
| D6-8 | A5 知识候选回写 Neo4j 时带 `kb_id` = 会话首个选定库（未选 = 默认库） | 沉淀知识有库归属，图谱按库过滤可见；多库会话归属首个（开放问题 §5-③） |

### 2.4 服务与端口增量

| 服务/模块 | 变更 |
|---|---|
| py-engine api(8090) | `/kb/bases` CRUD + 列表带每库统计；`/kb/documents` 上传带 kb_id、列表按 kb 过滤；`/kb/graph` 可选 kb_id 过滤 |
| py-engine knowledge | `GraphRAGService.query/query_batch(…, kb_ids)`：graph/vector/text 三层过滤；refs 带 kb_id |
| py-engine worker | `a1_task` 读会话 kb_ids 传入检索；`kb_build` 写向量/图谱带 kb_id |
| py-engine kb | Milvus collection schema v2（kb_id 字段 + 旧版检测重建）；`kb.migrate_multikb` 存量迁移 CLI |
| workflow | `design_sessions.kb_ids` 透传（create/get/list 回填 kb_names，同 projectName 模式）；KbBaseLookup 校验 |
| biz | Flyway V9（kb_bases + kb_documents.kb_id + design_sessions.kb_ids） |
| frontend | 知识库页重构（左库列表 + 右库内管线）；NewSessionDrawer 选库；会话总览"知识检索"卡片 |

## 3. 任务分解（T6.1–T6.6）

> 执行顺序：T6.1 → T6.2 → T6.3 → T6.4（mockup 可提前出）→ T6.5 → T6.6 收官。

### T6.1 多库数据模型与库管理 API

- **目标**：kb_bases 建模 + 库 CRUD + 文档管线按库隔离 + 存量迁移。
- **设计要点**：
  - V9 迁移（§2.2）；`kb_store.py` 增 bases CRUD（create 重名 409 / list 带 per-库统计：documents、indexed、chunks、extractions_pending / patch 改名描述 / delete）；`list_documents(kb_id=)`、`doc_stats(per_base=True)`。
  - API（经 gateway `/api/v1/kb/**`，鉴权沿用 require_user/require_writer）：

    | 方法 | 路径 | 说明 |
    |---|---|---|
    | GET | /kb/bases | 库列表 + 每库统计（默认库置顶） |
    | POST | /kb/bases | {name, description}（writer；重名 409） |
    | PATCH | /kb/bases/{id} | 改名/描述（writer；默认库可改描述） |
    | DELETE | /kb/bases/{id} | 默认库 400；非空 409；`?force=true` 级联清理（§6 ⑦）|
    | POST | /kb/documents | multipart 增加 `kb_id`（缺省默认库） |
    | GET | /kb/documents | `?kb_id=` 过滤 |
  - 抽取确认/构建端点不变（doc 维度，doc 已携带库归属）；`/kb/build` 增量语义不变。
  - 存量迁移 CLI `python -m kb.migrate_multikb`（幂等）：Neo4j 存量节点 backfill kb_id、检查 Milvus collection schema 版本并按需重建（§2.3 D6-2）。
- **DoD**：
  - [ ] 库 CRUD 单测（重名 409 / 默认库保护 / 非空 409 / force 级联清理断言）
  - [ ] 上传带 kb_id、列表过滤、每库统计单测（沿用 test_t31/t32 fake 模式）
  - [ ] 迁移 CLI 实机幂等跑两遍（第二遍 no-op）
  - [ ] 网关走查：建库 → 上传两文档 → 列表统计正确

### T6.2 检索三层按库过滤

- **目标**：向量/图谱/文本三层检索接受 `kb_ids`，空 = 不过滤；refs 带 kb_id。
- **设计要点**：
  - `ChunkVectorStore`：schema 加 `kb_id VARCHAR(64)`；`ensure_collection` 检测旧 schema（无 kb_id 字段）→ 记日志并 drop 重建（升级后需触发一次全量 `POST /kb/build` 重刷向量——**升级步骤写入 HANDOFF**）；`upsert_chunks` 带 kb_id；`search(query_vector, top_k, kb_ids)` filter `kb_id in [...]`。
  - `Neo4jStore.find_entities_matching` 加 kb 过滤参数；`write_extractions_to_graph` / A5 writeback 写入 kb_id。
  - `GraphRAGService`：动态语料 `(kb_id, doc_id, location, text)`；`query(query, kb_ids=None)` / `query_batch(queries, kb_ids=None)`；graph/vector/text 三层下推 kb_ids；SDK 级按 D6-5 跳过并如实记 degraded_from；`RetrievalResult.refs` 增 kb_id。
  - 契约：`contracts/ir/ir1.schema.json` retrieved_context 条目加可选 `kb_id`；ir_contract.py 校验器放行（可选字段）。
- **DoD**：
  - [ ] 三层过滤单测（fakes：指定 kb 只命中该库 chunk/实体/段落；空 kb_ids 行为与现状一致）
  - [ ] SDK 级跳过 + degraded_from 如实标注单测
  - [ ] Milvus 旧 collection 检测重建单测（fake client）
  - [ ] 实机：双库各构建 → 定向检索只命中目标库；全库检索命中两库

### T6.3 会话选库链路（后端 + 总览卡片）

- **目标**：新建会话选库 → A1 定向检索 → IR1 引用带库 → 会话 API 回带库名。
- **设计要点**：
  - workflow：`POST /sessions` 请求体 + `kb_ids: string[]`（可选）；`KbBaseLookupEntity/Mapper`（@TableName("kb_bases")，同 ProjectLookup 先例）校验全部存在，无效 400；`DesignSessionEntity` + `@TableField(exist=false) kbNames`（get/list 批量回填，同 fillProjectNames 模式）。
  - py-engine：`storage/session_meta.py`（直读 design_sessions.kb_ids，同 ir_store 直读模式）；`a1_task` 读取并传入 `query_batch(sub_queries, kb_ids)`；`A1Agent._retrieve` 透传 kb_ids；IR1 retrieved_context 条目带 kb_id。
  - 前端：
    - `NewSessionDrawer` 增"知识库"多选 Select（options = GET /kb/bases，mode="multiple"，placeholder 提示"不选则检索全部知识库"）；提交体带 kb_ids。
    - `SessionPage` 总览新增**"知识检索"卡片**（OverviewTab）：上行 = 本次选定的库（Tag 列表 / "全部知识库"）；下行 = 实际命中聚合（IR1.retrieved_context 按 kb_id 分组：库名 + 命中 N 条 / 涉及 M 份文档，doc filename 用现有 listDocuments 建映射）；检索降级（level=text / 全空）如实标注（复用 ir1Degraded 风格）。
    - `types.ts`：DesignSession + `kbIds?/kbNames?`；Ir1 retrieved_context 条目 + `kb_id?`。
- **DoD**：
  - [ ] workflow 单测：create 透传 / 无效 kb_id 400 / get·list 回填 kbNames（Mockito，26+ 用例基线）
  - [ ] py-engine 单测：a1_task kb_ids 传递 + IR1 引用带 kb_id（fake kb_batch）
  - [ ] 前端 vitest：命中聚合纯函数（kbHitSummary：分组/空容错/旧快照无 kb_id 容错）
  - [ ] 实机 e2e：双库 → 会话只选库 A → IR1 引用全部 kb_id = 库 A → 总览卡片如实；另建全库会话对照
- **仪表盘说明**：会话**列表**不加库列（信息密度），"本次用到哪些库"在会话详情总览呈现；移动端列表描述行可带库名缩写（实现时按空间取舍）。

### T6.4 知识库管理页重构（**mockup 先行，§7.3**）

- **目标**：知识库页从"平铺单库"重构为"多库管理 + 库内管线"。
- **流程**：先出静态 mockup（库列表态 / 库详情态 / 删库确认态，含空态）→ **用户确认后**再写码。
- **设计要点（mockup 评审稿，可调）**：
  - 页面结构：左侧库列表栏（库名 + 文档数徽标 + indexed 状态点，底部「＋新建知识库」，默认库带标识）+ 右侧当前库内容；全局资产移顶部二级 Tab：「知识库｜三库管理｜LLM 供应商｜图谱」。
  - 库内右侧子 Tab =「文档｜抽取确认」（即现 DocumentsTab/ExtractionsTab 加 kbId 参数化，交互不变）。
  - 图谱 Tab 增库过滤下拉（GET /kb/graph?kb_id=，全部=不过滤）。
  - 删库：非空弹强确认 Modal（列明将清理 N 份文档及其向量/图谱引用），确认走 force；默认库删除入口禁用 + Tooltip 说明。
  - 视觉沿用全站 token（#e5e7eb 边框 / 石墨字阶 / 交互蓝 #2563EB 仅操作），库卡片风格对齐项目管理页声呐卡片网格语言。
- **DoD**：
  - [ ] mockup 三态经用户确认（§7.3 关）
  - [ ] 库 CRUD 全交互走查（建/改/删/force 删除后统计刷新）
  - [ ] 文档上传自动归属当前库；切库列表正确隔离
  - [ ] tsc/build/vitest 全绿；旧入口无死链

### T6.5 A5 知识候选回写归属库

- **目标**：writeback 写 Neo4j 候选节点带 kb_id（D6-8：会话首选库，未选 = 默认库）。
- **DoD**：
  - [ ] 回写节点 kb_id 正确 + 图谱按库过滤可见（单测 + 实机抽查明细）
  - [ ] 候选去重（find_name_matches）语料同样按库过滤（避免跨库误判同名——按 D6-8 归属库语料比对）

### T6.6 e2e 与收官

- **e2e**（`tests/e2e/test_multikb_e2e.py`）：建双库 → 各上传首样文档 → 分块/抽取确认/构建 → 建会话选库 A 跑 A1 → 断言 IR1 引用 kb_id 全属库 A → 总览数据核对 → 清理（复用 _cleanup 模式）。
- 单测全量回归（py-engine / workflow 26 / biz 7 / frontend vitest）；HANDOFF 升级步骤（**必须跑 migrate CLI + 一次全量 build 重刷向量**）与 roadmap 回填。

## 4. 阶段管理

### 4.1 里程碑

| 里程碑 | 包含 | 判据 |
|---|---|---|
| M6-1 库模型与管理 API | T6.1 | 库 CRUD + 按库隔离 + 迁移 CLI 实机通过 |
| M6-2 定向检索 | T6.2 | 三层过滤单测 + 双库实机定向/全库检索对照 |
| M6-3 会话选库闭环 | T6.3 | e2e：选库 A 会话 IR1 引用全属 A；总览卡片如实 |
| M6-4 管理页 | T6.4 | mockup 确认后页面走查通过 |
| M6-5 收官 | T6.5+T6.6 | 回写归属正确 + 全量回归 + 文档回填 |

### 4.2 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| Milvus collection 重建窗口 | 升级期间向量级不可用 | 检索自动落文本级（A-14 既有降级）；重建+重刷分钟级（439 chunks），HANDOFF 写明升级顺序 |
| 存量数据迁移遗漏（Neo4j 节点无 kb_id） | 图谱过滤漏节点 | CLI backfill 幂等 + 检索过滤对 null kb_id 容错（null 视同默认库） |
| 选库过窄导致检索零命中 | A2 方案缺知识原料 | A1 既有"检索全败如实补 missing"沿用；总览卡片如实显示零命中 + 建议换库重跑 |
| SDK 未来启用与多库冲突 | graphrag 级无法按库 | D6-5 已定性：定向检索跳过 SDK 级并如实标注；SDK 启用时另立按库索引决策 |

## 5. 开放问题（请评审拍板）

| # | 问题 | 推荐 |
|---|---|---|
| ① | 删非空库的形态：仅 force 通道 vs 引导清空即可 | **默认 409 + force 级联**（D6-4，两全） |
| ② | 不选库的语义：全部库 vs 默认库 | **全部库**（D6-3，向后兼容） |
| ③ | A5 回写归属：会话首选库 vs 默认库 vs 回写时手选目标库 | **会话首选库**（D6-8；手选目标库可作后续增强） |
| ④ | "其他基本知识库管理（待定）"候选优先级：库启停 / 文档跨库移动 / 检索命中 chunk 原文预览（审阅页看 A2 参数引用的知识原文）/ 库导出导入 / 库级权限 | 推荐下一批做**命中原文预览**（审阅价值最高），其余按需 |
| ⑤ | 总览"知识检索"卡片信息密度：仅库级聚合 vs 可展开到文档级明细 | **库级聚合 + 展开文档明细**（两层折叠） |

## 6. 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-09-19 | 规划评审版：现状澄清（检索在 A1）、V9 数据模型、库 CRUD 与 force 级联、三层过滤方案（Milvus 加字段重建 / Neo4j backfill / SDK 级跳过如实）、会话选库链路与总览卡片、管理页 mockup 先行、开放问题五项 |
