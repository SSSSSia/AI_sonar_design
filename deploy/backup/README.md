# 容灾备份（T11.7，P2-7）

> 脚本化备份/恢复，GAP-001 P2-7 口径「compose 备份脚本 + GraphML 快照」。
> 演进与双活路线（PG 流复制 / Neo4j causal cluster / MinIO 站点复制）见 [ADR-005](../docs/adr/ADR-005-容灾与双活路线.md)。

## 对象与口径

| 数据 | 备份方式 | 一致性 | RPO（定时每日 02:00 时） |
|---|---|---|---|
| PG（单库 sonarcad，全部服务表） | `pg_dump -Fc`（权威） | 一致性快照 | ≤24h |
| MinIO（5 桶对象） | py-engine 容器内 SDK 全量下载打包 | 最终一致（运行中快照） | ≤24h |
| Redis | BGSAVE 后拷贝 dump.rdb | 近似 | ≤24h（缓存可重建，重要性低） |
| RabbitMQ | definitions 导出（拓扑） | — | —（消息不做持久备份） |
| Neo4j 图谱 | **在线 GraphML 快照**（py-engine `/kb/graph/snapshot` → knowledge-dumps 桶） | 写入端幂等，快照级 | ≤24h |

如实说明（A-16）：Neo4j 采用在线 GraphML 快照（不停库）；需要严格一致性时用
`neo4j-admin database dump`（须停库）属生产落地项，见 ADR-005。MinIO 快照为运行中
采集——对象级原子性由 MinIO 保证，跨对象无事务语义。

## 备份

```bash
bash deploy/backup/backup.sh            # 输出 ./backups/<时间戳>/
# 定时（宿主 cron，每日 02:00）：
# 0 2 * * * cd /path/to/repo && bash deploy/backup/backup.sh >> logs-backup.log 2>&1
# Windows：任务计划程序每日调用 bash（Git Bash 路径）执行同一脚本
```

产物：`sonarcad.dump` / `minio-snapshot.tar.gz` / `redis-dump.rdb` /
`rabbitmq-definitions.json` / `graphml-snapshot.json`（GraphML 本体在 MinIO
knowledge-dumps 桶）。

异地副本（建议）：`rsync backups/ <nas>` 或对象存储跨区复制——本仓库不代管。

## 恢复

```bash
# 演练（推荐先做）：临时 PG 容器恢复 + 关键表行数核对，自动清理
bash deploy/backup/restore.sh backups/<时间戳> --drill

# 正式：PG --clean 覆盖恢复 + MinIO 回灌
bash deploy/backup/restore.sh backups/<时间戳>
```

Redis 恢复（需短暂停机）：停 redis 容器 → 用 redis-dump.rdb 覆盖卷内
`/data/dump.rdb` → 起容器（AOF 关闭时直接读 RDB；开启 AOF 时先 `redis-cli BGREWRITEAOF`）。
RabbitMQ 恢复：`rabbitmqctl import_definitions rabbitmq-definitions.json`。

## 保留策略（建议基线）

- 本地 7 天：`find backups -maxdepth 1 -type d -mtime +7 -exec rm -rf {} +`
- 每周一保留一份周备份 ×4、每月 1 日保留月备 ×12（差异增量全量见 ADR-005）。
- RTO 目标（单机 compose）：PG 恢复 + MinIO 回灌 ≤ 30 分钟（演练实测见
  `docs/reviews/`）。
