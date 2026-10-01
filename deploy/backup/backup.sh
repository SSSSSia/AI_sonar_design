#!/usr/bin/env bash
# =====================================================================
# Sonar-CAD 容灾备份（T11.7，P2-7）：PG 权威备份 + MinIO 对象快照 + Redis RDB
#   + RabbitMQ definitions + 触发 Neo4j GraphML 快照（经 py-engine 端点）
# 用法（宿主机，需 docker 可用）：
#   bash deploy/backup/backup.sh [输出目录，默认 ./backups/日期时间]
# 定时：宿主 cron / Windows 计划任务调本脚本（默认每日 02:00 建议）。
# 口径（A-16 如实）：Neo4j 备份 = 在线 GraphML 快照（一致性在写入端保证）；
# 离线一致性 dump（neo4j-admin database dump，需停库）属生产落地项，见 ADR-005。
# =====================================================================
set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash 会把容器内路径 /app/... 改写为宿主路径（T11.7 实测）

cd "$(dirname "$0")/../.."   # 仓库根

OUT="${1:-backups/$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
echo "==> 备份输出目录: $OUT"

PG_CONTAINER="${PG_CONTAINER:-sonar-postgres}"
MINIO_CONTAINER="${MINIO_CONTAINER:-sonarcad-py-api}"
REDIS_CONTAINER="${REDIS_CONTAINER:-sonar-redis}"
MQ_CONTAINER="${MQ_CONTAINER:-sonar-rabbitmq}"
PG_USER="${POSTGRES_USER:-sonarcad}"
PG_DB="${POSTGRES_DB:-sonarcad}"

# ① PG 全库（单库 sonarcad 承载全部服务表——权威备份，-Fc 自定义格式可 pg_restore）
echo "==> [1/5] pg_dump ${PG_DB}"
docker exec "$PG_CONTAINER" pg_dump -U "$PG_USER" -Fc "$PG_DB" > "$OUT/${PG_DB}.dump"

# ② MinIO 对象快照（py-engine 容器内 minio SDK 全桶下载打包；含新增 knowledge-dumps）
echo "==> [2/5] MinIO 对象快照（经 py-engine 容器）"
docker exec "$MINIO_CONTAINER" python /app/deploy_backup_minio.py > /dev/null
docker cp "$MINIO_CONTAINER":/tmp/minio-snapshot.tar.gz "$OUT/minio-snapshot.tar.gz"

# ③ Redis RDB（BGSAVE 后拷贝 dump.rdb；AOF 亦在 redis-data 卷，README 见 RPO 说明）
echo "==> [3/5] Redis BGSAVE"
docker exec "$REDIS_CONTAINER" redis-cli BGSAVE || true
sleep 2
docker cp "$REDIS_CONTAINER":/data/dump.rdb "$OUT/redis-dump.rdb" 2>/dev/null \
  || echo "  （redis dump 拷贝失败——检查 REDIS_CONTAINER 名，跳过不阻断）"

# ④ RabbitMQ definitions（交换机/队列/绑定拓扑）
echo "==> [4/5] RabbitMQ definitions"
docker exec "$MQ_CONTAINER" rabbitmqctl export_definitions /tmp/defs.json >/dev/null 2>&1 \
  && docker cp "$MQ_CONTAINER":/tmp/defs.json "$OUT/rabbitmq-definitions.json" \
  || echo "  （definitions 导出失败——检查 MQ_CONTAINER 名，跳过不阻断）"

# ⑤ Neo4j GraphML 快照（在线，经 py-engine 管理端点落 knowledge-dumps 桶 + 本地留档）
echo "==> [5/5] Neo4j GraphML 快照"
PYAPI="${PYAPI:-http://127.0.0.1:8090}"
curl -s -X POST "$PYAPI/api/v1/kb/graph/snapshot" \
  -H "X-User-Id: backup-script" -H "X-User-Roles: admin" > "$OUT/graphml-snapshot.json" \
  && echo "  （快照已落 knowledge-dumps 桶，meta: $(cat "$OUT/graphml-snapshot.json" | head -c 200)）" \
  || echo "  （GraphML 快照失败——检查 py-engine 可达，跳过不阻断）"

# 清理脚本容器内临时包
docker exec "$MINIO_CONTAINER" rm -f /tmp/minio-snapshot.tar.gz >/dev/null 2>&1 || true

echo "==> 完成。清单："
ls -la "$OUT"
echo "保留策略建议：本地保留 7 天（find backups -mtime +7 -delete）；异地副本见 README。"
