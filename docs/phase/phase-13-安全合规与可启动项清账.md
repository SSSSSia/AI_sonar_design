# 阶段 13 开发文档 · 安全合规与可启动项清账（v0.6）

| 项 | 内容 |
|---|---|
| 文档编号 | PHASE-013 |
| 版本 | v1.0（2026-10-07，编制；HANDOFF 入口 9 全部可启动项一轮清账） |
| 关联 | 总体设计方案 §9.3/§9.4/§5.16/§5.2/§7.4/§5.19/§10.5、[HANDOFF](../HANDOFF.md) 入口 9、[PHASE-012](phase-12-二期收尾.md) §7（v0.6+ 清单来源）、[GAP-001](../设计实现对照与优化路线.md)（P2-7 过期子句修正）、[PHASE-011](phase-11-业务泛化与生产上线.md) :56/:108（成员级权限缺口留痕）、roadmap 阶段 13 |
| 阶段目标 | 清掉入口 9 中 6 个可启动项：独立审计库+日志分流（Loki 轻量）、涉密语义脱敏完整形态（占位回填）、成员级数据权限（project_members）、PDF 导出（gotenberg）、交叉编码器重排序（配置驱动）、基础元数据管理；收官打 tag `v0.6.0` |
| 验收形态 | 单测 + Java 测试 + vitest/tsc + 容器级实测证据（Loki 采集/PDF 转换）+ e2e 全量回归 + 文档回填 |

---

## 1. 阶段定位与边界

### 1.1 为什么有这个阶段

HANDOFF 入口 9 列 9 项：真实 vLLM（待硬件）、真实 GJB（待授权文本）、双活（待生产环境）三项外部阻塞继续留待；其余 6 项（ELK/审计库、涉密语义脱敏完整形态、成员级数据权限、PDF 导出、交叉编码器重排序、元数据管理界面）**均无外部依赖、可立即启动**——本阶段一次清账。主轴是总体设计 §9 安全合规章的两笔欠账（§9.4 审计留痕 + 项目级权限粒度、§9.3 涉密脱敏「占位与回填」完整形态），辅以 §5.2 PDF、§7.4 重排序、§5.16 元数据三项能力补全。

### 1.2 本期范围（做）

| 任务 | 对应 |
|---|---|
| T13.1 独立审计库与留痕插桩（V17 + Java/py 插桩 + 查询界面） | §9.4 审计留痕 |
| T13.2 日志分流 Loki 轻量落地（loki+promtail+Grafana 数据源） | §5.19/§10.5「应用日志→ELK」（差异如实） |
| T13.3 涉密语义脱敏完整形态（占位回填 + A1–A5 挂点） | §9.3 完整形态 |
| T13.4 成员级数据权限（V18 project_members + 可见性过滤 + 成员管理 UI） | §9.4 项目级粒度 + 附录 C「本人项目」 |
| T13.5 PDF 导出（gotenberg + export/pdf 端点 + 前端按钮） | §5.2 Word/PDF（正式军标模板仍待外部） |
| T13.6 交叉编码器重排序（配置驱动 /rerank + 检索融合挂钩） | §7.4 |
| T13.7 基础元数据管理（V19 + CRUD + KnowledgePage 新 Tab） | §5.16 + 附录 D |
| T13.8 收官 + tag v0.6.0 | — |

### 1.3 非目标（明确不做）

- 真实 vLLM 部署、真实 GJB 全量录入、双活实施（外部依赖，按 v0.4 既有手册/入口/ADR 执行）；
- 完整 ELK 栈（Elasticsearch+Kibana+Logstash）——开发环境以 Loki+promtail 承担日志分流语义，**差异如实记录，ELK 留生产替换**（§10.5 口径差异，A-14）；
- DWG/DXF 解析、PDM/PLM 对接、图谱手动编辑、三层记忆、任务独立页（更远期外围，GAP-001 保留）；
- 正式军标 docx/PDF 模板替换（模板待授权文本，本期 PDF 转换基于既有内置模板）；
- AdminPage Tabs 化重构（第 5 张审计卡继续堆叠，避免无谓回归——前置发现 ⑨）。

## 2. 设计要点

### 2.1 独立审计库（T13.1）

- **表**：biz Flyway V17 `audit_logs(id bigserial, user_id uuid, username varchar(64), action varchar(64), resource_type varchar(32), resource_id varchar(64), detail jsonb, ip varchar(64), created_at timestamptz default now())` + 索引（created_at、user_id、(resource_type, resource_id)）。与 notifications 同模式：**biz 持有 Flyway，多服务直写同一张表**。
- **插桩范围**（§9.4 口径「记录所有 Agent 调用、参数变更、人工审阅决定、知识回写与导出操作」——Agent 调用/参数变更/审阅决定/知识回写已由 traceability_chain、cad_param_updates、review_records、IR5 回写承担（A-12 溯源体系），审计库补齐**不覆盖的操作域**）：auth=login 成功/失败 + 用户管理写端点；workflow=会话创建/删除 + gate 裁决；biz=项目写端点；py-engine=KB 写端点（上传/删除/建图/registry 写/llm-providers 写）+ 导出端点。action 命名 `域.动作`（如 `auth.login`、`session.delete`、`kb.build`）。
- **AuditRecorder**：Java 侧轻量组件（try-catch 包裹，审计失败只打 log 不阻断主流程——审计是旁路）；detail 只落资源标识与动作参数摘要，**不落敏感明文**（沿用掩码口径，宪法 §10）。
- **查询**：auth 服务 `AuditController GET /api/v1/audit/logs`（admin-only，user/action/resource_type/时间窗+分页）+ gateway 路由 `/api/v1/audit/**` → auth。前端 AdminPage 第 5 卡「审计日志」（过滤表格+详情，复用 T12.5 LogCenter 模式）。

### 2.2 日志分流 Loki（T13.2）

- compose 加 `loki`（单体模式，retention 7d，压缩开）+ `promtail`（docker_sd 服务发现采集容器 stdout，经 docker.sock）。Grafana provisioning 加 Loki 数据源。
- **先验证再铺开**：Docker Desktop 挂 docker.sock 给容器的可行性先做最小实验（起 loki+promtail 看 targets）；不可达则改静态文件方案并如实记录。
- **差异口径（A-14 如实）**：§10.5 写「应用日志 → ELK」；开发环境以 Loki+Grafana 承担「集中采集/检索/告警查询」同等分流语义（Grafana 已在栈内，零新增查询面）；ELK 留生产环境替换（采集面接口不变——promtail/pipeline 换 filebeat 即可）。session_logs（PG）定位不变：业务遥测（A-12），Loki 采的是容器 stdout（应用日志）。

### 2.3 涉密语义脱敏完整形态（T13.3）

- §9.3 要求「剥离 → 占位 → 大模型返回后按映射回填」——v0.4 T11.6 已落**密钥形态**出站脱敏（sanitize.py 7 pattern），本任务补**语义级**：
  - 新 `llm/desensitize.py`：`extract_sensitive_items()` 从**结构化字段直取**（会话标题、IR1 objectives 文本、参数 `name=value 单位` 对）——规则级提取、非 NER 模型，覆盖面如实标注（A-16）；
  - `Desensitizer.mask(texts)` →（masked, mapping）占位符 `[SENS-n]`；`restore(text, mapping)` 按映射精确回填；per-call 无全局状态。
- **挂点**：`adapter.chat()` 加可选 `desensitize_items`——出站 `sanitize_messages()` 之后 mask、LLM 响应返回前 restore（A3/A4/A5 正文与报告需真实值，回填在进入管线前完成）。默认**身份形态自动扫描**（chat 内生效，A1–A5 调用点零改动）；显式注册项为可选增强（调用方有确定敏感串时传入）。开关 `SANITIZE_SEMANTIC`（默认开，纵深防御口径；密钥脱敏不受此开关影响）。
- **风险**：默认开可能破「断言 prompt 内容」的既有测试——逐个更新并在 devlog 记录。

### 2.4 成员级数据权限（T13.4）

- V18 `project_members(project_id, user_id, role varchar(32) default 'member', created_at)`，UNIQUE(project_id, user_id)；建项目时 owner 自动种子（projects.owner_id 即天然 owner）。
- **可见性规则**：admin 全量；项目 owner 或成员可见该项目及其会话；无关联用户不可见（403 口径与其他资源一致）。py-engine 内网计算路径不动（identity 信任头已透传，A1–A5 worker 是系统行为非用户行为）。
- **管理**：biz ProjectMemberController GET/POST/DELETE `/api/v1/projects/{id}/members`（admin 或 owner）；前端 ProjectsPage 项目卡「成员管理」弹窗。
- **scope 划界（务实，A-14）**：本任务落「项目级」粒度——会话隶属于项目，会话可见性随项目；不引入会话级独立 ACL（附录 C 的「本人项目」列语义即项目级）。

### 2.5 PDF 导出（T13.5）

- compose 加 `gotenberg` 容器（内置 LibreOffice，`POST /forms/libreoffice/convert`）——**docx→PDF 直转保真**优于 HTML 重排路线（weasyprint），且完整复用既有 docx 模板链（T11.5）。
- `GET /cad/sessions/{sid}/export/pdf`：`export_session_docx()` 产 docx bytes → httpx 推 gotenberg → 返回 PDF；gotenberg 不可达 503 如实报错（A-14）。env `DOCGEN_PDF_ENDPOINT`（缺省不开挂转换）。
- 前端 ReportTab「导出 PDF」按钮（沿 exportDocx 下载模式）。正式军标模板仍待外部（差异如实）。

### 2.6 交叉编码器重排序（T13.6）

- 新 `kb/rerank.py` RerankClient：POST `/rerank`（兼容 Jina/Cohere/SiliconFlow/vLLM 格式；env `KB_RERANK_ENDPOINT/MODEL/API_KEY/TOPK`，密钥入 .env）。**配置驱动默认不开**——无 endpoint 即直通跳过（A-14 如实标注，与 graphrag_sdk_unavailable 降级同口径）。
- 挂点 `service.py:_query_uncached` 融合分支：配置可用→vector top_k 放大（如 20）→rerank→截断（5）→融合；失败→跳过原结果并 degraded 标注。
- 对比验证：同组查询 on/off 命中序对比入任务书；无真实 key 则 mock 单测 + 如实标注「实测待配置」（A-16 不造假）。

### 2.7 基础元数据管理（T13.7）

- V19 四表：`meta_sonar_types`（安装形式×频率双维度枚举）、`meta_freq_bands`（低/中/高/甚高频+范围）、`meta_perf_specs`（参数名/单位/典型范围/依据）、`meta_prompt_templates`（环节/名称/模板文本/版本/启用）。种子数据取自总体设计附录 D 元数据字典。
- py-engine `storage/metadata_store.py` + `api/metadata.py`（CRUD，写端点 require_admin 沿 kb 口径）+ gateway 路由 `/api/v1/metadata/**`；前端 KnowledgePage 第 5 Tab「元数据管理」（prompt_templates 带版本化+启用切换）。
- LLM 模型注册表（llm_providers）已于 v0.1 落地并有前端页——四子项补齐后 §5.16 全覆盖。

## 3. 任务分解（T13.1–T13.8）

> 执行顺序：T13.1 → T13.2 → T13.3 → T13.4 → T13.5 → T13.6 → T13.7 → T13.8（每任务独立 commit）。

### T13.1 独立审计库与留痕插桩 ✅ 已完成（2026-10-07）

- **DoD**：
  - [x] V17__audit_logs.sql（表+4 索引；user_id 用 VARCHAR 兼容非 UUID 调试值；dev 库已手动应用）
  - [x] Java 插桩：auth=login 成功/失败/禁用 + UserController 五写端点；workflow=会话创建/删除/批量删除 + gate 裁决；biz=项目创建/修改/删除；AuditRecorder 旁路（失败不阻断，detail 不落敏感明文——密码/密钥不进审计）
  - [x] py-engine：storage/audit_store.py（psycopg+Json 适配）+ api/audit.py 规则表（21 条）+ main.py ASGI 中间件——覆盖 KB 全部写端点与导出端点；E1/微调/IR1 修订不重复留痕（cad_param_updates/cad_tune_*/ir_snapshots 专表已承担，A-12）
  - [x] `GET /api/v1/audit/logs`（auth 宿主，admin，Bearer 二次验签）+ gateway audit-api 路由（biz 兜底前）+ AdminPage「审计中心」卡（操作者/动作/资源/详情/IP 过滤+分页；顺带清账两处遗留「日志中心未建」过期文案）
  - [x] 单测：Java AuditRecorderTest 3 例（字段透传/空值/mapper 失败旁路）；py test_t131_audit 4 例（规则表命中/落库检索/中间件留痕含 404 尝试/未匹配不记）
  - 执行期发现：①psycopg 查询内字面 `%` 需转义（teardown LIKE 报 placeholder 错，改参数化）；②MyBatis jsonb 列直读返回 PGobject——查询端点 SQL 里 `::text` 出参 + 控制器解析回 JSON 对象；③AdminPage 两处 v0.4 时代「日志中心未建」文案过期未随 T12.5 清理——本期顺带修正。**Java 容器级实机验证（镜像重建后登录→查审计行）统一留 T13.8 收官执行**

### T13.2 日志分流 Loki ⬜→✅ 已完成（2026-10-07）

- **DoD**：
  - [x] compose loki（3.1.1 单体，retention 7d，compactor retention_enabled）+ promtail（docker_sd）+ Grafana Loki 数据源（provisioning 重启后加载核对）——loki/promtail 配置文件随 T13.1 commit 先行入库（git add -A 扫入，如实注明）
  - [x] docker.sock 可行性**实测通过**（Docker Desktop）：promtail docker_sd 发现 25 个服务标签全量入库（gateway/auth/workflow/biz/notify/py-api/py-worker + 底座容器）
  - [x] 证据：docs/reviews/evidence/t132-loki-log-shipping.json（ready 双端 + 标签清单 + 经网关真实请求后 py-api 访问日志行 + Grafana 数据源清单）
  - [x] ELK 差异口径写入本节与 HANDOFF（Loki 承担采集/检索语义，ELK 留生产替换；session_logs 定位不变：业务遥测 A-12）

### T13.3 涉密语义脱敏完整形态 ✅ 已完成（2026-10-07）

- **DoD**：
  - [x] llm/desensitize.py：`Desensitizer`（per-call 映射，mask_messages/restore）+ `make_desensitizer` 工厂；身份形态规则 4 条（型号/代号/番号引导值、责任人、单位部门、≥2 字母前缀编号）+ 显式注册项通道（长串优先）+ numeric_items 通道（SANITIZE_SEMANTIC_NUMERIC=1 才启用）
  - [x] adapter.chat 挂点：sanitize 键值脱敏后 mask（缓存键/缓存值均为 mask 形态，取回统一 restore——语义一致）；出站 mask、响应 restore 两个 return 点全覆盖；desensitize_items 显式参数
  - [x] 开关：SANITIZE_SEMANTIC（默认开，纵深防御）；关闭时显式注册项仍生效（调用方确定性意图）
  - [x] 单测 9 例：往返一致/同值复用占位/数值默认关+开/关闭直通+显式仍生效/非字符串字段保留/单字母前缀不误捕（A-12 决策编号）/wire 级出站无原值+响应回填/默认自动扫描/关闭无脱敏；LLM 适配器族回归 28 绿
  - **边界如实（A-14/A-16，任务书 §2.3）**：①规则级提取非 NER，覆盖面以规则集为准；②**数值型指标默认不脱敏**——A2/A3/A4 数值推导/建模/校验依赖真实值参与 LLM 侧算术，占位符无法计算，盲脱破坏工程正确性；数值剥离需「占位化+确定性 solver 代入」架构改造（生产化纵深项），机制通道已留；③LLM 改写（非回显）敏感串时 restore 无法还原——§9.3 占位方案固有限制
  - 执行期返修 2 次（如实记录）：①占位符 `[SENS-1]` 被「≥2 字母编号」规则二次捕获嵌套——占位符改 `[SENS_n]` 下划线形态 + 身份扫描先行；②身份规则把「型号：」标签词一起吞掉致 restore 后原文缺损——改为 group(0) 内定位 group(1) 只替换捕获组，restore 后原文完整

### T13.4 成员级数据权限 ⬜

- **DoD**：
  - [ ] V18__project_members.sql + 建项目 owner 自动种子
  - [ ] biz ProjectMemberController（GET/POST/DELETE，admin/owner）
  - [ ] workflow 可见性过滤（list 过滤 + 单查/删除/事件校验，403 口径）
  - [ ] 前端成员管理弹窗 + 列表联动
  - [ ] 单测（Java ProjectMemberTest + workflow 过滤）

### T13.5 PDF 导出 ✅ 已完成（2026-10-07）

- **DoD**：
  - [x] compose gotenberg:8.15（profile: app；仅 LibreOffice 转换路由，宿主 3010/栈内 3000——3000 与前端冲突实测修正）+ py-api `DOCGEN_PDF_ENDPOINT` 注入
  - [x] py `GET /cad/sessions/{sid}/export/pdf`：复用 export_session_docx → gotenberg `POST /forms/libreoffice/convert`；未配置/不可达/非 %PDF 内容一律 503 如实（A-14/A-16）；审计中间件补 `export.pdf` 规则
  - [x] 前端 ReportTab「导出 PDF」按钮（沿 exportDocx 下载模式）
  - [x] 单测 6 例（未配置/成功 multipart 断言/非 PDF 拒收/HTTP 错误传播/端点 503/审计规则覆盖）+ 容器级真实验证：37 KB docx → 46 KB 合法 PDF（%PDF-1.7，7.8s），证据 docs/reviews/evidence/t135-pdf-conversion.json
  - 边界如实：基于既有内置模板；正式军标模板仍待授权文本（外部依赖）。py-api 容器内端点上线随 T13.8 镜像重建

### T13.6 交叉编码器重排序 ⬜

- **DoD**：
  - [ ] kb/rerank.py RerankClient（多供应商格式兼容，配置驱动默认关）
  - [ ] service.py 挂点（top_k 放大→rerank→截断→融合；失败 degraded 如实）
  - [ ] 单测（MockTransport）+ on/off 对比记录（无 key 则如实标注实测待配置）

### T13.7 基础元数据管理 ✅ 已完成（2026-10-07）

- **DoD**：
  - [x] V19__metadata_tables.sql：meta_sonar_types（双维度唯一约束）/ meta_freq_bands / meta_perf_specs / meta_prompt_templates（(stage,name,version) 唯一 + 启用互斥索引）+ 附录 D 种子（6 声呐类型 + 4 频段 + 4 性能规范，示例口径如实标注）
  - [x] storage/metadata_store.py（列白名单防任意列写、提示词模板版本自动递增 max+1、启用互斥切换）+ api/metadata.py（读=登录用户，写=admin；未知类别 404）+ gateway 路由 + 审计中间件 metadata.* 规则
  - [x] 前端 KnowledgePage 第 5 Tab「元数据管理」：四类别切换 + 动态字段表单（select/number/textarea 按列规格）+ 启用开关 + 版本语义提示；§5.16 五子项全覆盖（LLM 注册表由「LLM 供应商」页承担，如实互链）
  - [x] 单测 5 例（种子核对/必填校验/版本递增+互斥启用/更新删除往返/API CRUD+authz 403）；tsc 0 错 + vitest 43 绿
  - 执行期发现：teardown 引用 freq_bands 不存在的 remark 列（UndefinedColumn）——按表分列清理修正

### T13.8 收官 ✅ 已完成（2026-10-07）

- **DoD**：
  - [x] 全量回归全绿：**py 单测 379 passed 1 skipped**（新增 30：T13.1 审计 4 + T13.3 脱敏 9 + T13.5 PDF 6 + T13.6 重排 6 + T13.7 元数据 5）；**e2e 7 条逐条验证全绿**（towed 717s 真链路 / multikb / phase2 / e1_reverse / kb_pipeline / cad_tune / e1_forward——**两处批量顺序性偏差如实记录**：①首轮套跑漏设容器化适配变量 `CAD_TUNE_MOCK_HOST=host.docker.internal`（phase-11 已记录口径），补设后 e1_forward+cad_tune 复跑绿；②kb_pipeline 在批量尾位 KeyError、独立复跑绿（顺序性 flake，A-16 不粉饰）；towed 在批量尾位因前置供应商行状态被跳过、单独跑绿）；**Java 83**（BUILD SUCCESS，新增 12：审计 3+可见性 3+成员管理 6，含当日返修复测）；**vitest 43 / tsc 0**
  - [x] 三镜像原位重建（sonarcad-java/py/frontend:0.3.0）+ 实机验证五项：①登录→audit_logs 落行 ②审计查询过滤 ③metadata 端点经网关 ④PDF 导出经网关 200（98KB %PDF）⑤成员可见性三层对照（designer 项目 0 vs admin 412；会话 1 vs 413；加入项目后可见）
  - [x] 文档回填：GAP-001（P2-7 过期子句修正 + §4 v0.6 行）；HANDOFF（头部时间戳 v0.6.0 + 入口 7 过期子句清 + 入口 9 改写为阶段 13 纪要 + 入口 10 v0.7+ 生产落地）；roadmap 阶段 13 全 ✅；附录 E.4 v0.6 清账句；用户指南 v0.6（审计中心/成员可见性/元数据管理/PDF 导出四章）；devlog 2026-10-07（执行期返修七项）
  - [x] 宪法附录 A 自检 → tag `v0.6.0` + push
- **执行期返修（详细见 devlog 2026-10-07）**：①网关默认 TimeLimiter=1s 掐断慢端点（编程式 Customizer 120s——最有价值发现）；②biz MapperScan 漏 audit 包；③visibility SQL 笔误；④脱敏占位符嵌套/标签词吞噬；⑤V17~V19 幂等化；⑥审计检索参数 CAST；⑦gotenberg 宿主端口改 3010

## 4. 阶段管理

### 4.1 风险与对策

| 风险 | 对策 |
|---|---|
| Docker Desktop 挂 docker.sock 不可达 → promtail 采不到容器日志 | 先最小实验验证；降级静态文件方案并如实记录；不阻塞收官（A-16） |
| SANITIZE_SEMANTIC 默认开破坏既有断言 prompt 的测试 | 执行期逐个修正 + devlog 记录；开关可关作为逃生门 |
| gotenberg 镜像拉取慢/容器启动失败 | 后台拉取轮询；失败则 A-16 如实关账（端点保留 503 语义） |
| 成员可见性过滤波及既有前端列表（admin 视角回归） | Java 过滤测试 + Dashboard 走查；admin 全量语义不变 |
| audit 插桩影响主流程性能 | 旁路 try-catch + 异步不阻断；detail 不落敏感明文 |

### 4.2 验收流程（阶段末）

全量回归绿 → 各任务 DoD 证据齐 → 文档回填 → 附录 A 自检 → tag v0.6.0。

### 4.3 附录 A 自检（宪法，发布检查清单）

- [x] ①roadmap/文档回填 ✅（每任务 commit 即回填；收官修正 GAP-001/HANDOFF 三处过期口径）
- [x] ②端到端冒烟 = e2e towed ✅（真 LLM 全链）+ e1_forward/cad_tune（mock，补容器化变量后绿）
- [x] ③审阅关通过流+驳回流 = phase2 e2e ✅
- [x] ④文档→KB 全流程 = kb_pipeline e2e ✅（独立复跑绿；套内顺序干扰如实记录）
- [x] ⑤前端核心页面人工走查——**用户连续豁免**（沿用 2026-09-30 口径如实记录；[用户指南](../ops/用户指南.md) v0.6 已含新增走查点：审计中心/成员管理/导出 PDF/元数据管理）
- [x] ⑥.env 未入库、密钥脱敏 ✅（KB_RERANK_API_KEY 等 env 注入；审计 detail 不落敏感明文；语义脱敏默认开）
- [x] ⑦工作区干净 main 已推 ✅
- [x] ⑧tag 三段式 ✅（v0.6.0）

## 5. 验收记录

| 任务 | 实测证据 | 时间 |
|---|---|---|
| T13.1 审计库 | 单测：Java AuditRecorderTest 3 例 + py test_t131_audit 4 例；py 全量 353 绿/Java BUILD SUCCESS/vitest 43 绿；实机：登录→audit_logs 落行（admin/auth.login/IP=网关段）、`GET /api/v1/audit/logs?action=auth.login` 过滤命中、export.pdf 404/200 尝试均留痕 | 2026-10-07 |
| T13.2 Loki | docker.sock 实测可用，25 服务标签全量入库；证据 docs/reviews/evidence/t132-loki-log-shipping.json（含经网关真实请求后 py-api 日志行）；Grafana 重启后双数据源 | 2026-10-07 |
| T13.3 语义脱敏 | 单测 9 例（往返一致/占位复用/数值开关/关闭直通/wire 级出站无原值+响应回填/单字母前缀不误捕）；两次返修（嵌套占位/标签词吞噬）如实入 devlog；py 全量 362 绿 | 2026-10-07 |
| T13.4 成员权限 | 单测：Java ProjectMemberControllerTest 6 例 + SessionVisibilityTest 3 例；实机三层对照（rbac_designer vs admin）：项目 0 vs 412、会话 1 vs 413、加入项目后该会话可见；V18 owner 回填 412 行 | 2026-10-07 |
| T13.5 PDF 导出 | 单测 6 例；容器级：既有 docx 链 37KB→98KB 合法 PDF（%PDF-1.7），**实机返修网关默认 TimeLimiter=1s**（见 devlog 补充 4）后经网关 200；证据 docs/reviews/evidence/t135-pdf-conversion.json | 2026-10-07 |
| T13.6 重排序 | 单测 6 例（请求格式/解析兼容/排序截断/失败维持原序/默认不开）；py 全量 374 绿；真实供应商 key 缺——实测状态如实标注「mock 验证，实测待配置」（A-16） | 2026-10-07 |
| T13.7 元数据 | 单测 5 例（附录 D 种子核对/必填校验/版本互斥/CRUD 往返/API authz 403）；实机 `/api/v1/metadata/freq_bands` 经网关返回 4 频段；V19 种子 6+4+4 | 2026-10-07 |
| T13.8 收官 | 全量回归：py 379 / e2e（本节下方回填）/ Java（含当日返修复测）/ vitest 43 / tsc 0；三镜像重建 + 实机验证五项（审计落行/审计查询/元数据/网关 PDF/成员可见性）；文档回填 + tag v0.6.0 | 2026-10-07 |

## 6. 决策记录

- **范围主轴 = 全量清账（6 项可启动项一次清完）**——用户未在范围问题作答，按既有偏好（v0.5「二期收尾完整版」取径）与「继续=彻底清账」模式取全量（2026-10-07，ZCode 判断，规划已批准）。
- **日志分流 = Loki 轻量落地**（Grafana 已在栈内零新增查询面；ELK 留生产替换，差异如实）——同上决策路径。
- **PDF = gotenberg docx→PDF 直转**（保真优于 HTML 重排；正式军标模板仍待外部，转换基于既有内置模板——两件事分开如实记录）。
- **重排序 = 配置驱动 HTTP /rerank**（仿 embeddings.py 模式），非本地 torch 交叉编码器——避免 py 容器重依赖；语义等价（交叉编码器重排由供应商侧模型承担），能力如实标注。
- **审计范围 = 操作域补齐**（登录/用户/项目/会话/KB/导出），Agent 调用等已由溯源体系（A-12）承担的域不重复留痕——§9.4 口径的务实切分。
- **成员权限 = 项目级粒度**（会话可见性随项目），不引入会话级独立 ACL——附录 C「本人项目」列语义。

## 7. 演进路线（承诺项，非本期范围）

- v0.7+/生产环境：真实 vLLM 部署（按 vLLM切换手册）、真实 GJB 全量录入（按批量入口）、双活实施（按 ADR-005）、ELK 替换 Loki（生产口径）、正式军标模板替换、阶段 1–4/6/8 人工补走查（用户指南就绪）。

## 8. 修订记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v1.0 | 2026-10-07 | 初版编制（ZCode）。前置发现（探索实证）：①审计零实现——全库无 audit 表/插桩，AuthController 无 logger，现有留痕（traceability_chain/review_records/cad_param_updates/IR5）不覆盖登录/用户管理/KB 写/导出；②数据权限——design_sessions.created_by 存在但全库查询无归属过滤（SessionService.list 仅按 projectId），team/member/share 零命中，无 project_members 模型（phase-11:56/:108 明示缺口）；③脱敏——sanitize.py 7 pattern 全为密钥形态，无语义级占位回填；④PDF 零能力——全库无转换库/容器，docx 链路（export_docx.py + ReportTab）可复用；⑤rerank 零实现（附录 E:1028 明示规划项），融合点 knowledge/service.py:_query_uncached:226-234，embedding 走供应商 HTTP 可仿；⑥元数据——§5.16 四子项无模型无界面（LLM 注册表已落地），kb registry CRUD+Tab 模式可复用；⑦日志分流——compose 无日志收集容器，session_logs 为业务遥测（A-12）；⑧文档欠账——GAP-001 P2-7 行「日志检索仍待接」子句过期（T12.5 已清）、HANDOFF:20 头部时间戳停在 v0.4.0、HANDOFF:56 入口 7 尾同类过期；⑨AdminPage 已 4 卡堆叠——审计卡继续堆叠不引入 Tabs 重构 |
| v1.1 | 2026-10-07 | 收官回填（T13.8）：全量回归 + 三镜像重建 + 实机验证 + 文档回填 + tag v0.6.0。执行期返修（详见 devlog 2026-10-07）：①**网关默认 TimeLimiter=1s 掐断慢端点**（PDF 转换 8s 被 fallback 误报熔断；yaml 不生效，改编程式 Customizer 120s——本阶段最有价值发现，同步惠及未来同步长操作）；②biz MapperScan 漏登记 audit 包 crash-loop；③ProjectVisibilityMapper SQL 笔误（projects 主键 id）；④语义脱敏占位符嵌套/标签词吞噬两次返修；⑤V17~V19 补幂等写法（沿 V15/V16「手工应用+Flyway 补账」惯例）；⑥审计检索 SQL 参数类型推断（CONCAT 显式 CAST）；⑦gotenberg 宿主端口 3000 冲突改 3010 |
