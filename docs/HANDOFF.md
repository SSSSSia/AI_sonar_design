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

## 当前状态（最后更新：2026-09-17 15:15 · ZCode）

| 项 | 状态 |
|---|---|
| **阶段 0** | **全部完成** ✅（T0.1~T0.4，含实机验收：9/9 容器 healthy、Flyway V1 建表 12 张成功） |
| git | 多个 commit 待 push（remote git@github.com:SSSSSia/AI_sonar_design.git，**push 由用户手动执行**） |
| 数据底座 | 运行中，接入端口：PG 5432 / Redis 6379 / RabbitMQ 5672,15672 / Neo4j **17474,17687** / 业务 MinIO **19000**（Console 已随上游移除）/ Milvus 19530 / Nginx 80（宿主机已有 openspg 容器占用 7474/7687/9000/9001，故覆盖端口，见 `.env`） |
| MinIO 供应链 | 已切换 Chainguard digest 固定镜像（0 CVE，见 ADR-002），真实凭据 S3 回环验证通过 |
| 阶段 1 | **T1.1 + T1.2 + T1.6 + T1.3 完成**（实机验收通过）；设计文档 PHASE-001 完整版 |
| ⚠️ 待用户 | `.env` 的 `LLM_API_KEY` 为占位符（401 实锤）——填真实 key 后 LLM 规划自动恢复，无需改码 |
| py-engine | worker（Celery/RabbitMQ）+ 内部 FastAPI 8090 就绪；LLM 适配器 env 兜底（llm_providers 表 V3 已建，管理 API 随 T3.3） |
| 服务端口 | gateway 8080 / auth 8081 / workflow 8082 / biz 8083（jar 实跑验证过；启动需 source .env 提供 POSTGRES_PASSWORD 与 JWT_SECRET） |
| 登录凭据 | admin / admin123（Flyway V2 播种，开发默认，上线必改） |

## 下一步入口（按序）

1. **用户**：push 全部 commit（`git push`）。
2. **任一 AI 续做 T1.4 A2 方案生成**：IR1 → IR2（带出处参数表：source_type 四件套 + trace_id，A-06）；公式类参数必须由 SymPy 代码计算（A-07），求解底座落 `py-engine/solver/`；设计文档生成可迁移旧 prompts.py 的 CONSOLIDATION_DOC_PROMPT（★）。IR2 快照复用 `storage/ir_store.py`，任务模式复刻 `worker/tasks.py::a1_task`。
   - graphrag 本机未安装（可选依赖），当前检索自动落文本级；装 graphrag~=2.0 后图谱级自动启用（A-14）
3. 阶段 1 开发提示：数据底座与 Java 四服务均可直接连（连接参数见 `.env`；注意 Neo4j/MinIO 的 host 端口是覆盖后的非默认值；Java 服务启动前 `set -a; source .env; set +a`）。

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
- **py-engine 陷阱**：① `shared_task` 进程内 delay 必须先 `celery_app.set_current()/set_default()`，否则消息进默认 celery 队列；② DLQ 严禁放 task_queues（worker 会订阅死信成回环），用 worker_ready 信号声明；③ Celery v2 消息的 embed 字段含 null，Java 侧 `Map.of()` 会 NPE；④ 失败任务要进 DLQ 需显式 `Reject(requeue=False)`；⑤ pip 默认装进 Anaconda——统一 `python -m pip`（python=D:/Download/Python/Python312）。
- **ZCode 沙箱（2026-09-17 核对）**：Bash/Git/curl/Maven 均正常（与 WorkBuddy 清单不同，PowerShell 仅为 Windows 互操作用）；`java -version` 显示 21 但 Maven 用 JAVA_HOME=jdk-17，编译按 17 正确。新坑：
  - Git Bash 控制台发中文 JSON body 会按 GBK 编码 → curl 验收一律 ASCII 负载
  - 服务运行中 `mvn package` 会因 jar 被锁 rename 失败 → 先杀 `java -jar *SNAPSHOT.jar` 进程再打包
  - PG uuid 列 vs Java String 实体：JDBC URL 已加 `?stringtype=unspecified`（三模块 yml），勿删
  - JWT sub 必须是用户 UUID（owner_id/created_by 外键），username 走 claim
  - spring-statemachine 4.0 用阻塞 `sendEvent(E)`/`start()`，无 sendEventReactively

## 工作约定速记（全文见宪法 CONST-001）

- 文档驱动：无文档不开工；任务完成 = 代码 + roadmap 回填 + devlog **同一 commit**。
- 小步提交：Conventional Commits 中文描述；会话结束工作区必须干净；push 提醒用户。
- 架构硬约束 A-01~A-16（尤其：IR Schema 单一事实源在 `contracts/ir/`；Agent 不互调；LLM 不给公式参数数值；军标 0 条如实标"待补"）。
- UI 先 mockup 确认再写码；蓝色仅交互语义；中性生产力美学。
- 密钥只存 `.env`（已 gitignore）；提交/日志/文档中不得出现真实 Key。
