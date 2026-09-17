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

## 当前状态（最后更新：2026-09-17 09:50 · WorkBuddy）

| 项 | 状态 |
|---|---|
| 阶段 0 | T0.1 ✅ / T0.3 ✅ / T0.2 🟡待验收 / T0.4 🟡待验收（代码全部完成） |
| git | 多个 commit 待 push（remote git@github.com:SSSSSia/AI_sonar_design.git，**push 由用户手动执行**） |
| 数据底座 | compose 语法已校验；**Docker 引擎已就绪（29.6.1），但镜像拉取被国内网络阻断**（Docker Hub 直连秒断，报 access denied 为误导性报错，见 devlog 2026-09-17 补充节） |
| 阶段 1 | 未开始 |

## 下一步入口（按序）

1. **用户**：给 Docker 配镜像加速（二选一）——
   - 方案 a（推荐）：Docker Desktop → Settings → Docker Engine，加 `"registry-mirrors": ["https://docker.m.daocloud.io", "https://docker.1ms.run"]` → Apply & Restart；
   - 方案 b：仓库根目录 `.env` 取消注释 `DOCKER_MIRROR=docker.m.daocloud.io/` 与 `QUAY_MIRROR=quay.m.daocloud.io/`（compose 已支持前缀变量）。
   然后 `docker compose -f deploy/docker-compose.yml --env-file .env up -d` → `ps` 确认全部 healthy → push 全部 commit。
2. **任一 AI**：T0.2/T0.4 验证通过后回填 roadmap「完成」+ devlog（一次 `docs:` commit）。
3. **阶段 1 开工**：T1.1 Java 平台层骨架（Gateway 路由 + JWT/RBAC 基础 + 会话 API + 状态机 pending→running→paused→completed/failed）。开工前先写轻量阶段设计 `docs/phase/phase-1-*.md` 并登记 roadmap（宪法 §7.1 文档先行）。

## 待验证 / 待决事项

- [ ] T0.2：容器实机启动 + healthcheck 全绿（命令见上，用户侧执行）
- [ ] T0.4：Flyway V1 建表（biz 服务连 PG 启动时自动执行，或 T1.1 联调时验证）
- [ ] ZCode 首次接入：确认沙箱/终端能力与「环境注意」清单是否同样适用，不适用处更新该清单

## 环境注意（机器相关事实，只增不删；失效的标注"[已失效]"）

- **WorkBuddy 沙箱**：Bash 工具损坏，一律 PowerShell；`Write-Output` 回显会被吞 → 验证结果用 **Python 落盘再 Read**；`wsl.exe`/`reg.exe` 被沙箱安全策略拉黑；**无法拉起 Docker Desktop**、**无法 git push**（均由用户手动）。
- **PowerShell 5.1**：写文件默认带 BOM/GBK——提交信息一律 Python 写 UTF-8 无 BOM 文件后 `git commit --file`；验证真实文件内容用 Python 原始字节读。
- **Git**：`git add -A` 会卷入根目录临时文件——.gitignore 已挡 `/_*.py|txt|log`，但仍建议按任务范围指定路径 add；两文件并行 Edit 同一文件会互相覆盖。
- **Maven**：走 aliyun 镜像；`spring-statemachine-bom` 不在 Boot BOM 内，父 pom 显式 import 且 **version 写死**（property 插值在 BOM import 处失效）。
- **前端**：npm 慢（135 包 ≈ 9 分钟）；构建 `tsc -b && vite build` 已验证。
- **Python**：托管 venv 在 `C:\Users\Administrator\.workbuddy\binaries\python\envs\default`（pytest/fastapi/jsonschema 已装）。
- **Docker Desktop**：安装于 `D:\Download\Docker Desktop\`（非默认路径）；国内网络拉 Docker Hub 秒断（报 access denied 误导，实为网络不通）——compose 镜像已支持 `DOCKER_MIRROR`/`QUAY_MIRROR` 前缀变量（.env 控制），或配 daemon.json `registry-mirrors`；镜像总量约 2~3 GB。

## 工作约定速记（全文见宪法 CONST-001）

- 文档驱动：无文档不开工；任务完成 = 代码 + roadmap 回填 + devlog **同一 commit**。
- 小步提交：Conventional Commits 中文描述；会话结束工作区必须干净；push 提醒用户。
- 架构硬约束 A-01~A-16（尤其：IR Schema 单一事实源在 `contracts/ir/`；Agent 不互调；LLM 不给公式参数数值；军标 0 条如实标"待补"）。
- UI 先 mockup 确认再写码；蓝色仅交互语义；中性生产力美学。
- 密钥只存 `.env`（已 gitignore）；提交/日志/文档中不得出现真实 Key。
