# HANDOFF · 双 AI 交接状态（唯一交接事实源）

> **用途**：WorkBuddy 与 ZCode 交替开发本项目，每次会话开始先读本文件，会话结束前必须更新本文件。
> **分工边界**：`docs/roadmap.md` 是唯一**进度**事实源（任务状态）；本文件只回答三件事——**现在干到哪、下一步从哪进、有什么坑**。两者冲突时以 roadmap 为准并立即修正本文件。
> **更新纪律**：谁结束会话谁更新（改动与 devlog 同 commit 或独立 `docs:` commit）；会话结束时工作区不得留未提交改动（宪法 §8.1）。

---

## 会话流程（两个 AI 都遵守）

1. **开始**：读宪法（`docs/00_开发约束与协作规范.md`）→ `docs/roadmap.md` → 本文件 → 从「下一步入口」继续，不开新摊子。
2. **结束前自查**：
   - [ ] roadmap 状态已回填，devlog 已记录（与代码同 commit）
   - [ ] 本文件「当前状态 / 下一步入口 / 待验证 / 环境注意」已更新
   - [ ] 工作区干净，已提醒用户 push
   - [ ] 有新踩坑 → 追加到「环境注意」（只记可复现的事实，不记流水账）

---

## 当前状态（最后更新：2026-10-01 · ZCode，v0.3.0 阶段 10 已收官 tag 待用户复核；当前在跑栈 = 容器化全栈 + 监控）

| 项 | 状态 |
|---|---|
| **阶段 0** | **全部完成** ✅（T0.1~T0.4，含实机验收：9/9 容器 healthy、Flyway V1 建表 12 张成功） |
| **v0.1.0** | **已发布** ✅（2026-09-20 用户确认，`git tag -a v0.1.0` + push；阶段 0–5 全部 + 验收期增补，范围见 tag message / PHASE-005 §4） |
| git | push 由 AI 自主执行（2026-09-17 用户授权），每任务一推 |
| 数据底座 | 运行中，接入端口：PG 5432 / Redis 6379 / RabbitMQ 5672,15672 / Neo4j **17474,17687** / 业务 MinIO **19000**（Console 已随上游移除）/ Milvus 19530 / Nginx 80（宿主机已有 openspg 容器占用 7474/7687/9000/9001，故覆盖端口，见 `.env`） |
| MinIO 供应链 | 已切换 Chainguard digest 固定镜像（0 CVE，见 ADR-002），真实凭据 S3 回环验证通过 |
| 阶段 1 | **全部 7 任务完成**（T1.1–T1.7），e2e 需求→STEP 落 MinIO 通过；**待人工验收**（PHASE-001 v2.1） |
| **阶段 2** | **全部 4 任务完成**（T2.1–T2.4），五步闭环 + 审阅关驳回流 e2e 全绿（16m36s，真实 DeepSeek）；**待人工验收**（PHASE-002 v1.1） |
| **阶段 3** | **全部 4 任务完成**（T3.1–T3.4），首样 e2e 全绿（12m12s）：上传→解析→分块 97→抽取 849 条→三层构建（Milvus 向量 97 / Neo4j 518 实体 / 动态语料）→检索命中 graph 级；**待人工验收**（PHASE-003 v1.1） |
| KB 语料 | **4 文档 / 439 chunks / 2939 条确认抽取**（首样 + 声呐技术 + 水声学原理 + 潜艇拖曳总体设计，导入脚本 `knowledge/import_corpus.py`）；检索验证查询全部命中 graph 级 |
| OCR 插件 | **已接**（D-14 落地）：PaddlePaddle/PaddleOCR-VL-1.5（SiliconFlow），`kb/ocr.py`；扫描页自动识别，单文档页数预算 KB_OCR_MAX_PAGES=20，真实 API 用例门控 KB_OCR_REALTEST=1 |
| 管线现状 | START → A1→A2→**gate1**→A3→A4→**gate2**→A5→completed；驳回=局部回算+下游失效（ir_snapshots.invalidated_at）；**失败会话可 RETRY**（failed→running，按 current_stage 重派，worker 任务开始即标记阶段）；知识关（gate=knowledge）人工确认后回写 Neo4j；`GET /api/v1/trace/{trace_id}` 反查。IR0–IR5 全量快照 API 齐 |
| Flyway | V1~V10 已应用（V9 多库：kb_bases + kb_base_documents 多对多归属 + design_sessions.kb_ids；V10 微调：cad_tune_sessions/_rounds/_tool_calls；biz 启动即自动迁移，无需手工步骤） |
| LLM 供应商 | 已接真实 siliconflow key；模型 **deepseek-ai/DeepSeek-V3.2**；流式适配器；阶段 2 e2e 两次全链真实跑通 |
| py-engine | 任务 sonarcad.a1~a5 + kb_build/kb_extract + echo/always_fail（队列 sonarcad.a1~a5/kb）；KB 治理 API `/api/v1/kb/**`（gateway 直转 8090，信任头校验）；检索三级：graph(Neo4j 实体层)→vector(Milvus kb_chunks)→text；`POST /knowledge/writeback`；neo4j/pymilvus 已入 requirements |
| 服务端口 | gateway 8080 / auth 8081 / workflow 8082 / biz 8083（启动需 source .env 提供 POSTGRES_PASSWORD 与 JWT_SECRET；gateway 另有 `/api/v1/trace/**` → workflow 路由） |
| 登录凭据 | admin / admin123（Flyway V2 播种，开发默认，上线必改）；另有验收用 **rbac_designer / admin123**（designer 角色，RBAC 抽查用，可删） |
| ⚠️ py-engine 重启纪律 | py-engine 代码改动须**同时重启 uvicorn API 与 celery worker**（worker 是独立进程跑 A1~A5，只重启 API 不生效——2026-09-26 检索修复时踩过：回归会话第一轮 A1 仍走旧代码） |

## 下一步入口（按序）

0. **v0.1.0 已收官**——后续为 v0.2 迭代：①~~OCC 转换 subprocess 化（GIL 饥饿，优先级最高）~~**已完成（2026-09-20，commit 见 devlog 补充 47）**：转换移入独立子进程 convert_worker.py，状态经 MinIO 标记传递；②服务全容器化 + 干净克隆 compose up 验收；③前端 bundle 分割；④系统管理日志/备份/用户管理三个「待接」；⑤graphrag 可选依赖。**阶段 7（几何特征真实化与数值校验，PHASE-007）已完成待人工验收**（2026-09-22 全任务收官：[ADR-003](adr/ADR-003-几何自校验路线-特征真实化优先.md) + [PHASE-007](phase/phase-7-几何特征真实化与数值校验.md) §5 验收表 + 截图 `docs/phase/assets/phase7/`——IR3 features 由 STEP 实测提取（多体 B-rep 零镶嵌 10s / 单体半径剖面）+ A4 数值复核 CHK-GEO-NUM（容差 2%）+ 审阅页「IR3 建模产物」卡；**实机验证**：会话 `ad769c7e` 自然产出 2 条 CHK-GEO-NUM error（LLM 画错的间距/缆长，改造前静默通过）；执行期增补 Color 越界归一化 d8d8e15）。启动：`scripts/start-all.cmd`（PS）/ `bash scripts/start-all.sh`（Git Bash）。导航（2026-09-20 两轮结构优化，devlog 补充 50/51）：工作台 = 仪表盘（项目筛选/?project=）+ **项目管理 /projects**（声呐卡片网格：状态分布/最近活动/编辑/删除空项目）；「新建会话」为原地抽屉（NewSessionDrawer，仪表盘与项目卡片共用，项目预选 + 知识库多选，不选=Agent 自主圈定），需求输入独立页已移除（/sessions/new 重定向 /）。**知识库多库（PHASE-006，2026-09-20 全任务收官）**：知识库页重构为左侧库列表（文档池入口）+ 库内管线；文档↔库多对多归属（kb_base_documents 唯一事实源），删库=解除归属文档转未归库（不参与检索）；检索三层按 doc 集合过滤（Milvus/Neo4j 结构不动）；会话总览「知识检索」卡片展示选定库/Agent 依据/命中明细；审阅参数「查看知识原文」（GET /kb/refs）；A5 回写批准时手选目标库。e2e=tests/e2e/test_multikb_e2e.py。**CAD 交互微调（PHASE-008，2026-09-24 全任务收官，[ADR-004](adr/ADR-004-CAD交互微调集成路线.md)）**：FreeCAD 插件 `clients/freecad-plugin/SonarCADLink`（零凭据，登录平台换 JWT；自研 CadAgent 迁移改造：cq 翻译层/质量门禁/面板复用，废弃本地 LLM 直连与会话落盘）+ py-engine `cad_agent` 模块（AgentLoop 服务端跑，ReAct 文本协议，工具执行经 `cad_tune_tool_calls` 表长轮询外包插件）——流程：A3 完成的设计会话 → 插件拉 STEP 导入 → 点选+自然语言微调 → 每轮收敛上传真实 STEP → geometry_inspect 实测 → IR3 追加新版本（GLB 按版本缓存自动失效）→ gate2 审最新版（ReviewRecord.ir_snapshot_id 天然冻结）；微调版本 features 解除参数联动（人工偏差交 gate2 裁决），脚本溯源=基线+微调段拼接。局限如实：STEP 导入无参数历史（参数化编辑列二期）、插件主线程执行无强制超时（语法预检+Stop+服务端 180s 工具超时兜底）。e2e=tests/e2e/test_cad_tune_e2e.py（自带 mock LLM 随机端口，44s）。**人工验收：插件真机 13 步清单见 clients/freecad-plugin/README.md**。
1. **用户**：阶段 1 + 阶段 2 + 阶段 3 人工验收（各自 §验收记录）——建议抽查：真实 IR4 风险定级；Neo4j 浏览器（localhost:17474）看沉淀/抽取实体；删除一个 KB 文档验证清理完整性。
2. **阶段 4（前端工作台）已完成，待人工验收**（§4.4 两组真实样例走查已由 ZCode 预跑通过，含知识写入与 3D 交付，记录见 PHASE-004/005 验收表；用户复核可抽其中任意环节）：`cd frontend && npm run dev`（5173，需全栈在跑），admin/admin123。建议走查：①仪表盘 WS 实时进度（开两个浏览器看同一会话）＋断网重连；②需求输入建会话→gate1 驳回（选 A2）→观察局部回算与下游失效→二次过闸→gate2 批准→知识关批准→三维页签看模型（首次转换数分钟属正常，按版本缓存）；③知识库五页签 CRUD（上传/抽取确认/三库启停/图谱邻域）；④系统管理页现状数字与 /api/v1/kb/status 一致性。已知优化项已全部清账（2026-09-18，commit 9228708）：图谱节点按 Neo4j label 着色、工作台总览含实时日志面板（worker log 流）；会话 id 已归一化无横线（旧外链带横线 id 仍可用，PG uuid 归一化）。视觉打磨（frontend-design skill）与 PHASE-005 自动项预执行均完成，见 PHASE-004/005 验收记录
   - graphrag 本机未安装（可选依赖）：检索图谱级已由自建 Neo4j 实体层承担；装 graphrag~=2.0 后 SDK 级自动优先（A-14）
   - **2026-09-19 用户首轮验收反馈已修**（devlog 补充 30）：失败会话「重新执行」按钮（RETRY 事件+worker 开始即标记阶段）；审阅页降级 IR1 溢出；裁决历史 JSON 批注→可读（ReviewAnnotation）；会话 id 短显+复制；gate1 空方案/降级横幅警示；A1 降级缺失项去噪。**系统性功能交互/页面交互/UI 设计审查已执行**（2026-09-19 ZCode，报告 `docs/reviews/2026-09-19-全站设计审查.md`：P0×3/P1×9/P2×16）→ **报告全部条目闭环**：Top10（补充 32/33）+ P2 打磨组 14 项（补充 34：菜单分组/gate 中文化/横幅去重/仪表盘知识卡/图谱图例中文/文档库拖拽区/三库编号单位/空态文案/交付物 IR 下载/登录内联错误/合理偏离记录）；P2-9 调参与 P2-6 v-status 两项按 A-16 裁剪不做（无数据不造假，已记录）。会话 API 现回 createdByName（join users）
3. 开发提示：**全栈一键启动：PowerShell/cmd 用 `.\scripts\start-all.cmd`（显式 Git Bash，勿直接敲 bash——PS 会命中已损坏的 WSL）；Git Bash 用 `bash scripts/start-all.sh`**（幂等：在跑的跳过；.env 注入/日志落根目录 logs-*.log；数据底座容器在跑则复用）；数据底座与 Java 四服务均可直接连（Java 启动前 `set -a; source .env; set +a`）；**e2e 四条**：`test_towed_array_e2e.py`（7 分钟）/ `test_phase2_pipeline_e2e.py`（17 分钟，门语义）/ `test_kb_pipeline_e2e.py`（12 分钟，KB 全流程）/ `test_cad_tune_e2e.py`（~1 分钟，微调协议闭环，自带 mock LLM 随机端口+独立 provider 行不依赖现网供应商）。
4. **设计-实现对照与优化路线（2026-09-30 新增）**：`docs/设计实现对照与优化路线.md`（GAP-001）——按桌面三份方案文档（已迁移至 `docs/reference/`，其中《设计方案》《系统实施方案》2026-09-30 补入，三份文末均已附纯增量增补）逐章对照实现现状，并维护 v0.2+ 优化补齐清单（P0：uvicorn 挂死根因/CSR 落地/军标首批条目；P1：限流熔断/容器化/监控/压测/性能重基线/L1 缓存；P2：E1 反向回写等 12 项）。**后续 v0.2/v0.3 迭代已按宪法 §7.1 编制阶段开发文档并登记 roadmap**：[PHASE-009](phase/phase-9-稳定性与核心承诺补强.md)（v0.2：T9.1 挂死根因 → T9.2 CSR/T9.3 军标并行 → T9.4 限流熔断 → T9.5 容器化 → T9.6 L1 缓存 → T9.7 收官 tag v0.2）与 [PHASE-010](phase/phase-10-生产就绪与M4验收.md)（v0.3：监控告警/50 并发压测/性能重基线 → M4 验收 tag v0.3）；阶段 11（v0.4）仅 roadmap 总览占位，任务分解待启动时编制。完成项随代码同 commit 回填状态。

5. **阶段 9（稳定性与核心承诺补强，PHASE-009）已于 2026-09-30 全任务收官，待人工验收**：T9.1 挂死（线程池耗尽根因机制复现 + 线程池扩容 + 长轮询 async 化 + /healthz 事件循环心跳 503 探针）、T9.2 CSR（IR4 可空 csr + ReportTab 数值卡，口径 tooltip）、T9.3 军标激活（V12 expr 列 + SymPy 安全求值 + 6 条 DEMO 演示条目【待真实 GJB 授权后经军标页签替换】+ 完整管理表单）、T9.4 限流/熔断/429 结构化降级 + 前端幂等 GET 指数退避、T9.5 三镜像容器化（`deploy/docker/*.Dockerfile` + compose `--profile app`，README 双轨）、T9.6 L1 三层缓存（缓存键含供应商标识——e2e 可重复性教训）。全量回归绿（单测 281 / 四条 e2e / Java 46 / vitest 38），证据见 [PHASE-009](phase/phase-9-稳定性与核心承诺补强.md) §5。**人工验收清单**：①前端走查 ReportTab CSR 卡与军标页签新表单；②「另目录干净克隆 → compose --profile app up」目标机器复走；③确认后打 tag v0.2。之后进入阶段 10（PHASE-010，监控/压测/性能重基线/M4 验收）。**2026-09-30 补记**：用户明示「我懒得测试了，直接开始执行下一阶段」——v0.2.0 人工验收豁免（见 PHASE-009 §5 补记），阶段 10 已启动。

## 待验证 / 待决事项

- [x] T0.2：容器实机启动，9/9 healthy（2026-09-17）
- [x] T0.4：Flyway V1 对 PG 17.11 实机执行，12 张表建成（2026-09-17，flyway 一次性容器验证）
- [x] MinIO 供应链：切换 Chainguard digest 固定镜像（0 CVE），真实凭据 S3 回环验证通过（2026-09-17，ADR-002）
- [ ] 注意：MinIO 镜像按 digest 固定，升级 = 重拉 cgr.dev/chainguard/minio:latest 取新 digest 更新 compose（流程见 ADR-002）
- [ ] ZCode 首次接入：确认沙箱/终端能力与「环境注意」清单是否同样适用，不适用处更新该清单
- [ ] **py-engine uvicorn 事件循环挂死根因排查**（2026-09-19 设计审查期间实发：连接建立无响应、堆积 50 连接，前端轮询无熔断持续累积；重启已恢复，根因未查，疑似同步调用阻塞 loop）——见 `docs/reviews/2026-09-19-全站设计审查.md` §0
- [ ] **`llm_providers` 表当前 0 行**（验收期被清）：系统靠 .env 兜底跑真实 LLM；~~e2e 前需重播 enabled mock 行~~ **已核实无需重播**（e2e 前置 gate 接受 .env 真实 key 兜底，2026-09-22 三跑 e2e 均走真实 DeepSeek 通过）；供应商页与实际配置脱节属审查报告 P1-6

## 环境注意（机器相关事实，只增不删；失效的标注"[已失效]"）

- **WorkBuddy 沙箱**：Bash 工具损坏，一律 PowerShell；`Write-Output` 回显会被吞 → 验证结果用 **Python 落盘再 Read**；`wsl.exe`/`reg.exe` 被沙箱安全策略拉黑；**无法拉起 Docker Desktop**、**无法 git push**（均由用户手动）。
- **PowerShell 5.1**：写文件默认带 BOM/GBK——提交信息一律 Python 写 UTF-8 无 BOM 文件后 `git commit --file`；验证真实文件内容用 Python 原始字节读。
- **Git**：`git add -A` 会卷入根目录临时文件——.gitignore 已挡 `/_*.py|txt|log`，但仍建议按任务范围指定路径 add；两文件并行 Edit 同一文件会互相覆盖。
- **Maven**：走 aliyun 镜像；`spring-statemachine-bom` 不在 Boot BOM 内，父 pom 显式 import 且 **version 写死**（property 插值在 BOM import 处失效）。
- **前端**：npm 慢（135 包 ≈ 9 分钟）；构建 `tsc -b && vite build` 已验证。
- **Python**：托管 venv 在 `C:\Users\Administrator\.workbuddy\binaries\python\envs\default`（pytest/fastapi/jsonschema 已装）。
- **Docker Desktop**：安装于 `D:\Download\Docker Desktop\`（非默认路径）；国内网络拉 Docker Hub 秒断（报 access denied 误导，实为网络不通）——已配 daocloud registry-mirrors 解决；镜像总量约 2~3 GB。
- **代理与 Docker**：用户常挂梯子，但系统代理对 Docker daemon 不自动生效，且客户端分流规则常把 docker.io/registry-1.docker.io 设为 DIRECT——"挂着梯子也连不上"属正常现象；Docker 拉取以 registry-mirrors 为准，不要试图依赖梯子。
- **Docker 镜像坑**：`minio/minio` 官方镜像已被 MinIO 从 Docker Hub 全量下架（2026-02，社区版转 source-only），各加速源 403/not found 属上游不存在；**现用 Chainguard digest 固定镜像（0 CVE，ADR-002）**，entrypoint 即 /usr/bin/minio、非 root(65532) 运行、卷属主由 minio-volume-init 服务初始化。Docker 引擎运行中时本沙箱可直接执行 docker 命令（含 compose/一次性容器）。
- **端口冲突**：本机常驻 `release-openspg-*` 容器占 7474/7687/9000/9001 → 本套 host 端口已覆盖（Neo4j 17474/17687、MinIO 19000/19001，见 `.env`）；busybox wget 的 healthcheck 必须用 `127.0.0.1` 不能用 `localhost`（IPv6 解析坑）。
- **git push**：ZCode 沙箱可直接 push（2026-09-17 用户授权 AI 自主 git 管理，含 push）。
- **e2e 与 mock LLM**：`tests/e2e/test_towed_array_e2e.py` 前置=全栈在跑 + llm_providers 有 enabled mock 行（conftest 会自动加载 .env）；mock 服务 `python tests/e2e/mock_llm_server.py`（8091）。**改 worker/agent 代码后必须重启 celery worker**（进程内不热载）。
- **py-engine 陷阱**：① `shared_task` 进程内 delay 必须先 `celery_app.set_current()/set_default()`，否则消息进默认 celery 队列；② DLQ 严禁放 task_queues（worker 会订阅死信成回环），用 worker_ready 信号声明；③ Celery v2 消息的 embed 字段含 null，Java 侧 `Map.of()` 会 NPE；④ 失败任务要进 DLQ 需显式 `Reject(requeue=False)`；⑤ pip 默认装进 Anaconda——统一 `python -m pip`（python=D:/Download/Python/Python312）。
- **ZCode 沙箱（2026-09-17 核对）**：Bash/Git/curl/Maven 均正常（与 WorkBuddy 清单不同，PowerShell 仅为 Windows 互操作用）；`java -version` 显示 21 但 Maven 用 JAVA_HOME=jdk-17，编译按 17 正确。新坑：
  - Git Bash 控制台发中文 JSON body 会按 GBK 编码 → curl 验收一律 ASCII 负载
  - 服务运行中 `mvn package` 会因 jar 被锁 rename 失败 → 先杀 `java -jar *SNAPSHOT.jar` 进程再打包
  - PG uuid 列 vs Java String 实体：JDBC URL 已加 `?stringtype=unspecified`（三模块 yml），勿删
  - JWT sub 必须是用户 UUID（owner_id/created_by 外键），username 走 claim
  - spring-statemachine 4.0 用阻塞 `sendEvent(E)`/`start()`，无 sendEventReactively
- **阶段 2 实机新坑（2026-09-18，验收九轮 e2e 累积，只增不删）**：
  - **JDK HttpClient 默认发 h2c Upgrade 头**（`Upgrade: h2c` + `HTTP2-Settings`），uvicorn/h11 会把这类请求的 body 丢弃（FastAPI 报 422 `loc:["body"] missing`，Spring RestClient 独立 builder 同样不写 body）→ **Java 调 py-engine 一律 `HttpClient.newBuilder().version(HTTP_1_1)`**（见 KnowledgeWritebackClient）
  - **JDBC/MyBatis 落 jsonb 列没问题，但 varchar 长度按枚举值最长者核对**：V1 把 review_records.gate 建成 varchar(8)，注释里声明的枚举值 "knowledge"（9 字符）插不进去，运行期才炸（V5 已扩 16）——新表枚举列长度务必数一遍字符
  - **gateway 路由按路径前缀匹配**，新增非 `/api/v1/workflow/**` 的 workflow 端点（如 `/api/v1/trace/**`）必须同步加 gateway 路由，否则落 biz 兜底报 "No static resource"
  - **改了编排语义要同步改 e2e**：管线接入 gate 后 `test_towed_array_e2e.py` 轮询遇 paused 自动批准（已适配）；`test_phase2_pipeline_e2e.py` 是阶段 2 门语义的标准样例（约 17 分钟，含驳回流）
  - 审阅请求体风格 camelCase（`reworkStage`），与既有端点一致；Python 侧快照读取自动过滤 `invalidated_at IS NOT NULL` 的版本
  - neo4j driver 6.x 超时参数是 `connection_timeout`（5.x 是 `connection_timeout_s`，storage/neo4j_store.py 已做兼容）
  - Bash 复合命令里 `&&` 与 `&` 优先级坑：`cmd1 && cmd2 & cmd3 &` 只让 cmd2 吃到环境，cmd3 在外——**多服务带 env 启动务必整段包进 `bash -c`**
- **阶段 3 实机新坑（2026-09-18，只增不删）**：
  - **改任何 py-engine 代码后 uvicorn 也必须重启**（不只 worker）——KB e2e 两轮被进程内旧代码坑（图谱 source_session 字段缺失、kb_levels 开关不生效）
  - **FastAPI 路由挂载前缀变更要同步所有调用方**（单测/e2e/gateway）：kb router 挂 /api/v1 后，单测与 e2e 里的 /kb/... 全部 404
  - **大文档 LLM 抽取必须异步化**：97 chunks 串行 ~10 分钟，HTTP 同步必超时——sonarcad.kb_extract 任务 + worker 内 ThreadPool 并行（KB_EXTRACT_WORKERS=4）
  - **跨进程内存态不可靠**：worker 构建刷新的动态语料，uvicorn 进程看不到——改为查询时按 TTL 从库懒加载（GraphRAGService._ensure_corpus）
  - **同名函数定义两次后者覆盖前者**（get_knowledge_service 踩过）：重构后 grep 确认唯一定义
  - **kb_extractions.graph_key 列宽**：关系组合键 src16->dst16=34 字符 > varchar(32)（V8 扩至 64）——新列按最长可能值定宽
  - Milvus pymilvus 3.x：`MilvusClient.create_schema` 是类方法，局部 import 后再调用（ensure_collection 踩过 NameError）
- **OCR 插件实机事实（2026-09-18）**：
  - PaddleOCR-VL-1.5 走 SiliconFlow chat 接口（OpenAI 兼容），图片 base64 data URL；**输出带 `<|LOC_x|>` 版面标记（每行四个角点）必须清洗**（kb/ocr.py strip_layout_markers）
  - 请求务必带 `temperature: 0`（否则同一图像识别结果不稳定）；单页 ~1250 vision tokens、~2s
  - 测试造"扫描件"：PIL 黑白模式（"1"）多页图像直接 save 成 PDF（RGB 模式的 PDF 保存需要 JPEG 编码器，本机 Pillow 没有）
- **py-engine uvicorn 挂死与重启（2026-09-19 实发）**：uvicorn 事件循环可能整体挂死（端口 LISTENING、连接 ESTABLISHED 但零响应，健康检查也挂）；**重启必须 `cd py-engine && bash -c 'set -a; source ../.env; set +a; python -m uvicorn api.main:app --host 127.0.0.1 --port 8090'`——漏 source .env 时 PG 报 `fe_sendauth: no password supplied` 全接口 500**；本机 curl 探测一律用 `127.0.0.1`（`localhost` 解析 IPv6 不可靠）

## 工作约定速记（全文见宪法 CONST-001）

- 文档驱动：无文档不开工；任务完成 = 代码 + roadmap 回填 + devlog **同一 commit**。
- 小步提交：Conventional Commits 中文描述；会话结束工作区必须干净；push 提醒用户。
- 架构硬约束 A-01~A-16（尤其：IR Schema 单一事实源在 `contracts/ir/`；Agent 不互调；LLM 不给公式参数数值；军标 0 条如实标"待补"）。
- UI 先 mockup 确认再写码；蓝色仅交互语义；中性生产力美学。
- 密钥只存 `.env`（已 gitignore）；提交/日志/文档中不得出现真实 Key。

6. **阶段 10（生产就绪与 M4 验收，PHASE-010）已于 2026-10-01 全任务收官**（v0.2.0 人工验收由用户明示豁免后直跑）：T10.1 监控告警（compose `--profile monitoring`：prometheus/alertmanager/grafana/postgres-exporter + RabbitMQ 原生 prometheus 插件；py 指标双通道 API /metrics + worker :9101；Java 五服务 actuator；4 条核心告警触发证据入 `docs/reviews/evidence/`；AM→notify 桥接 Bearer 信任）；T10.2 50 并发×30min 压测全过（错误率 0.015%/轮询 P95 7ms/5xx=0/返工闭环 83 次 0 失败；限流校准 WF 150/450、PY 100/300——并修复 T9.4 compose RL_* 漏绑定）；T10.3 性能重基线（真 LLM 4 轮中位 554.5s≈9.2min 达成 NFR ≤15min；检索 P95 1.3s；T10.4 未触发关账）；压测/基线途中返修五项：py 镜像缺 contracts/、broker 心跳被 solo 长任务阻塞（broker_heartbeat=0）、闸门重投卡死（幂等+自愈）、供应商切换失效（查询失败/确认空双语义）、presigned URL 容器化不可达（公开端点独立签名 client region 固定）。全量回归绿：py 296 单测 / 四条 e2e / Java 46+ / vitest 38。报告：`docs/reviews/压测报告_v0.3.md`、`docs/reviews/性能基线_v0.3.md`、`docs/reviews/2026-09-30-监控告警证据-T101.md`。**当前在跑栈 = 容器化全栈（--profile app）+ monitoring + loadtest**；切回宿主开发模式先 `cd deploy && docker compose --profile app --profile monitoring --profile loadtest down`（保留底座）再 `scripts/start-all.cmd`。
