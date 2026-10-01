# 阶段 11 开发文档 · 业务泛化与生产上线（v0.4）

| 项 | 内容 |
|---|---|
| 文档编号 | PHASE-011 |
| 版本 | v1.0（2026-10-01，编制；依据 GAP-001 §3 P2 / §4 v0.4 行） |
| 关联 | [GAP-001](../设计实现对照与优化路线.md) §3 P2（P2-1/2/3/4/5/7/11）/§4、总体设计方案 §5.2（军标格式导出）/§5.18（通知中心）/§9.3（脱敏代理）/§11.2（M5）、附录 C 权限矩阵、[ADR-004](../adr/ADR-004-CAD交互微调集成路线.md)（E1 预留）、[PHASE-010](phase-10-生产就绪与M4验收.md)（前置）、roadmap 阶段 11 |
| 阶段目标 | 业务泛化五项（E1 CAD→文档反向回写、军标规模化录入与格式导出、细粒度 RBAC、通知持久化）+ 生产化两项（vLLM 切换就绪与 LLM 前置脱敏代理、容灾备份），对标 M5 中可软件交付部分，收官打 tag `v0.4.0` |
| 验收形态 | e2e（含 E1 新链路）+ 单测 + 操作手册 + 备份恢复演练证据 + 文档回填 |

---

## 1. 阶段定位与边界

### 1.1 为什么有这个阶段

总体设计 §11.2 的 **M5「业务泛化」**（生产部署、全量军标核对、用户可用）是本阶段验收锚点。两项**外部依赖**在编制时即明示（GAP-001 §4 末行），处理口径经用户 2026-10-01 确认：

1. **昇腾 910C + vLLM 硬件未到位** → 本期交付「切换就绪」：LLM 前置脱敏代理（设计 §9.3 纵深防御，纯软件）+ 供应商失败转移 + OpenAI 兼容 mock 验证切换链路 + 切换操作手册；真实 vLLM 服务待硬件按手册接入。
2. **军标标准文本授权未到位** → 本期交付「规模化录入能力」：批量 API + xlsx/csv 导入 + 批量验证数据；真实 GJB 全量核对待授权后由内部人员经批量入口完成。

### 1.2 本期范围（做）

| 任务 | 对应 GAP-001 |
|---|---|
| T11.1 通知持久化（notify 库表 + 查询已读 API + 前端登录拉取） | P2-4 |
| T11.2 细粒度 RBAC（三条硬规则）+ auth 用户管理 API | P2-5 + P2-7（用户管理部分） |
| T11.3 E1 CAD→文档反向回写（diff 提案→人工确认→IR2 新版本→下游失效→gate2 复审） | P2-1（+ P2-2 处置） |
| T11.4 军标批量录入（batch upsert + xlsx/csv 导入 + 批量验证） | 军标规模化（§4 v0.4 行） |
| T11.5 军标格式 Word 导出（IR2/IR3/IR4 三章渲染） | P2-11 |
| T11.6 vLLM 切换就绪（脱敏代理 + 失败转移 + 切换验证 + 操作手册） | vLLM 私有化切换（§2.6） |
| T11.7 容灾备份（备份/恢复脚本 + 定时 + GraphML 快照 + 恢复演练 + ADR-005） | P2-7（备份恢复）+ P2-3（knowledge-dumps）+ 双活方案 |
| T11.8 收官：全量回归 + M5 口径核验 + 文档回填 + tag v0.4.0 | — |

### 1.3 非目标（明确不做）

- 真实 vLLM 服务部署与昇腾适配（硬件到位后按 T11.6 手册执行）；
- 真实 GJB 标准文本全量录入与核对（授权到位后经 T11.4 批量入口执行）；
- 双活实施（PG 流复制、Neo4j causal cluster、MinIO 站点复制）——本期出 [ADR-005](../adr/) 方案文档定案，实施留生产环境；
- PDF 导出（docx 先行；PDF 可后续由 docx 转换，如实记录）；
- ELK 日志中心、前端 bundle 分割、P2-8/9/10/12 等未列入 v0.4 范围的 P2 项（日常迭代）。

## 2. 设计要点

### 2.1 E1 CAD→文档反向回写（T11.3）

- **插入点唯一**：`cad_agent/service.py:finalize_version`（:258-353）尾部——微调定稿当前只追加 IR3（`linked_param_id` 一律 None）+ WS 通知，零回写（ADR-004 D8：偏差交 gate2 裁决）。
- **diff 提案**：基线 IR3（tune.baseline_ir_version，A3 产出、`linked_param_id` 有值）vs 新 IR3 实测 geometry，对**长度/直径类可自动映射项**（`feature.linked_param_id` ↔ IR2 `parameter.id`，经 `linked_dim` 对应 bbox 维度）生成变更提案：`param_id / 旧值 / 新实测值 / 偏差% / 状态`；匹配逻辑复用 `a3_cad/geometry_link.py` 的粗匹配思路。
- **人工确认后回写**：IR2 追加新版本——`value` 与 `derivation` **必须同步**（A4 符号表来自 derivation 的"符号=数值"行，`solver/standards.py:117`，只改 value 会导致军标/公式复算用旧值）；`design_document` 追加修订记录段（何参数因何微调由何值改为何值，留痕）。
- **下游衔接**：IR4/IR5 **定向失效**（新增 markInvalidated 定向语义，不动既有"驳回整批"）→ 重派 `a4_task` → STAGE_DONE(a4) → gate2 复审（复用现有状态机幂等，沿用 T10.5 修复的防重/自愈语义）→ 审阅关②人工裁决。
- **P2-2 处置**：`parameter_schemes` 表（建成但零引用）随本任务二选一定案——作 E1 提案存储或迁移注释声明"预留，IR2 快照为权威"，消除表-实现歧义。

### 2.2 细粒度 RBAC（T11.2）

- 现状：五表齐全已播种（4 角色 8 权限点）、JWT 带 roles、gateway 注 `X-User-Id/X-User-Roles`、biz/workflow 已有 `@PreAuthorize`。缺口：auth 无用户管理 API（AdminPage「待接」）；**py-engine `authz.py` 空角色集放行**（require_writer/require_admin 双漏洞）；KB 写端点用 require_writer（admin/designer）而非 admin。
- **三条硬规则**（GAP-001 P2-5）：①审阅关裁决权 = `hasAnyRole('admin','reviewer')`（workflow 已有，补测试固化；「本人项目」成员级数据权限因无 project_members 模型，**角色级先落 + 如实记录**成员级留后续）；②知识库写权限 = admin（py-engine registry/文档写端点收紧）；③系统管理页门禁 = admin（py-engine 管理面 require_admin 全覆盖核验 + 前端已有路由守卫）。
- **authz.py 修复**：无角色头 → 403（gateway 恒注入角色头，空角色 = 异常请求；放行语义废弃）。
- **auth 用户管理 API**（admin-only）：列表 / 创建（BCrypt）/ 启停 / 重置密码 / 角色分配；AdminPage 用户管理页接通。

### 2.3 通知持久化（T11.1）

- 现状：notify 纯内存 WS 广播（零持久化、无 db/migration）；前端 localStorage 过渡态（刷新仍在、换端即失）；告警桥接（T10.1c）也只广播。
- **方案**：biz 库 V14 `notifications` 表（`user_id` 可空 = 广播、`session_id`、`type`、`title`、`payload jsonb`、`read_at`、`created_at`）；notify 模块加 datasource（复用 biz 库，避免动 PG init 脚本）；InternalPublish/InternalAlert 广播同时落库；查询/已读 API 挂 notify，经 gateway 新路由 `/api/v1/notifications/**`（信任头鉴权）；前端 notifyStore 登录后拉取与 WS 实时流合并、已读调 API、未读徽标用服务端未读数。

### 2.4 军标批量录入（T11.4）

- 单条 CRUD/前端表单已齐（PHASE-009 T9.3），批量入口缺。批量 API 按 `code` upsert（冲突策略：更新已有行字段 + 报告差异清单）；xlsx/csv 导入复用 `kb/parsers.py` 解析思路（列头 → 字段白名单校验，`expr` 列留空 = 仅登记提示性核对）。
- 前端 RegistryTab 批量入口：上传/粘贴 → 预览（逐行校验结果）→ 确认入库；入库后 `standards.reset_cache()` 热生效。

### 2.5 军标格式 Word 导出（T11.5）

- `python-docx>=1.1` 已在 requirements（现仅用于解析输入），导出方向零成本起步。素材 = IR2（design_document + parameters）/ IR3（geometry_summary + features）/ IR4（issues + csr）快照，`ir_store` 可取。
- 结构按设计 §5.2 排版约定：封面（项目/会话/版本/日期）→ 设计文档正文（IR2.design_document）→ **参数表**（id/名称/数值/单位/来源分类/溯源）→ **溯源依据**（source_type 分组 + trace 反查指引）→ **校验结论**（overall/CSR/issue 分级明细）。
- **如实标注**：内置程序化模板（标题层级/表格样式/中文字体），非授权军标正式模板；格式如有出入以用户提供的正式模板替换（预留 template 覆挂点）。

### 2.6 vLLM 切换就绪 + 脱敏代理（T11.6）

- **脱敏代理**（设计 §9.3 纵深防御，宪法 §10 红线扩展到 LLM 出站内容）：`llm/sanitize.py` 在 chat() 出站前对消息内容做键值脱敏——模式：API key/token/密码赋值形、内网地址与连接串、`.env` 键名值对；命中记审计日志（脱敏后的位置与类别，不记原值）；`SANITIZE_LLM_CONTENT` 开关默认开，关闭需显式 env。
- **失败转移**：chat() 重试链升级——当前供应商重试耗尽后自动切换 priority 次高 enabled 供应商再试（可配置 `LLM_FAILOVER=1` 默认开）；`GET /llm/provider` 暴露各 provider 最近健康状态（来自调用结果）。
- **切换验证**：e2e 用 OpenAI 兼容 mock 模拟 vLLM endpoint——注册 provider（base_url 指向 mock）→ 热切换生效 → 出站内容经脱敏断言 → 主 provider 故障自动转移次选。
- **操作手册** `docs/ops/vLLM切换手册.md`：通用 GPU 与昇腾两形态（vLLM 起 OpenAI 兼容服务 → `POST /kb/llm-providers` 注册 → TTL 内自动生效），含回退步骤。

### 2.7 容灾备份（T11.7）

- `deploy/backup/`：`backup.sh`（pg_dump auth/biz/workflow 库 + neo4j-admin dump + MinIO `mc mirror` 数据桶 + redis BGSAVE 拷贝 + rabbitmq definitions 导出）、`restore.sh`（逆序恢复）、README（RPO/保留策略/恢复步骤）。
- 定时执行：compose `--profile backup` 侧car（cron 镜像挂 backup 脚本与备份目录，默认每日 02:00，`BACKUP_CRON` 可调）。
- **GraphML 快照**（承接 P2-3）：Neo4j 全图导出 GraphML 落 MinIO `knowledge-dumps` 桶（桶随此创建）——py-engine admin 端点触发 + 备份脚本调用；`docs/reference` 桌面原文不动。
- **恢复演练**（A-16 落档）：干净目录执行 restore → PG/MinIO/Neo4j 数据可读、会话页面可打开，证据入 `docs/reviews/`。
- **ADR-005**：容灾与双活路线（PG 流复制 / Neo4j causal cluster / MinIO 纠删与站点复制的选型与触发条件=生产部署）。

## 3. 任务分解（T11.1–T11.8）

> 执行顺序：T11.1 → T11.2 → T11.3 → T11.4 → T11.5 → T11.6 → T11.7 → T11.8（T11.4/T11.5 可并行于 T11.3 之后；每任务独立 commit）。

### T11.1 通知持久化 ✅ 已完成（2026-10-01）

- **DoD**：
  - [x] biz V14 `notifications` 表迁移 + notify 模块 datasource 接入（扇出落库，含 alert 类型；修订：**notify 不引 Flyway**——迁移由 biz 统一执行，避免共库双 flyway_schema_history 冲突，见 §8 v1.1）
  - [x] 查询/已读 API（列表分页 + 未读数 + 单条已读 + 全部已读 + 删除/清理）经 gateway `/api/v1/notifications/**` 路由可达，信任头鉴权 + 平台统一响应包 {code:0,message,data}
  - [x] 前端 notifyStore 登录后拉取（hydrate 按 client_key 幂等合并、WS 实时项去重）+ 已读/删除/清理同步调 API、未读徽标本地口径；vitest 3 新用例（共 41 passed）
  - [x] Java 测试：NotificationApiTest 8 用例（落库接线/角色扇出/过程类不落库/降级 200 广播、401/503/清理 scope）+ NotifyIntegrationTest 9 用例回归，notify 17 全绿
  - [x] 容器栈真实链路验证：V14 已应用（dev PG 手动 + biz 下轮启动 Flyway 幂等）；内网发 review_request → admin 落库、designer（无审阅角色）无行、task_progress 不落库；重发同信封内容不同时间戳=新事件新行（唯一键拦同信封重试）；alert 带 Bearer 落库全员（admin+designer 各一行、服务端标题「系统告警 firing（1 条）」）；单删/清空按用户隔离（admin 清空后 designer 行仍在）

### T11.2 细粒度 RBAC + 用户管理 ✅ 已完成（2026-10-01）

- **DoD**：
  - [x] auth 用户管理 API（admin-only，Bearer 二次验签：列表/创建 BCrypt/启停/重置密码/角色替换/删除 + 护栏：不能停用删除自己、不能动最后一个 active admin）+ AdminPage「用户与角色管理」卡接通（替换「待接」行）
  - [x] 三条硬规则：①审阅关裁决权 = `hasAnyRole('admin','reviewer')`（workflow 既有，新增 ReviewControllerRbacTest 注解反射守护；「本人项目」成员级数据权限因无 project_members 模型，角色级先落、如实记录留后续）②KB 写权限 = admin：py-engine kb.py **13 个写端点** require_writer→require_admin（designer 不再可写知识库）③系统管理门禁 = admin：`/llm/provider/reload`（原无鉴权）→ require_admin、`/llm/provider` → require_user
  - [x] `authz.py` 空角色放行漏洞修复（无角色头 → 403；旧语义「角色头缺失即放行」是审查确认的越权漏洞，gateway 对合法 JWT 恒注入角色头故正常流量不受影响）
  - [x] py 单测 6 新用例（test_t112_rbac.py，全量 302 passed 无回归）+ Java auth 11 用例（新 UserControllerTest）+ workflow RbacTest；容器栈真实生命周期验证：创建 reviewer→登录→列表 403→停用→登录 403→重置密码+启用+升 admin→登录 200→删除

### T11.3 E1 CAD→文档反向回写 ✅ 已完成（2026-10-01）

- **DoD**：
  - [x] finalize_version 尾部 diff 提案生成（`cad_agent/param_proposals.py`：segment 对位 + 仅长度类联动项 + 1% 噪声阈；分段数变化/链式微调基线如实产 0 条）+ biz V15 `cad_tune_param_proposals`（(tune,round,param) 唯一、整组替换幂等；**P2-2 定案 = parameter_schemes 迁移注释声明「预留，IR2 快照为权威」**）
  - [x] 提案查询/确认回写 API：IR2 新版本（value+derivation **原地同步**——符号表 setdefault 首匹配，追加行不生效故替换首个 ≈旧值的 symbol=数值 + 尾部修订记录 + design_document 修订段）→ IR4/IR5 定向失效（`ir_store.mark_invalidated`，py 侧新增，与 Java mapper 同语义）→ a4_task 重派（进程内 delay）→ STAGE_DONE 自动重挂 gate2（复用 pauseAtGate 乐观条件，零 Java 改动）；派发失败自愈（提案退回 proposed + 503 幂等重试）；completed 会话 409（无复审链路，如实拒绝）
  - [x] 前端 ReviewTab「参数变更提案」卡（Ir3Card 后：参数/原值/实测/偏差/操作，单项应用/忽略/全部应用）
  - [x] e2e `test_e1_reverse_write_e2e.py` 全闭环（39s：真实 STEP 100→120mm → 提案 1 条 → 确认 → IR2 v2 derivation 同步断言 → 自动重挂 gate2 → 批准 → A5 → completed → 提案终态 applied）
  - [x] A-16 抽查：单测覆盖无联动基线/分段变化/噪声阈不产提案；derivation 未同步时符号表用旧值的风险以原地替换消除（parse_inputs_from_derivation 回读断言）

### T11.4 军标批量录入 ✅ 已完成（2026-10-01）

- **DoD**：
  - [x] `POST /kb/registry/{kind}/batch`（500 行/批上限；冲突键 upsert + 逐行错误清单）+ `POST /registry/{kind}/import`（xlsx/csv 解析，utf-8-sig/GBK 兼容，enabled 中英布尔归一，dry_run 预览不入库）；批量 upsert = **单连接单事务 + 每行 SAVEPOINT**（实测教训：每行独立连接 25 行 ~2-3s 被网关慢调用熔断误杀）
  - [x] 前端 RegistryTab「批量导入」（军标页签）：上传 → dry_run 预览（行数+问题清单）→ 确认入库 → reset_cache 热生效
  - [x] 25 条可机读演示条目批量入库验证（`deploy/seed/demo_standards_batch.csv`，【演示】+ confidence=low + **enabled=停用**——不干扰现网 A4 核对，真实 GJB 授权后经此入口规模化录入并启用）；容器栈实测：dry_run 预览 25 行 0 问题 → 导入 25 → 重导幂等（25 updated/0 inserted）

### T11.5 军标格式 Word 导出 ✅ 已完成（2026-10-01）

- **DoD**：
  - [x] `docgen/export_docx.py`：封面 → 设计文档正文（IR2.design_document Markdown 轻量渲染：标题/表格/段落）→ 参数表（7 列含来源分类中文化）→ 溯源依据（source_type 四分类分组 + derivation）→ 校验结论（overall/CSR/issue 分级表）；DOCX_TEMPLATE_PATH 覆挂点预留
  - [x] `GET /cad/sessions/{sid}/export/docx`（RFC 5987 中文文件名）+ 前端 ReportTab「导出 Word」按钮（getBlob 下载）
  - [x] 单测 4 例（重开断言章节/表格行数/CSR/中文化/素材缺失如实标注）；容器栈真实验证：真实会话（基线 round-2）导出 42KB，四章齐、参数表 20 行、总体结论/CSR=100.0 如实渲染；内置模板（非授权军标模板）如实标注

### T11.6 vLLM 切换就绪 + 脱敏代理 ✅ 已完成（2026-10-01）

- **DoD**：
  - [x] `llm/sanitize.py` 出站键值脱敏（sk- key/Bearer/password/带凭据连接串/.env 密钥行七类模式；默认开 `SANITIZE_LLM_CONTENT=1` 显式 0 才可关；审计日志只记类别与次数不记原值）；接入 chat() 入口——缓存键随之基于脱敏后内容
  - [x] 失败转移：主 provider 重试耗尽自动切 priority 次高（`LLM_FAILOVER` 缺省开）+ `_record_health` 健康态经 `GET /llm/provider` 的 health[] 暴露（进程内口径如实注明）
  - [x] 验证：单测 8 例（七类模式/正常文本零命中/开关语义/wire 级出站断言——httpx 打桩捕获请求体不含原始密钥且 [REDACTED] 在场/转移链 3 次调用 2+1/关闭转移仅主供应商/健康记录）；容器栈实测：注册 vllm-sim（OpenAI 兼容 mock）→ 热切换 active → 容器内真实 chat → 审计日志「脱敏命中 api_key×1」→ 健康态 ok=True → 删除回退
  - [x] `docs/ops/vLLM切换手册.md`（通用 GPU/昇腾 vllm-ascend 两形态 + 注册/回退/验收清单；真实 vLLM 待硬件如实记录）

### T11.7 容灾备份 ⬜

- **DoD**：
  - [ ] `deploy/backup/` backup.sh/restore.sh/README（四库 + Neo4j + MinIO + Redis + RabbitMQ definitions；RPO/保留策略）
  - [ ] compose `--profile backup` 定时 sidecar（BACKUP_CRON 可调）+ GraphML 快照落 knowledge-dumps 桶（P2-3 承接）
  - [ ] 恢复演练证据入 docs/reviews/（干净目录恢复后数据可读、会话可打开）
  - [ ] ADR-005 容灾与双活路线（方案定案，实施触发条件=生产部署）；AdminPage 备份行状态更新

### T11.8 收官 + tag v0.4.0 ⬜

- **DoD**：
  - [ ] 全量回归：py 全量单测 + e2e 全套（含新增）+ Java mvn test + vitest
  - [ ] M5 口径逐项如实核验：生产部署 = 容器化全栈+监控+备份就绪（单机形态，集群留生产环境）；全量军标核对 = 批量能力就绪 + GJB 待授权（如实）；用户可用 = 用户管理/RBAC/E1/导出可用
  - [ ] 文档回填：roadmap 阶段 11 全任务、phase-11 §5/§8、GAP-001 P2 相关行、HANDOFF、附录 E.4、devlog
  - [ ] 宪法附录 A 自检 → tag `v0.4.0`（§8.3 三段式）+ push

## 4. 阶段管理

### 4.1 风险与对策

| 风险 | 对策 |
|---|---|
| E1 改动波及审阅关状态机（T10.5 刚修复幂等） | 定向失效与重派严格复用既有语义；e2e 全量回归含 phase2/cad_tune 两条门语义链路 |
| RBAC 收紧破坏既有 e2e/前端流 | 收紧点逐一过回归；前端对 403 统一提示；designer 账号实测越权路径 |
| 通知落库写放大（WS 高频 task_progress） | 落库限 review_request/ir_updated/alert 等关键类型，task_progress 等过程类仍仅广播（口径写进代码注释与文档） |
| 备份脚本误操作损伤在跑栈 | 备份只读（dump/mirror）；restore 演练在独立目录/临时容器进行；先演练后收官 |
| 军标导出格式与正式模板出入 | 如实标注内置模板；预留模板覆挂点，待用户提供正式模板替换 |

### 4.2 验收流程（阶段末）

全量回归绿 → 各任务 DoD 证据齐 → M5 三项口径逐项核验（如实）→ 文档回填 → 附录 A 自检 → tag v0.4.0。

## 5. 验收记录

| 任务 | 实测证据 | 时间 |
|---|---|---|
| T11.1 通知持久化 | Java notify 17 用例全绿（新 NotificationApiTest 8）；vitest 41 passed（新 3）；容器栈真实验证：review_request 角色扇出（designer 无行）/ alert 全员扇出 + 服务端标题 / task_progress 不落库 / 单删清空按用户隔离 / 503 降级；gateway 路由 + 统一响应包实测 200 | 2026-10-01 |
| T11.2 RBAC+用户管理 | py 302 passed（新 6）；auth UserControllerTest 11 用例；workflow RbacTest 注解守护；容器栈真实生命周期：designer 访问用户列表 403 / user-qa 创建→停用→登录 403→重置密码→升 admin→删除全链 200/403 符合预期 | 2026-10-01 |
| T11.3 E1 反向回写 | py 312 passed（新 10）；e2e 全闭环 39s 一次通过（真实 STEP 100→120mm：提案→确认→IR2 v2→A4 重算→自动重挂 gate2→批准→completed）；P2-2 随此定案 | 2026-10-01 |
| T11.4 军标批量录入 | py 320 passed（新 8）；容器栈实测 25 条批量导入（dry_run→导入→重导幂等）+ 熔断误杀返修（单连接 SAVEPOINT 重构） | 2026-10-01 |
| T11.5 Word 导出 | 单测 4 例全绿；容器栈真实会话导出 42KB——四章齐/参数表 20 行/总体结论与 CSR 如实渲染（python-docx 重开断言） | 2026-10-01 |
| T11.6 vLLM 就绪+脱敏 | 单测 8 例（wire 级出站断言+转移链）；容器栈实测：vllm-sim 热切换→chat→审计「脱敏命中 api_key×1」→health ok=True；手册入库 docs/ops/ | 2026-10-01 |

## 6. 决策记录

- **vLLM 口径 = 切换就绪（脱敏代理 + 失败转移 + mock 验证 + 操作手册）**，真实 vLLM 服务待硬件——用户 2026-10-01 确认（选项：脱敏代理+切换验证 / 部署真实 vLLM / 跳过）。
- **容灾口径 = 备份恢复落地 + ADR-005 双活方案文档**，流复制等双活不实施——用户 2026-10-01 确认。
- **通知表放 biz 库**（V14，notify 模块加 datasource 指向同库）：避免改 PG init 脚本触发底座卷重置；notify 与 biz 共库不同表，边界以表名前缀与访问层隔离。**实施修订**：notify 不引 Flyway（迁移统一 biz 执行）；通知表按**用户扇出**落行（原计划 user_id 可空广播 + 独立已读表——扇出模型让已读/删除/清理天然按用户隔离，避免「A 已读把 B 的未读也清了」，用户量小扇出成本可忽略）。
- **E1 提案存储 = biz V15 新表**（cad_tune_param_proposals）；P2-2 parameter_schemes 随 T11.3 定案（倾向：迁移注释声明"预留，IR2 快照为权威"，表结构不匹配提案语义则以新表承载）。
- **通知落库类型口径（实施修订）**：仅 review_request / alert 落库；task_progress / log 高频过程类照旧仅广播，**ir_updated 也不落库**（每会话 5 次更新，落库会淹没通知中心；原 §2.3/§6 草案列为关键类的口径据此收窄）。
- **Word 导出先行 docx**，PDF 后置（可由 docx 转换）；内置程序化模板 + 覆挂点，非授权军标正式模板（A-16 如实标注）。

## 7. 演进路线（承诺项，非本期范围）

- v0.5+（生产环境落地时）：真实 vLLM 昇腾部署（按 T11.6 手册）、真实 GJB 全量录入与核对（按 T11.4 批量入口）、双活实施（按 ADR-005）、ELK 日志中心、成员级项目数据权限、P2-8/9/10/12、PDF 导出。

## 8. 修订记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v1.0 | 2026-10-01 | 初版编制（ZCode）。前置发现（探索实证）：①阶段 9/10 文档回填遗留——roadmap 阶段 9/10 总览行与 T10.5 行仍「待开发」、phase-9 T9.7 标题未勾、phase-10 附录 A 自检①与事实不符、HANDOFF「下一步入口」缺阶段 11 条目，随本 commit 一并修正；②E1 插入点唯一（finalize_version 尾部），关联键现成（IR3 linked_param_id ↔ IR2 parameter.id），失效基建现成但需新增定向语义；③IR2 回写须 value+derivation 同步（A4 符号表依赖 derivation）；④python-docx 已在依赖，Word 导出零新增依赖；⑤RBAC 五表齐全已播种，缺口 = auth 用户管理 API、authz.py 空角色放行双漏洞、KB 写端点宽松；⑥notify 零持久化无迁移目录，通知表放 biz 库最简；⑦军标批量入口缺，单条链路已齐；⑧备份全仓零实现，GraphML 快照承接 P2-3（knowledge-dumps 桶未建随此建）；⑨vLLM 切换机制已就绪（llm_providers 热加载），vLLM 本身即 OpenAI 兼容——注册 provider 即切换，缺脱敏代理与失败转移 |
| v1.1 | 2026-10-01 | T11.1 执行修订：①notify 不引 Flyway——迁移统一 biz V14 执行（与 biz 共用 flyway_schema_history，双 Flyway 必冲突）；②通知表按用户扇出落行（(user_id, client_key) 唯一幂等——同信封 HTTP 重试不重复，新事件新时间戳=新行）；③落库类型收窄为 review_request + alert（ir_updated 每会话 5 次不落库）；④alert 信封补进 contracts/ws 契约与前端类型（T10.1c 已广播但 schema 漏列——契约漂移修复）；⑤notify 查询 API 沿平台统一响应包 {code:0,message,data}；⑥Hikari 懒初始化 + 落库静默降级/查询 503（库不可达不阻塞进程启动）；⑦V14 已手动应用于 dev 库（biz 旧镜像跑栈场景；新镜像启动 Flyway 幂等无冲突） |
| v1.2 | 2026-10-01 | T11.3 执行修订：①复审闭环零 Java 改动——a4 重开即 update_stage("A4") 覆写闸门，STAGE_DONE 后 pauseAtGate 的 IN(running,paused) AND current_stage≠gate2 乐观条件恰好命中「paused+A4」，自动重挂 gate2 重发审阅请求（探索实证 §2.1 推论）；②derivation 同步用**原地替换**首个 ≈旧值 的 symbol=数值（solver setdefault 首匹配优先，追加行不生效）+ 尾部无"="修订记录（不入符号表）；③定向失效新增 py 侧 ir_store.mark_invalidated（与 Java IrSnapshotMapper 同语义，E1 只失效 IR4/IR5 不动 IR2/IR3）；④确认仅限 paused 会话（completed 无复审链路如实 409）；链式微调基线（前次定稿版 linked_param_id 全 None）与分段数变化如实产 0 条提案；⑤V15 已手动应用于 dev 库（同 v1.1⑦ 口径） |
