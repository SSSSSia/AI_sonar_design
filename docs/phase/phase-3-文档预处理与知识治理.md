# 阶段 3 开发文档 · 文档预处理与知识治理

| 项 | 内容 |
|---|---|
| 文档编号 | PHASE-003 |
| 版本 | v1.0（2026-09-18，由 v0.1 规划版升级为完整版） |
| 关联 | CONST-001 §6（知识库构建管线约束，主约束源）、§5.4（API 基线）、§10.4（上传白名单）、A-13/A-14/A-16、D-14（OCR 可选插件）、roadmap 阶段 3 |
| 阶段目标 | 知识库从"单文件样例"升级为**可持续治理的领域知识库**：文档上传 → 解析 → 分块 → 抽取 → 索引全管线 + 公式/约束/军标库 + LLM 供应商管理 + 图谱可视化接口 |
| 验收形态 | 无 UI；全流程经 gateway（8080）API 走查；首样 = `towed_array_cad_knowledge.txt` 全流程入库；`tests/e2e/` 固化 |

---

## 1. 阶段定位与边界

- **为什么有这个阶段**：阶段 1/2 的知识库只有 KB 目录里的单个样例文件（text 级检索），A4 的军标框架是空占位，A-14 三级检索的"图谱/向量"两级没有真实数据源。本阶段把知识治理做成管线：文档可上传入库、解析带位置、抽取人工可审、索引异步可查，公式/约束/军标成为可管理的资产。
- **阶段边界（做什么/不做什么）**：
  - 做：T3.1–T3.4（见 §3）；首样全流程；A4 军标/公式库接线；LLM 供应商管理 API（补齐 5.16）。
  - 不做：MinerU/PP-Structure OCR 引擎本机部署（**插件化后置**——接口位预留，扫描页如实标注"OCR 待接"，D-14）；GraphRAG SDK 全量索引构建（重依赖 + token 成本大，图谱级检索以**自建 Neo4j 实体层**落地，SDK 装好后仍优先——对 v0.1 的一处收敛，理由见 §2 设计决策）；知识库管理 UI（阶段 4）。

## 2. 阶段架构与设计决策

### 2.1 数据流总览

```
上传(五格式) → MinIO kb-docs 桶（原文件）
   → 解析(按格式路由，保留页码/章节位置) → kb_documents(status=parsed)
   → 清洗分块(章节优先/重叠可调) → kb_chunks(doc_id+location)
   → LLM 本体抽取(pending) → kb_extractions → 人工确认 API
   → 异步构建 sonarcad.kb_build：chunks→embedding→Milvus；confirmed→Neo4j(带 source_doc)；
     chunks→检索文本级动态语料 → status=indexed（进度/状态可查，重试有上限 A-13）
检索(A-14)：图谱级=Neo4j 实体匹配(SDK 优先) → 向量级=Milvus → 文本级=关键词，逐级如实降级
```

### 2.2 服务与端口增量

| 服务/模块 | 变更 | 引入任务 |
|---|---|---|
| gateway | 新路由 `/api/v1/kb/**` → py-engine(8090)，经 JWT | T3.1 |
| py-engine api(8090) | `/kb/documents` 上传/列表/详情/删除、抽取预览/确认、三库管理、LLM 供应商管理、图谱查询；轻量信任头校验（X-User-Id 必带——收紧阶段 2 遗留点） | T3.1–T3.4 |
| py-engine worker | 新任务 `sonarcad.kb_build`（队列 sonarcad.kb） | T3.2 |
| py-engine knowledge | GraphRAGService 三级检索补全（Neo4j 图谱级 + Milvus 向量级） | T3.2 |
| py-engine solver | standards/formulas 库接线（读库优先，回退内存 registry） | T3.3 |
| biz | Flyway V6（kb_documents/kb_chunks/kb_extractions）+ V7（kb_formulas/kb_constraints/kb_standards） | T3.1/T3.3 |
| MinIO | 新桶 `kb-docs` | T3.1 |

### 2.3 横切约定与关键设计决策

- **KB API 鉴权**：gateway 注入 `X-User-Id`；py-engine 新增依赖校验该头存在（缺失 401）。写操作（上传/确认/三库变更）要求 admin/designer 角色头（轻量校验，完整 JWT 校验随阶段 4 收紧——如实记录）。
- **GraphRAG SDK 与自建图谱层的关系**：graphrag SDK（本机未装）仍是"native 图谱检索"的优先路径；未安装时图谱级 = 自建 Neo4j 实体/关系匹配（A5 沉淀 + T3.2 抽取确认写入的实体）。两级都不可用才落到向量/文本。`level` 与 `degraded_from` 如实标注实际路径（A-16）。
- **诚实红线**（§6）：`GET /knowledge/status` 与文档列表如实反映库现状（文档数/chunk 数/实体数/各层可用性）；单案例库不得包装成领域库。
- **删除同步清理**（§6 ⑦）：删除文档 → 删 MinIO 原件 + kb_chunks + Milvus 向量（按 doc_id 过滤删）+ kb_extractions；已入图实体不删、标 `deprecated: true`（沿用 A-10 语义）。

## 3. 任务分解（T3.1–T3.4）

> 执行顺序：T3.1 → T3.2 → T3.3 → T3.4 → e2e 验收。

### T3.1 文档上传与解析 ✅ 完成（2026-09-18）

- **目标**：五格式上传建档 + 按格式路由解析，解析产物保留页码/章节位置（§6 ①②）。
- **设计要点**：
  - 上传白名单（§10.4）：格式 `pdf|docx|xlsx|md|txt`，大小 ≤ 50MB，超限 413；上传即建档 `kb_documents`（status=uploaded）并存 MinIO `kb-docs/{doc_id}/{filename}`。
  - 解析路由：txt→段落；md→标题树章节路径；pdf→pypdf 逐页（`第N页`），空文本页计入 `ocr_pending` 并在详情如实标注"OCR 插件待接"；docx→heading 章节路径；xlsx→`工作表!行号`。解析失败：doc 状态=failed + error 如实记录，**不中断服务**（其他文档不受影响）。
  - 解析产物 units 存 kb_documents.parsed_units？不——直接进入分块（T3.2），解析与分块在同一异步任务里？**决策：解析同步做**（小文档秒级），上传接口直接返回解析结果摘要（页数/章节数/ocr_pending），units 存 `kb_documents` 的 jsonb 字段，status→parsed。解析异常转 failed。
- **接口契约**：
  | 方法 | 路径 | 说明 |
  |---|---|---|
  | POST | /api/v1/kb/documents | multipart 文件上传 → {doc_id, format, pages/sections, ocr_pending}（同步解析） |
  | GET | /api/v1/kb/documents | 文档列表（含状态/ocr_pending——诚实红线） |
  | GET | /api/v1/kb/documents/{doc_id} | 详情（含解析摘要 + error） |
  | DELETE | /api/v1/kb/documents/{doc_id} | 删除并同步清理（§6 ⑦） |
- **DoD**：
  - [x] 五格式各一真实样例解析通过且带位置信息（test_t31_kb.py 13 用例：txt 段落/md 章节树/docx 标题路径/xlsx 工作表行/pdf 页码）
  - [x] 扫描件 OCR 降级路径实测（pypdf 空白页 PDF → ocr_pending=2 如实计数，无伪造文本）
  - [x] 解析失败不中断服务（注入异常 → doc=failed + error，接口仍 200）
  - [x] 超限/非法格式 413/400；上传/删除鉴权（401 无信任头 / 403 非 writer 角色）
- **实现备注**：gateway 新增 `/api/v1/kb/**` → py-engine:8090 路由；py-engine `api/authz.py` 轻量信任头校验（X-User-Id 必带、writer 角色检查——收紧阶段 2 遗留点）；MinIO 新桶 `kb-docs`（对象键 `{doc_id}/{filename}`）；V6 迁移 kb_documents/kb_chunks/kb_extractions（chunks/extractions 对 doc 级联删除）。
- **OCR 插件落地（D-14，2026-09-18 补充）**：`kb/ocr.py` 接 PaddlePaddle/PaddleOCR-VL-1.5（SiliconFlow chat 接口，图片 base64 data URL）；pypdfium2 渲染扫描页位图；输出清洗 `<|LOC_x|>` 版面标记；temperature=0 确定性转写；单文档页数预算 `KB_OCR_MAX_PAGES`（默认 20，成本护栏）超出/失败如实降级回 ocr_pending；`temperature` 与识别提示词经实调校准。实机验收：图像版 PDF（2 页）上传 → ocr_pages=2、正文完整入解析单元；真实 API 用例经 `KB_OCR_REALTEST=1` 门控。

### T3.2 清洗分块 + 结构化抽取 + 索引构建 + 增量 ✅ 完成（2026-09-18）

- **目标**：可配置分块（§6 ③）→ LLM 本体抽取（§6 ④，人工预览）→ 异步索引（§6 ⑤）→ 增量更新（§6 ⑥）。
- **设计要点**：
  - 分块：`KB_CHUNK_SIZE`（默认 800 字符）/`KB_CHUNK_OVERLAP`（默认 150）可配；**章节优先**——不跨 location 边界，单元超长才内部切分；chunk 落 `kb_chunks`（doc_id+location+text），doc status→chunked。
  - 抽取：LLM 按 8 类本体类型（settings.yaml entity_types）逐 chunk 抽实体/关系 → `kb_extractions(status=pending)`；预览 API（GET）人工确认（PATCH confirmed/rejected）；LLM 不可用 → 抽取跳过并如实标注（构建仍可做向量/文本级）。
  - 构建：celery 任务 `sonarcad.kb_build`（队列 sonarcad.kb，max_retries=TASK_MAX_RETRIES）——① chunks→embedding（provider /embeddings，BAAI/bge-m3，1024 维）→ Milvus collection `kb_chunks`（upsert）；② confirmed 抽取→Neo4j MERGE（节点带 source_doc）；③ PG chunks 注册进检索文本级动态语料。embedding/Milvus/Neo4j 不可用→对应层跳过 + 文档状态如实（部分 indexed 标注），不伪造成功（A-16）。
  - 增量：任务按 doc_ids 处理（缺省=所有 status != indexed 的文档）；重构建先按 doc_id 删旧向量/旧 chunks 再插。改动单文档的重建时间 ≪ 全量。
- **接口契约**：
  | 方法 | 路径 | 说明 |
  |---|---|---|
  | POST | /api/v1/kb/documents/{doc_id}/chunk | 分块（body 可带 size/overlap 覆盖） |
  | POST | /api/v1/kb/documents/{doc_id}/extract | LLM 抽取（**异步任务**，返回 task_id；大文档并行抽取） |
  | GET | /api/v1/kb/extract/{task_id} | 抽取任务状态 |
  | GET | /api/v1/kb/documents/{doc_id}/extractions | 抽取预览（人工审） |
  | PATCH | /api/v1/kb/extractions/{id} | {status: confirmed\|rejected} 人工裁决 |
  | POST | /api/v1/kb/build | {doc_ids?: []} 触发异步构建 → {task_id} |
  | GET | /api/v1/kb/build/{task_id} | 构建进度/结果（进度可查 §6 ⑤） |
- **DoD**：
  - [x] 分块单测：章节优先不跨界、重叠正确、超长单元内部切分（test_t32_kb.py 16 用例）
  - [x] 抽取预览/确认单测（LLM fake + 清洗：非本体类型丢弃）；未确认候选不入图（断言）
  - [x] 构建任务：进度可查/失败重试有上限；Milvus/embedding 不可用逐层降级如实（build_detail）
  - [x] 增量构建实测：单文档重建 12~20s（e2e 记录），远小于全量管线（抽取 10 分钟级）
  - [x] 检索三级接线：graph（Neo4j 实体层）/vector（Milvus）/text 逐级降级单测（fakes）
- **实现备注（实机修正）**：抽取改异步任务 sonarcad.kb_extract（97 chunks 同步必超时）+ worker 内 4 并行 LLM 调用；语料刷新前先落 indexed 状态（否则取不到本档 chunks）；跨进程动态语料按 TTL 懒加载；MilvusClient 导入缺失修复。

### T3.3 公式/约束/军标库管理（首样入库）✅ 完成（2026-09-18）

- **目标**：三类知识资产管理（§6 + 5.14）+ LLM 供应商管理（5.16）+ 首样全流程。
- **设计要点**：
  - 三库表（V7）：`kb_formulas`（key/name/expr(SymPy 可解析)/symbols jsonb/unit/source_ref/enabled/confidence）、`kb_constraints`（name/expression/applies_to/severity hint/enabled/confidence）、`kb_standards`（code/name/applies_to/requirement/check_hint/enabled/confidence）。confidence ∈ high|medium|low（可信度分层）。
  - CRUD+启停 API：POST/GET/PATCH（含 enabled 切换）/DELETE，三库各一组。
  - **A4 接线**：`solver/standards.py` 增 `load_enabled_standards()`（读库，带 TTL 缓存；库空/库不可达→内存空 registry，check_param 维持"待补"诚实语义）；A4 公式复算解析顺序：solver REGISTRY → kb_formulas（expr 进 SymPy 同路复算）。
  - **LLM 供应商管理**：GET（api_key 脱敏 `****`）/POST（key 只写不读）/PATCH（enabled/priority/model/timeout）/DELETE `/api/v1/kb/llm-providers`——补齐 T1.2 预留（D-12 热切换的数据面）。
  - **首样**：`python -m knowledge.first_sample`——towed_array_cad_knowledge.txt 走 上传→解析→分块→抽取→确认→构建 全流程 + 播种 kb_formulas（声学约束公式节）/kb_constraints（设计约束节）；完成后一次真实检索应命中图谱级或向量级，`/knowledge/status` 如实展示。
- **DoD**：
  - [x] 三库 CRUD/启停单测（test_t33_kb.py 9 用例）；军标空态 check_param 维持"待补"（A-16 回归）
  - [x] 公式入库后 A4 复算可引用（单测 + 实机：库内 directivity_index 被 A4 定位复算）
  - [x] LLM 供应商 API：GET 响应不含 api_key 字段（脱敏红线），变更即 reload 热切换
  - [x] 首样全流程走通且检索命中图谱级（e2e：97 chunks→849 条抽取→向量 97/图谱 518 实体/语料 97→检索 level=graph）
- **实现备注**：首样 CLI（--skip-extract 快速模式）播种 5 公式 + 6 硬约束（幂等），实机验证通过后清理其重复文档（删除流程顺带实测 §6 ⑦ 向量/图谱同步清理）。

### T3.4 图谱可视化数据接口 ✅ 完成（2026-09-18）

- **目标**：力导向全图 + 邻域子图两类查询（节点带来源可反查文档）。
- **设计要点**：`GET /kb/graph?limit=300`（全图采样：非 deprecated 优先，节点+边）；`GET /kb/graph/neighborhood?key=&depth=2&limit=100`（BFS 邻域）；节点字段 {key,label,name,source_doc,deprecated}，边 {source,target,type}。Neo4j 不可达→503 如实。
- **DoD**：
  - [x] 真实库邻域查询毫秒级返回（e2e）；节点-文档互跳数据完整（KB 来源 source_doc / A5 会话来源 source_session 二者必有其一，实机 70 节点 = 50+21）
  - [x] 空图/缺失中心如实空集（单测）；Neo4j 不可达 503 如实降级
- **验收方法**：单测（fake driver + API 行为）+ e2e。

## 4. 阶段管理

### 4.1 里程碑

| 里程碑 | 包含任务 | 判据 |
|---|---|---|
| M3-1 上传解析 | T3.1 | ✅ 已达成（2026-09-18，五格式+OCR 降级+失败不中断，13 用例） |
| M3-2 管线与索引 | T3.2 | ✅ 已达成（2026-09-18，16 用例 + 检索三级 + 异步抽取） |
| M3-3 治理资产与首样 | T3.3 | ✅ 已达成（2026-09-18，9 用例 + A4 库接线实机验证） |
| M3-4 图谱接口 | T3.4 | ✅ 已达成（2026-09-18，3 用例 + 实机 70 节点来源完整） |
| M3-5 阶段收官 | e2e | ✅ 已达成（2026-09-18，首样 e2e 12m12s 全绿） |

### 4.2 依赖与顺序

T3.1 → T3.2 → T3.3 → T3.4 → 收官。T3.3 的 A4 接线依赖 T3.2 的构建产物（首样）；T3.4 依赖 T3.2 的 Neo4j 实体。

### 4.3 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| OCR 引擎（MinerU/PP-Structure）部署重 | 扫描件不可解析 | 插件化后置：解析器留 OCR 接口位，扫描页如实计数标注（D-14），不阻塞五格式主链 |
| GraphRAG 全量构建耗 tokens | 成本/时长不可控 | 图谱级以自建 Neo4j 实体层落地；SDK 全量构建留待真实多文档场景再评估 |
| embedding API 不稳 | 向量级缺失 | 构建逐层降级 + 如实标注；检索自动落文本级（A-14/A-16） |
| LLM 抽取质量参差 | 脏实体入图 | pending→人工确认后才入图（§6 ④），非本体类型清洗丢弃 |

### 4.4 验收流程（阶段末）

1. 逐任务 DoD 核对（本文件 + roadmap 双处一致）。
2. 阶段 3 e2e：首样全流程（上传→解析→分块→抽取→确认→构建→检索命中→图谱接口）一次走查。
3. 单测全量回归（py-engine + Java）。
4. 人工抽查：知识库现状诚实性（文档/实体数量对得上）；删除文档后的清理完整性。
5. 结论回填 §4 并同步 roadmap 与 HANDOFF。

## 4. 阶段验收记录

| 日期 | 事项 | 结论 |
|---|---|---|
| 2026-09-18 | T3.1 验收（五格式解析 13 用例 + OCR 降级实测 + 上传 API/鉴权） | ✅ 通过 |
| 2026-09-18 | T3.2 验收（分块/抽取/三层构建 16 用例 + 检索三级降级单测） | ✅ 通过 |
| 2026-09-18 | T3.3 验收（三库/供应商管理/A4 接线 9 用例 + 库内公式实机定位复算） | ✅ 通过 |
| 2026-09-18 | T3.4 验收（图谱接口 3 用例 + 实机 70 节点来源完整） | ✅ 通过 |
| 2026-09-18 | 首样 e2e（12m12s）：上传→解析 97 单元→分块 97→抽取 849 条→确认→三层构建（向量 97/图谱 518 实体/语料 97）→检索命中 graph 级→图谱接口→增量重建 | ✅ 通过 |
| — | **阶段 3 整体验收**：自动化全过；**待人工确认**（建议抽查：图谱浏览器看实体质量；删除文档的清理完整性） | ⏳ 待人工验收 |

## 5. 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-09-17 | 规划版：§6 硬性要求映射、四任务框架、风险识别 |
| v1.0 | 2026-09-18 | 升级为完整版：KB API 落 py-engine + gateway 路由 + 信任头校验；存储与表结构（V6/V7）；解析路由与 OCR 插件化决策；抽取人工确认门；kb_build 异步任务与增量语义；检索三级补全（自建 Neo4j 图谱层）；三库 + A4 接线 + LLM 供应商管理；图谱接口契约；GraphRAG SDK 全量构建后置的理由 |
| v1.1 | 2026-09-18 | 全任务收官回填：T3.1–T3.4 DoD 勾结、里程碑 M3-1~M3-5 达成、首样 e2e 12m12s 全绿（检索命中 graph 级）、抽取改异步任务、V8 修复 graph_key 列宽、图谱节点双来源（source_doc/source_session） |
| v1.2 | 2026-09-18 | OCR 插件（D-14）落地：PaddleOCR-VL-1.5 接入 `kb/ocr.py`，扫描页自动识别（页数预算+如实降级），真扫描件实机验收通过；文献语料导入 3 份（import_corpus 脚本，KB 达 4 文档/439 chunks/2939 条抽取） |
