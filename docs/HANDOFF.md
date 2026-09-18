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

## 当前状态（最后更新：2026-09-18 10:20 · ZCode）

| 项 | 状态 |
|---|---|
| **阶段 0** | **全部完成** ✅（T0.1~T0.4，含实机验收：9/9 容器 healthy、Flyway V1 建表 12 张成功） |
| git | push 由 AI 自主执行（2026-09-17 用户授权），每任务一推 |
| 数据底座 | 运行中，接入端口：PG 5432 / Redis 6379 / RabbitMQ 5672,15672 / Neo4j **17474,17687** / 业务 MinIO **19000**（Console 已随上游移除）/ Milvus 19530 / Nginx 80（宿主机已有 openspg 容器占用 7474/7687/9000/9001，故覆盖端口，见 `.env`） |
| MinIO 供应链 | 已切换 Chainguard digest 固定镜像（0 CVE，见 ADR-002），真实凭据 S3 回环验证通过 |
| 阶段 1 | **全部 7 任务完成**（T1.1–T1.7），e2e 需求→STEP 落 MinIO 通过；**待人工验收**（PHASE-001 v2.1） |
| **阶段 2** | **全部 4 任务完成**（T2.1–T2.4），五步闭环 + 审阅关驳回流 e2e 全绿（16m36s，真实 DeepSeek）；**待人工验收**（PHASE-002 v1.1） |
| **阶段 3** | **全部 4 任务完成**（T3.1–T3.4），首样 e2e 全绿（12m12s）：上传→解析→分块 97→抽取 849 条→三层构建（Milvus 向量 97 / Neo4j 518 实体 / 动态语料）→检索命中 graph 级；**待人工验收**（PHASE-003 v1.1） |
| KB 语料 | **4 文档 / 439 chunks / 2939 条确认抽取**（首样 + 声呐技术 + 水声学原理 + 潜艇拖曳总体设计，导入脚本 `knowledge/import_corpus.py`）；检索验证查询全部命中 graph 级 |
| OCR 插件 | **已接**（D-14 落地）：PaddlePaddle/PaddleOCR-VL-1.5（SiliconFlow），`kb/ocr.py`；扫描页自动识别，单文档页数预算 KB_OCR_MAX_PAGES=20，真实 API 用例门控 KB_OCR_REALTEST=1 |
| 管线现状 | START → A1→A2→**gate1**→A3→A4→**gate2**→A5→completed；驳回=局部回算+下游失效（ir_snapshots.invalidated_at）；知识关（gate=knowledge）人工确认后回写 Neo4j；`GET /api/v1/trace/{trace_id}` 反查。IR0–IR5 全量快照 API 齐 |
| Flyway | V1~V8 已应用（V4 快照失效 / V5 gate 列宽 / V6 KB 管线三表 / V7 知识资产三库 / V8 graph_key 列宽） |
| LLM 供应商 | 已接真实 siliconflow key；模型 **deepseek-ai/DeepSeek-V3.2**；流式适配器；阶段 2 e2e 两次全链真实跑通 |
| py-engine | 任务 sonarcad.a1~a5 + kb_build/kb_extract + echo/always_fail（队列 sonarcad.a1~a5/kb）；KB 治理 API `/api/v1/kb/**`（gateway 直转 8090，信任头校验）；检索三级：graph(Neo4j 实体层)→vector(Milvus kb_chunks)→text；`POST /knowledge/writeback`；neo4j/pymilvus 已入 requirements |
| 服务端口 | gateway 8080 / auth 8081 / workflow 8082 / biz 8083（启动需 source .env 提供 POSTGRES_PASSWORD 与 JWT_SECRET；gateway 另有 `/api/v1/trace/**` → workflow 路由） |
| 登录凭据 | admin / admin123（Flyway V2 播种，开发默认，上线必改）；另有验收用 **rbac_designer / admin123**（designer 角色，RBAC 抽查用，可删） |

## 下一步入口（按序）

1. **用户**：阶段 1 + 阶段 2 + 阶段 3 人工验收（各自 §验收记录）——建议抽查：真实 IR4 风险定级；Neo4j 浏览器（localhost:17474）看沉淀/抽取实体；删除一个 KB 文档验证清理完整性。
2. **任一 AI 开工阶段 4（前端）**：**每页先出 mockup 确认再写码（宪法 §7.3）**——页面清单见 roadmap T4.1~T4.4（仪表盘/审阅界面/参数表溯源/三维预览/知识库管理页）。后端 API 已齐（工作流/审阅/KB 治理/图谱/溯源）。
   - graphrag 本机未安装（可选依赖）：检索图谱级已由自建 Neo4j 实体层承担；装 graphrag~=2.0 后 SDK 级自动优先（A-14）
3. 开发提示：数据底座与 Java 四服务均可直接连（Java 启动前 `set -a; source .env; set +a`）；**e2e 三条**：`test_towed_array_e2e.py`（7 分钟）/ `test_phase2_pipeline_e2e.py`（17 分钟，门语义）/ `test_kb_pipeline_e2e.py`（12 分钟，KB 全流程）。

## 待验证 / 待决事项

- [x] T0.2：容器实机启动，9/9 healthy（2026-09-17）
- [x] T0.4：Flyway V1 对 PG 17.11 实机执行，12 张表建成（2026-09-17，flyway 一次性容器验证）
- [x] MinIO 供应链：切换 Chainguard digest 固定镜像（0 CVE），真实凭据 S3 回环验证通过（2026-09-17，ADR-002）
- [ ] 注意：MinIO 镜像按 digest 固定，升级 = 重拉 cgr.dev/chainguard/minio:latest 取新 digest 更新 compose（流程见 ADR-002）
- [ ] ZCode 首次接入：确认沙箱/终端能力与「环境注意」清单是否同样适用，不适用处更新该清单

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

## 工作约定速记（全文见宪法 CONST-001）

- 文档驱动：无文档不开工；任务完成 = 代码 + roadmap 回填 + devlog **同一 commit**。
- 小步提交：Conventional Commits 中文描述；会话结束工作区必须干净；push 提醒用户。
- 架构硬约束 A-01~A-16（尤其：IR Schema 单一事实源在 `contracts/ir/`；Agent 不互调；LLM 不给公式参数数值；军标 0 条如实标"待补"）。
- UI 先 mockup 确认再写码；蓝色仅交互语义；中性生产力美学。
- 密钥只存 `.env`（已 gitignore）；提交/日志/文档中不得出现真实 Key。
