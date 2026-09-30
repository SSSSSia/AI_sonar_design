# 阶段 10 开发文档 · 生产就绪与 M4 验收（v0.3）

| 项 | 内容 |
|---|---|
| 文档编号 | PHASE-010 |
| 版本 | v1.0（2026-09-30，完整版；部分细则依赖 PHASE-009 产出，启动时可修订） |
| 关联 | [GAP-001](../设计实现对照与优化路线.md) §3（P1-3/P1-4/P1-5 及条件触发的 L4 查询优化）、总体设计方案 §10.5（监控告警与日志）/§11.2（M4 验收标准）/附录 E、[PHASE-009](phase-9-稳定性与核心承诺补强.md)（前置）、roadmap 阶段 10 |
| 阶段目标 | 建立监控告警起步体系，完成 50 并发压测与端到端性能重基线，达成总体设计 M4 验收标准（压测通过、CSR 达标、无环形震荡），收官打 tag `v0.3` |
| 验收形态 | Grafana 面板 + 告警规则触发证据 + 压测报告 + 性能基线报告（均入库 docs/） |

---

## 1. 阶段定位与边界

### 1.1 为什么有这个阶段

总体设计 §11.2 的 **M4「生产就绪」** 是本阶段验收锚点：50 并发压测通过、CSR 达标、无环形震荡。前置条件由 PHASE-009 提供——挂死根因闭环（否则压测无效）、限流降级（否则压测即雪崩）、CSR 落地（否则无达标口径）、容器化（否则压测环境不可复现）。本阶段在此基础上补齐观测性（监控告警）并产出性能基线，为阶段 11 生产上线提供决策依据。

### 1.2 本期范围（做）

| 任务 | 对应 GAP-001 |
|---|---|
| T10.1 监控告警起步（Prometheus + Grafana + 4 条核心告警） | P1-3 |
| T10.2 50 并发压测（M4 验收标准） | P1-4 |
| T10.3 端到端性能重基线（阶段耗时埋点 + NFR 对照报告） | P1-5 |
| T10.4 检索查询优化（条件触发：基线显示检索 > 4 分钟才做） | L4（GAP-001 §2.5） |
| T10.5 M4 验收收官 + v0.3 tag | — |

### 1.3 非目标（明确不做）

- ELK 日志中心（会话日志已落库回看；集中日志列阶段 11 视需要）；
- 双活部署、PG 流复制、Neo4j GraphML 定期快照（P0 级容灾，属生产部署阶段 11 范围，本阶段仅预留）；
- 前端 bundle 分割、AdminPage 三待接等 P2 项（阶段 11 / 日常迭代）。

## 2. 设计要点

### 2.1 监控告警（T10.1 细则）

**拓扑**：compose 追加 `prometheus` + `grafana` 两服务（配置入库 `deploy/prometheus/`、`deploy/grafana/`），与 PHASE-009 的 app 服务组同网；抓取间隔 15s。

**指标源**：

| 来源 | 指标 |
|---|---|
| Java 五服务 | Spring Boot actuator + micrometer（HTTP 计数/延迟、JVM、线程池） |
| py-engine | `prometheus_client`：HTTP 计数与延迟直方图、Celery 任务时长/失败率、**缓存命中率（PHASE-009 T9.6 埋点直供）** |
| 中间件 | rabbitmq_exporter（队列长度）、postgres_exporter（连接/慢查询）、redis_exporter（可选） |

**首批 4 条告警规则**（对齐总体设计 §10.5）：

1. Agent 任务软超时触发率（> 15 分钟任务占比）；
2. CadQuery 执行失败率 > 10%；
3. 检索延迟 P95 > 30s；
4. Celery 队列长度 > 100。

**DoD 依据**：每条告警各制造一次触发条件（如压测灌队列、临时调低阈值）留证据；Grafana 面板 JSON 入库可重放。

### 2.2 50 并发压测（T10.2 细则）

- **工具**：k6（或 locust，启动时定夺），场景脚本入库 `tests/load/`；
- **场景混合**（模拟设计院负载）：登录 → 建会话（按比例）→ 轮询任务进度 → 审阅裁决操作 → 知识库页浏览；
- **LLM 模式**：支持 mock LLM（复用 e2e 的 mock_llm_server）与真实供应商两种模式——压测关注平台吞吐与稳定性，默认 mock 避免真实 LLM 配额/限流干扰；真实模式作小样本对照；
- **通过口径（M4）**：50 并发持续 ≥ 30 分钟——无挂死（验证 T9.1）、无未捕获 5xx、错误率 < 1%、429 降级按预期工作、P95 轮询延迟 < 3s；
- **产出**：压测报告（含曲线截图）入 `docs/reviews/` 并在 §5 登记；限流阈值（PHASE-009 保守值）按结果回调一版。

### 2.3 性能重基线（T10.3 细则）

- **埋点**：worker 各 Agent 阶段（A1–A5）与关键子步（检索、LLM 调用、脚本生成、沙箱执行、几何实测）耗时结构化记录——复用 session_logs 管道（V11）追加 `stage_duration` 类型行，同时打点进 Prometheus（与 T10.1 共用）；
- **基线方法**：同一场景跑 3 轮取中位数；场景 = `test_towed_array_e2e.py` 标准样例（真实 LLM）+ 一轮 mock 对照；
- **NFR 对照**（总体设计 §2.3）：端到端 ≤ 15 分钟、检索阶段 ≤ 4 分钟——逐项给「达成 / 差距 X 分钟」，差距项给出归因（LLM 延迟 / 检索层数 / 队列等待）；
- **产出**：`docs/reviews/性能基线_v0.3.md`；旧 demo 数字（597s 三阶段）自此作废并在 GAP-001 §5 偏差表注明已替换。

### 2.4 检索查询优化（T10.4，条件触发）

仅当 T10.3 基线显示**检索阶段 > 4 分钟**时启动，否则在本档记录「未触发」即关账。候选措施（按性价比序）：A1 子查询去重与合并（8 → 3 的同类项归并）、Neo4j 批量查询（一次往返取多子图）、Milvus top-k 调参、必要时图谱分区预热。任何结构性改动补 ADR。

## 3. 任务分解（T10.1–T10.5）

> 执行顺序：T10.1 与 T10.3 埋点可先行 → T10.2（前置：PHASE-009 全部完成）→ T10.4（视基线）→ T10.5 收官。

### T10.1 监控告警起步 ✅ 已完成（2026-09-30）

- **DoD**：
  - [x] compose 起 prometheus/grafana，五 Java 服务 + py-engine + RabbitMQ/PG 指标可查（monitoring profile：prometheus/alertmanager/grafana/postgres-exporter；10 抓取目标全 up，含 py-api /metrics、py-worker :9101、rabbitmq 原生 prometheus 插件 :15692）
  - [x] 4 条告警规则各一次触发证据（docs/reviews/evidence/t101-*.json：四条同时 firing + AM active + 队列深度 211；①②③ 按任务书 §2.1 允许以临时调低阈值触发，④ 走真实阈值 >100；恢复后全部回落 inactive）；通知渠道打通 = Prometheus 规则 → Alertmanager → notify /internal/alerts 桥接（AM 0.27 webhook 无法注入自定义头，实测改 Bearer 静态信任 token，notify 侧 200 accepted；决策记录 §6）
  - [x] 面板 JSON 与规则文件入库，干净环境可重放（deploy/prometheus/、deploy/alertmanager/、deploy/grafana/：两块预置面板 provisioning 自动装载）

### T10.2 50 并发压测 ⬜ 待开发

- **DoD**：
  - [ ] k6 场景脚本入库（mock + 真实两种 LLM 模式开关）
  - [ ] 50 并发 × 30 分钟通过口径全项达标，报告入 docs/reviews/
  - [ ] 限流阈值回调记录（PHASE-009 保守值 → 压测校准值）

### T10.3 端到端性能重基线 ⬜ 待开发

- **DoD**：
  - [ ] stage_duration 埋点落 session_logs + Prometheus 双通道
  - [ ] 3 轮中位数基线报告（NFR 逐项对照 + 差距归因）入库
  - [ ] GAP-001 §5 偏差表「性能数据为旧 demo 基线」条目更新为新基线引用

### T10.4 检索查询优化（条件触发） ⬜ 待开发

- **DoD**：
  - [ ] 触发判定记录（基线数字）；未触发则本任务关账为「未触发」
  - [ ] 触发时：优化后检索阶段复测达标（≤ 4 分钟或给出 residual 归因）；结构性改动补 ADR

### T10.5 M4 验收收官 ⬜ 待开发

- **要点**：对照总体设计 §11.2 逐项核（50 并发压测 ✅ / CSR 达标 ✅（PHASE-009 落地，本阶段抽验）/ 无环形震荡 ✅（压测场景含参数联动操作））；文档回填；宪法附录 A 检查清单；tag `v0.3`。

## 4. 阶段管理

### 4.1 风险与对策

| 风险 | 对策 |
|---|---|
| 压测暴露新缺陷（挂死复发/队列堆积） | 回 PHASE-009 对应任务返修，不降低通过口径 |
| 真实 LLM 配额/限流干扰 | 压测默认 mock LLM；真实模式仅小样本对照并如实标注 |
| 性能基线差距过大（检索 >> NFR） | 触发 T10.4；仍不达标的差距如实入报告与 GAP-001，不造假达标（A-16） |
| 监控栈资源占用 | Prometheus 单机起步、保留期 15 天，不做高可用 |

### 4.2 验收流程（阶段末）

全量回归绿 → 压测报告 + 基线报告 + 告警证据三件套 → M4 三标准逐项核验记录 → 用户人工验收 → tag v0.3。

## 5. 验收记录

| 任务 | 实测证据 | 时间 |
|---|---|---|
| T10.1a py 指标 | 单测 12 用例 + 全量 293 passed；宿主实测 API :8090/metrics 200、worker :9101 全指标族；容器栈 10 抓取目标全 up | 2026-09-30 |
| T10.1b Java actuator | mvn test BUILD SUCCESS（notify 9 用例含桥接 2 例）；五服务 /actuator/prometheus 全 200 | 2026-09-30 |
| T10.1c 监控栈 | 4 条告警同时 firing 证据（docs/reviews/evidence/t101-*.json）；AM→notify 桥接 200 + 零投递错误；恢复正式规则后全部回落 inactive；echo 队列排空归零 | 2026-09-30 |

（阶段末随 T10.2/T10.3 增补压测与基线证据）

## 6. 决策记录

- **压测工具 = k6**（docker 镜像 grafana/k6:0.53.0，免宿主安装；任务书默认项）。
- **压测目标栈 = 容器化 app 栈**（§1.1 可复现性依据）；mock LLM 以 compose `--profile loadtest` 服务（sonarcad-py 镜像 + 挂载 mock_llm_server.py，MOCK_LLM_HOST=0.0.0.0）入网，provider 表注册 priority=1000 热切换。
- **告警通知链 = Prometheus 规则 + Alertmanager → notify 桥接**，非任务书字面的「Grafana alert」：AM 的 webhook http_config 可携带信任凭证而 Grafana 11 OSS 联系点不能稳定注入；AM 0.27 不支持自定义 http_headers（实测 config.plain 拒绝），改 `authorization: Bearer` 静态 token（开发默认 `sonarcad-alert-bridge`，上线必改），notify `InternalAlertController` 接受 X-User-Id 或该 Bearer 双通道。
- **告警触发证据口径**：④ 队列积压走真实阈值（worker 停止 + 150 条 echo 灌队列，深度 211 > 100）；①②③ 以「临时调低阈值」触发（任务书 §2.1 明示允许），触发后恢复正式阈值并确认回落 inactive。

## 7. 演进路线（承诺项，非本期范围）

- 阶段 11 / v0.4（业务泛化与生产上线，M5）：E1 CAD→文档反向回写、军标规模化录入、军标格式 Word/PDF 导出、细粒度 RBAC、通知持久化、vLLM 私有化部署切换（脱敏代理随此落地）、双活与容灾备份——任务分解待启动时编制，参照 [GAP-001](../设计实现对照与优化路线.md) §3 P2 与 §4。

## 8. 修订记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v1.0 | 2026-09-30 | 初版编制（ZCode），依据 GAP-001 优化路线；压测工具等细则启动时可修订 |
