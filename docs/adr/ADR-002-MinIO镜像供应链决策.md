# ADR-002：MinIO 镜像供应链决策（官方镜像下架后的替代）

| 项 | 内容 |
|---|---|
| ADR 编号 | ADR-002 |
| 状态 | 已接受 |
| 日期 | 2026-09-17 |
| 关联 | D-10（MinIO 选型不变）；T0.2 |

## 背景

T0.2 实机部署时发现 `minio/minio` 官方 Docker 镜像已被 MinIO 官方于 2026-02 从 Docker Hub **全量下架**（社区版 2025-10 起转 source-only，只发源码不发构建产物；最后一个含修复的版本为 RELEASE.2025-10-15T17-29-55Z，修复 HIGH CVE-2025-62506）。各加速源对该仓库 403 / not found 属上游不存在，非网络问题。

临时方案 elestio/minio:latest（社区重建）为 RELEASE.2025-09-07T16-13-09Z，**不含 CVE-2025-62506 修复**，存在已知高危漏洞，不能作为长期方案。

## 决策

1. **D-10 选型不变**：文件存储仍是 MinIO（S3 协议），仅更换镜像来源，不构成架构变更。
2. **采用 Chainguard 官方二进制重构镜像**：`cgr.dev/chainguard/minio`，按 **digest 固定**：
   `sha256:83b4a2d9f21bbf5127ef8b8bbe456d371cd35c8d03ec72262866ceee889c9a88`
   - Chainguard 为正规商业厂商（wolfi 基底），当前镜像 0 已知 CVE、约 58MB；
   - 非 root（uid 65532）运行；内含 `mc` 客户端，健康检查用官方推荐的 `mc ready local`；
   - 已实测：命名卷 + 非 root 启动、建桶/写/读全回环通过。
3. **随上游移除 Web Console**：业务 MinIO 不再映射 9001 端口（`MINIO_CONSOLE_PORT` 保留占位）。
4. **版本升级方式**：cgr.dev 只保留 `latest`，digest 会被上游重建覆盖；升级 = 重新 `docker pull cgr.dev/chainguard/minio:latest` → 取新 digest → 更新 compose → 回归。如需更严格版本控制，备选方案见下。

## 备选方案（未采用，留档）

| 方案 | 结论 |
|---|---|
| elestio/minio | 含 CVE-2025-62506，仅作过渡 |
| alpine/minio | 加速源 403（daocloud 源级封锁），且单维护者 |
| 源码自建（git clone + docker build，GOPROXY 走 goproxy.cn） | 完全可控、可 pin RELEASE tag；维护成本高，作为 Chainguard 停止维护/合规不允许时的 fallback |
| 换 SeaweedFS / RustFS 等 | 属 D-10 架构变更，必须走 ADR 且重新设计分桶/SDK 适配，现阶段不做 |

## 后果

- 生产部署的 MinIO CVE 阻塞项解除；供应链依赖从 Docker Hub 转移到 cgr.dev（Chainguard free tier，无账号可用）。
- digest 固定保证可复现部署；升级需显式操作（不会自动漂移）。
- 若未来接入内网/信创环境，cgr.dev 不可达时，按备选方案"源码自建"在离线环境重建镜像。
