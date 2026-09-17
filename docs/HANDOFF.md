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

## 当前状态（最后更新：2026-09-17 11:15 · WorkBuddy）

| 项 | 状态 |
|---|---|
| **阶段 0** | **全部完成** ✅（T0.1~T0.4，含实机验收：9/9 容器 healthy、Flyway V1 建表 12 张成功） |
| git | 多个 commit 待 push（remote git@github.com:SSSSSia/AI_sonar_design.git，**push 由用户手动执行**） |
| 数据底座 | 运行中，接入端口：PG 5432 / Redis 6379 / RabbitMQ 5672,15672 / Neo4j **17474,17687** / 业务 MinIO **19000**（Console 已随上游移除）/ Milvus 19530 / Nginx 80（宿主机已有 openspg 容器占用 7474/7687/9000/9001，故覆盖端口，见 `.env`） |
| MinIO 供应链 | 已切换 Chainguard digest 固定镜像（0 CVE，见 ADR-002），真实凭据 S3 回环验证通过 |
| 阶段 1 | 未开始，**下一步从这里进** |

## 下一步入口（按序）

1. **用户**：push 全部 commit（`git push`）。
2. **任一 AI 开工阶段 1**：T1.1 Java 平台层骨架（Gateway 路由 + JWT/RBAC 基础 + 会话 API + 状态机 pending→running→paused→completed/failed）。开工前先写轻量阶段设计 `docs/phase/phase-1-*.md` 并登记 roadmap（宪法 §7.1 文档先行）。
3. 阶段 1 开发提示：数据底座已就绪可直接连（连接参数见 `.env`；注意 Neo4j/MinIO 的 host 端口是覆盖后的非默认值）。

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

## 工作约定速记（全文见宪法 CONST-001）

- 文档驱动：无文档不开工；任务完成 = 代码 + roadmap 回填 + devlog **同一 commit**。
- 小步提交：Conventional Commits 中文描述；会话结束工作区必须干净；push 提醒用户。
- 架构硬约束 A-01~A-16（尤其：IR Schema 单一事实源在 `contracts/ir/`；Agent 不互调；LLM 不给公式参数数值；军标 0 条如实标"待补"）。
- UI 先 mockup 确认再写码；蓝色仅交互语义；中性生产力美学。
- 密钥只存 `.env`（已 gitignore）；提交/日志/文档中不得出现真实 Key。
