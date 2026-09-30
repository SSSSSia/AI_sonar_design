# 阶段 9 开发文档 · 稳定性与核心承诺补强（v0.2）

| 项 | 内容 |
|---|---|
| 文档编号 | PHASE-009 |
| 版本 | v1.0（2026-09-30，完整版） |
| 关联 | [GAP-001](../设计实现对照与优化路线.md) §3（P0-1/P0-2/P0-3/P1-1/P1-2/P1-6）、宪法 A-13（有界终止）/A-16（无数据不造假）/§7.3（UI 约束）、总体设计方案 §5.4（CSR）/§10.3–10.4（性能与容错）/附录 E、[PHASE-008](phase-8-CAD交互微调-FreeCAD集成.md)（前序）、roadmap 阶段 9 |
| 阶段目标 | 消除生产阻断级缺陷（uvicorn 事件循环挂死根因）、补齐设计文档两大核心承诺（CSR 约束满足率、军标插拔式核对激活）、落地生产化第一梯队（网关限流/熔断/429 降级、应用全容器化、L1 多层缓存），收官打 tag `v0.2` |
| 验收形态 | 单测 + e2e 断言（CSR/军标/缓存命中）+ 挂死复现脚本 + 干净克隆 `compose up` 清单 + 限流压测验证 |

---

## 1. 阶段定位与边界

### 1.1 为什么有这个阶段

[GAP-001](../设计实现对照与优化路线.md) 的差距分析结论：一期（M1–M3）已收官，但存在三类欠账——

1. **生产阻断级缺陷**：py-engine uvicorn 事件循环挂死（2026-09-19 实发、根因未查，HANDOFF 待决事项首位），不解决则后续压测（阶段 10）结果无效；
2. **设计文档核心承诺缺口**：CSR 是 M4 验收标准之一但未实现；军标接入框架落地但条目 0，「插拔式注入」的承诺未经数据验证；
3. **生产化基建为零**：网关无限流熔断（超载即雪崩）、应用无 Dockerfile（无法干净交付）、L1 多层缓存未做（检索/LLM 重复开销无谓）。

### 1.2 本期范围（做）

| 任务 | 对应 GAP-001 |
|---|---|
| T9.1 uvicorn 事件循环挂死根因排查与修复 | P0-1 |
| T9.2 CSR 约束满足率落地（A4 汇总 + IR4 契约 + 前端展示） | P0-2 |
| T9.3 军标库首批条目录入与符合性核对激活 | P0-3 |
| T9.4 网关限流/熔断/429 结构化降级 + 前端指数退避 | P1-1 |
| T9.5 应用容器化与干净克隆 compose up 验收 | P1-2 |
| T9.6 L1 多层缓存（LLM/检索/代码生成三层） | P1-6 |
| T9.7 收官：全量回归 + 文档回填 + v0.2 tag | — |

### 1.3 非目标（明确不做）

- 监控告警体系与 50 并发正式压测 → **阶段 10**（本期只交付挂死复现脚本与限流触发验证）；
- E1 CAD→文档反向回写、军标格式 Word/PDF 导出、细粒度 RBAC、通知持久化、vLLM 私有化切换 → **阶段 11 / v0.4**（依赖：硬件、军标资料授权）；
- 军标**规模化**录入（本期仅首批 5–10 条验证机制闭环）；
- ELK 日志中心（会话日志已落库，监控起步版走 Prometheus，见阶段 10）。

## 2. 设计决策与要点

### 2.1 uvicorn 挂死排查方案（T9.1 细则）

**已知事实**（HANDOFF「待验证/待决事项」+ `docs/reviews/2026-09-19-全站设计审查.md` §0）：2026-09-19 审查期间实发——连接建立但无响应、堆积约 50 连接；重启恢复；根因未查，疑似同步调用阻塞事件循环；前端轮询无熔断持续放大。

**排查假设清单**（按可能性排序）：

1. async 路由内混入同步阻塞调用（httpx 同步客户端 / psycopg2 同步驱动 / `subprocess.wait` / 重 CPU 序列化）——单个阻塞即卡死整个 loop；
2. `def`（非 `async def`）路由打进 Starlette 线程池（默认 40 线程），并发轮询把池耗尽后表现为「连接无响应」；
3. uvicorn 单 worker + keep-alive 配置不当，慢客户端占满连接。

**处置**：

- 先写**复现脚本**（`tests/load/hang_repro.py`：并发压 `/healthz` 与慢端点组合），能复现则定位修复、不能复现则如实记录并转防御性改造（A-16：不假装已解决）；
- 防御性改造（无论是否复现都做）：全量盘点 `py-engine/api/**` 与 worker 回调链的同步 IO → 换 `httpx.AsyncClient` / `asyncio.to_thread` / 线程池隔离；线程池容量与 uvicorn `--timeout-keep-alive` 配置化入 `app_config`；
- 为容器化（T9.5）预置健康探针：`/healthz` 增加「事件循环心跳」检查（`asyncio` 内看家任务超时即报 503），探针失败自动拉起。

### 2.2 CSR 口径与契约演进（T9.2 细则）

**口径（写死在代码注释与本文档，前端 tooltip 同文案）**：

```
CSR（约束满足率）= 通过的约束检查数 / (通过 + 违反) × 100
```

- 计数对象：A4 全部确定性检查逐条计——formula 等式复核、case 区间核对、standard 符合性核对、几何数值复核（CHK-GEO-NUM）；
- **违反 = error 级**；**warning（来源冲突存疑）与 info（提示）不计入分母**——存疑不等于违反，避免虚低；
- **无任何有效检查（全 skip / 空方案）时 `csr = null`**，前端显示「—」（A-16：无数据不造假，不显示 0% 或 100%）。

**契约加法演进**（参照 T7.1 / commit 9228708 先例）：`contracts/ir/ir4.schema.json` 增加可空字段 `csr: number | null`（0–100，两位小数）；旧快照无此字段 → 前端与契约测试双形态兼容。计算位置：`py-engine/agents/a4_validation/agent.py` 汇总段（checks 计数与 issues 生成同源，保证口径一致）。

**UI**：ReportTab 既有统计区增补一张 CSR 数值卡 + 口径 tooltip。属组件级微增、无新布局（宪法 §7.3 豁免 mockup；如需视觉确认可后补轻量预览）。

### 2.3 军标条目结构与判定求值（T9.3 细则）

**数据**（`kb_standards` 表 V7 已建，字段齐备）：首批录入 5–10 条，每条含——条目编号（如 GJB 23B-2018 §x.x）、约束类型（几何/声学/环境/可靠性）、适用对象（参数名匹配规则）、判定表达式（SymPy 可求值，如 `outer_diameter <= 27`）、优先级、启用状态。

**求值**（`py-engine/solver/standards.py`，现状 check=None）：对 `source_type=standard` 的参数，按适用对象匹配 → 表达式代入参数值（复用 `solver/recompute.py` 的 SymPy 代入与单位换算）→ 三值结果：

- 表达式成立 → pass（计入 CSR 通过）；
- 不成立 → error（计入 CSR 违反）+ `suggestion: back_to_A2`；
- 条目存在但适用对象在 IR2 中无对应参数 → info（如实提示未覆盖，不算违反）。

**数据来源纪律**：真实 GJB 条目文本需用户提供或确认来源；无授权文本时，首批可用**自拟演示条目**（明确标注 demo 性质、不冒充军标编号——A-16），待资料到位后替换。

### 2.4 网关限流熔断与前端退避（T9.4 细则）

**网关侧**（`java-platform/gateway`）：

- 限流：Spring Cloud Gateway `RequestRateLimiter`（Redis 令牌桶），key = JWT 用户（fallback IP）；`replenishRate` / `burstCapacity` 抽 `.env`；对 py-engine 算力路由（`/api/v1/kb|cad|cad-agent|logs/**`）与 workflow 路由分别设阈值；
- 熔断：resilience4j `CircuitBreaker` 过滤器罩 py-engine 路由（连续失败率触发，half-open 试探），熔断打开时返回结构化 503；
- 429/503 结构化错误体（新增全局约定）：`{"code":429,"message":"...","retryAfterSeconds":N}` + `Retry-After` 响应头。

**前端侧**（`frontend/src/services/`）：

- axios 响应拦截器：429/503 时——幂等 GET 自动指数退避重试（1s/2s/4s 上限 3 次 + jitter）；POST/审阅类**不自动重试**，toast 提示人工稍后再试（避免重复裁决）；
- 配合既有 react-query 缓存与 WS 对账轮询，补竞态防抖（AbortController 取消过期请求）。

### 2.5 容器化拓扑（T9.5 细则）

| 镜像 | 基础镜像 | 说明 |
|---|---|---|
| sonarcad-frontend | node:20（build）→ nginx:alpine | 多阶段；复用 `deploy/nginx` 配置；WS 代理 `/ws → notify` |
| sonarcad-java | eclipse-temurin:17-jre | 五服务共用一镜像，compose 以不同 `command` 启 gateway/auth/workflow/biz/notify |
| sonarcad-py | micromamba（conda-forge cadquery/OCP）+ pip 项目依赖 | 三进程共用：compose 以 command 区分 uvicorn(8090) / celery worker / convert_worker |

compose 扩展（`deploy/docker-compose.yml` 追加 app 服务组）：frontend / gateway / auth / workflow / biz / notify / py-api / py-worker / py-convert，全部 `depends_on` + healthcheck；`.env` 注入不变。**start-all 脚本保留为开发模式**（宿主机热更新），README 双轨说明。

**干净克隆验收清单**（DoD 依据）：新目录 `git clone` → `cp .env.example .env` 填密钥 → `docker compose up -d` → 全部 healthy → 浏览器登录（admin/admin123）→ 建会话跑通 A1 → 知识库页可见数据 → 三维页签可加载。

### 2.6 缓存键设计与失效纪律（T9.6 细则）

| 缓存键 | TTL | 失效时机 | 开关 |
|---|---|---|---|
| `cache:llm:call:{sha256(model+messages+temperature)}` | 7d | 供应商配置变更时清 | `LLM_CACHE_ENABLED`（默认开，调试可关） |
| `cache:kb:query:{sha256(query+kb_ids+level)}` | 24h | **KB 任何变更必清 `cache:kb:*`**（文档增删/抽取确认/索引重建/删库） | 常开 |
| `cache:codegen:{sha256(IR2 参数指纹)}` | 30d | 参数方案变更自然 miss | 常开 |

- 实现点：`py-engine/llm/adapter.py`（仅非流式调用走缓存，流式一期不走）、`py-engine/knowledge/service.py`（检索结果缓存）、A3 生成脚本文本缓存（沙箱执行与几何实测链路照跑，保证 IR3 真实性不受缓存影响）；
- 命中率埋点：命中/未命中计数 + 日志，并在 `/api/v1/kb/status` 暴露计数字段——为阶段 10 监控告警（缓存命中率 < 60% 告警）预留数据源；
- 修复循环内（A3 FIX_PROMPT 重试）**不查 codegen 缓存**，保证失败重试真实执行。

## 3. 任务分解（T9.1–T9.7）

> 执行顺序：T9.1 →（T9.2/T9.3 可并行，均独立）→ T9.4 → T9.5（依赖 T9.1 探针）→ T9.6 → T9.7 收官。

### T9.1 uvicorn 事件循环挂死根因排查与修复 ✅ 已完成（2026-09-30）

- **目标**：挂死不再发生，或有探针自动恢复 + 告警；根因结论留档。
- **要点**：§2.1 排查假设清单逐项过；复现脚本 `tests/load/hang_repro.py`；同步 IO 治理（AsyncClient/to_thread/线程池）；`/healthz` 事件循环心跳；uvicorn keep-alive 与线程池容量配置化。
- **根因结论**：全部路由为同步 `def` 跑 AnyIO 默认 40 线程池，并发长占线程端点（长轮询 25s/慢 IO）耗尽后旧版同步 healthz 也要排队——「连接建立但无响应」。机制经 synthetic A/B 复现证实（40 档旧 healthz max 6.15s 排队 vs 新版 0.00s；120 档均 ≤0.03s），详见 devlog 2026-09-30 补充 1（含同步 IO 静态盘点清单 7 项）。
- **DoD**：
  - [x] 复现脚本可重复执行（机制复现成立；无架构调整，未补 ADR）
  - [x] `py-engine/api/**` 无 async 路由内同步 IO（async 路由均 to_thread 包裹；盘点清单入 devlog）
  - [x] healthz 心跳探针 + 容器 restart 策略联动（restart 策略在 T9.5 compose 落地）
  - [x] e2e 回归全绿（cad_tune e2e 针对性回归 48.65s passed；四条全量按计划留 T9.7 统一跑）

### T9.2 CSR 约束满足率落地 ✅ 已完成（2026-09-30）

- **目标**：CSR 从文档概念变为端到端可见的量化指标。
- **要点**：§2.2 口径；A4 汇总段计算；IR4 schema 加法演进（可空 `csr`）；契约测试新旧双形态；ReportTab CSR 数值卡 + tooltip。
- **实现落点**：`agents/a4_validation/agent.py` run() 穿统计器（formula 复算/standard 核对/CHK-GEO-NUM 三类确定性检查计 pass/violation；结构完整性错误不计入，由 overall 体现）；`contracts/ir/ir4.schema.json` 可空 csr；ReportTab CSR 数值卡 + 口径 tooltip（null 显示「—」+ A-16 说明）。
- **DoD**：
  - [x] 单测断言 CSR 数值与手工核算一致（7 用例：全过 100 / 混合 50 / 1过2违 33.33 / warning 不稀释 / 几何数值计入 / 全 skip null / 空参数 null）；e2e（phase2）断言 IR4 必带 csr 字段且形态合法（0–100 或 null）
  - [x] 全 skip / 空方案场景 `csr=null`，前端显示「—」（A-16）
  - [x] 契约 jsonschema 正反用例更新（旧形态无 csr 仍合法 + 越界/类型错误非法）

### T9.3 军标库首批条目录入与核对激活 ⬜ 待开发

- **目标**：插拔式军标接入经真实数据验证机制闭环。
- **要点**：§2.3 条目结构与求值；registry 页签录入走查；e2e 含 standard 参数会话（通过 + 违反两分支）。
- **前置依赖**：军标条目文本来源（用户提供/确认；无授权文本时用自拟 demo 条目并标注）。
- **DoD**：
  - [ ] 首批 5–10 条入库（真实或 demo 标注如实）
  - [ ] 违反时 IR4 error + `back_to_A2` 建议；通过时计入 CSR
  - [ ] e2e 两分支断言 + 三库页签操作走查

### T9.4 网关限流/熔断/429 结构化降级 ⬜ 待开发

- **目标**：超载返回结构化降级而非雪崩；前端自动恢复瞬时抖动。
- **要点**：§2.4 网关过滤器与前端拦截器；阈值全部 `.env` 化。
- **DoD**：
  - [ ] 压测触发限流：429 JSON 体 + `Retry-After` 头实测
  - [ ] 前端 vitest：GET 退避重试 / POST 不自动重试两用例
  - [ ] 熔断打开/半开恢复实测（模拟 py-engine 不可用）

### T9.5 应用容器化与干净克隆验收 ⬜ 待开发

- **目标**：干净机器 clone 后 `compose up` 全链路可用（HANDOFF v0.2 入口②）。
- **要点**：§2.5 三镜像 + compose app 服务组 + 双轨启动文档。
- **DoD**：
  - [ ] 干净克隆验收清单全过（另找目录实测，记录入本档 §5）
  - [ ] 三镜像构建脚本/文档入库；py 镜像含 cadquery/OCP 可跑 A3 沙箱
  - [ ] README/部署文档双轨更新（compose 生产 / start-all 开发）

### T9.6 L1 多层缓存 ⬜ 待开发

- **目标**：重复请求命中缓存，检索/LLM 重复开销显著下降。
- **要点**：§2.6 三层键 + 失效纪律 + 命中率埋点。
- **DoD**：
  - [ ] e2e 二次运行命中断言（命中计数递增）
  - [ ] KB 变更后 `cache:kb:*` 清空实测
  - [ ] `/api/v1/kb/status` 暴露命中率计数；`LLM_CACHE_ENABLED` 可关

### T9.7 收官 ⬜ 待开发

- **要点**：四条 e2e + py 单测 + Java 测试全量回归；roadmap/GAP-001/HANDOFF 状态回填；按宪法附录 A 发布检查清单打 tag `v0.2`；devlog 补充记录。

## 4. 阶段管理

### 4.1 里程碑

| 节点 | 内容 |
|---|---|
| M9-1 | T9.1 完成（挂死闭环）——后续压测有效性的前提 |
| M9-2 | T9.2 + T9.3 完成（两大设计承诺补齐） |
| M9-3 | T9.4 + T9.5 + T9.6 完成（生产化第一梯队） |
| M9-4 | T9.7 收官，tag v0.2 |

### 4.2 依赖与顺序

- T9.5 依赖 T9.1 的探针设计（容器 restart 策略）；
- T9.3 的 e2e 依赖 T9.2 的 CSR 计数（standard 检查计入 CSR），建议 T9.2 先行或同 commit 联调；
- T9.6 的 codegen 缓存与 A3 修复循环有交互边界（§2.6 已定），实现时对照。

### 4.3 风险与对策

| 风险 | 对策 |
|---|---|
| 挂死无法复现 | 防御性改造照做 + 探针自动恢复，结论如实记录（A-16），不阻塞阶段 |
| 军标文本无授权来源 | 自拟 demo 条目明确标注，机制验证不受阻；资料到位后替换 |
| py 镜像体积/构建时长（OCP 约 2GB） | micromamba 缓存层 + 镜像仓库预拉；构建慢不阻塞开发模式 |
| 限流阈值难定 | 先保守值（对齐 50 并发目标反推），阶段 10 压测后回调 |
| LLM 缓存干扰调试 | `LLM_CACHE_ENABLED` 一键关 + 命中日志可追溯 |

### 4.4 验收流程（阶段末）

1. 全量回归绿（四条 e2e + 单测）；
2. 干净克隆清单复走一遍；
3. CSR / 军标 / 限流 / 缓存四项各出一条实测证据（截图或日志摘录）入 §5；
4. 用户人工验收后打 tag。

## 5. 验收记录

（待阶段末回填）

## 6. 决策记录

（执行中如出现路线争议——如 py 镜像方案、挂死根因涉及架构调整——按宪法 §1.2 补 ADR 后链接于此）

## 7. 演进路线（承诺项，非本期范围）

- 监控告警、50 并发压测、性能重基线 → [PHASE-010](phase-10-生产就绪与M4验收.md)；
- E1 反向回写、军标规模化、vLLM 私有化等 → 阶段 11（v0.4，见 GAP-001 §3 P2 / §4）。

## 8. 修订记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v1.0 | 2026-09-30 | 初版编制（ZCode），依据 GAP-001 差距分析 |
