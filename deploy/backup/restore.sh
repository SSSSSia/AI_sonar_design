#!/usr/bin/env bash
# =====================================================================
# Sonar-CAD 灾难恢复（T11.7）：从 backup.sh 产物恢复
# 用法：bash deploy/backup/restore.sh <备份目录> [--target-db sonarcad]
# 演练模式（不碰在跑栈）：--drill 启动临时 PG 容器恢复并核对行数后自动清理。
# =====================================================================
set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash 会把容器内路径 /app/... 改写为宿主路径（T11.7 实测）
cd "$(dirname "$0")/../.."

SRC="$1"; shift || true
DRILL=0; TARGET_DB="${POSTGRES_DB:-sonarcad}"
while [ $# -gt 0 ]; do
  case "$1" in
    --drill) DRILL=1 ;;
    --target-db) TARGET_DB="$2"; shift ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
  shift
done

PG_CONTAINER="${PG_CONTAINER:-sonar-postgres}"
PG_USER="${POSTGRES_USER:-sonarcad}"
[ -f "$SRC/sonarcad.dump" ] || SRC_DUMP=$(ls "$SRC"/*.dump 2>/dev/null | head -1) || true
SRC_DUMP="${SRC_DUMP:-$SRC/sonarcad.dump}"
[ -f "$SRC_DUMP" ] || { echo "找不到 PG dump: $SRC"; exit 1; }

if [ "$DRILL" = "1" ]; then
  echo "==> 演练模式：临时 PG 容器恢复（不碰在跑栈）"
  NET="$(docker inspect "$PG_CONTAINER" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')"
  docker run -d --rm --name sonarcad-drill-pg -e POSTGRES_PASSWORD=drill -e POSTGRES_USER="$PG_USER" \
    -e POSTGRES_DB="$TARGET_DB" --network "$NET" postgres:17-alpine >/dev/null
  trap 'docker rm -f sonarcad-drill-pg >/dev/null 2>&1 || true' EXIT
  sleep 8
  docker exec -i sonarcad-drill-pg psql -U "$PG_USER" -c "SELECT 1" >/dev/null
  docker exec -i sonarcad-drill-pg pg_restore -U "$PG_USER" -d "$TARGET_DB" --no-owner < "$SRC_DUMP" \
    && echo "==> pg_restore OK"
  echo "==> 关键表行数（演练库）："
  docker exec sonarcad-drill-pg psql -U "$PG_USER" -d "$TARGET_DB" -t -c \
    "SELECT 'users', count(*) FROM users UNION ALL SELECT 'design_sessions', count(*) FROM design_sessions
     UNION ALL SELECT 'ir_snapshots', count(*) FROM ir_snapshots UNION ALL SELECT 'notifications', count(*) FROM notifications"
  echo "==> 演练完成，临时容器已清理。"
  exit 0
fi

echo "==> 正式恢复（会覆盖目标库数据，确认后继续）"
echo "  PG   : $PG_CONTAINER / $TARGET_DB ← $SRC_DUMP"
echo "  MinIO: minio-snapshot.tar.gz → 各桶（--restore 由 py-engine 容器执行）"
echo "  Redis: redis-dump.rdb（需停 redis 替换 /data/dump.rdb 后重启）"
read -rp "继续？(yes/no) " ok; [ "$ok" = "yes" ] || exit 1

# ① PG
docker exec -i "$PG_CONTAINER" pg_restore -U "$PG_USER" -d "$TARGET_DB" --clean --if-exists --no-owner < "$SRC_DUMP"
# ② MinIO（脚本注入 py-engine 容器执行回灌）
docker cp py-engine/deploy_backup_minio.py "${MINIO_CONTAINER:-sonarcad-py-api}":/app/deploy_backup_minio.py
docker exec "${MINIO_CONTAINER:-sonarcad-py-api}" python /app/deploy_backup_minio.py --restore "$SRC/minio-snapshot.tar.gz"
# ③ Redis/RabbitMQ：见 README（需短暂停机，不纳入脚本自动段）
echo "==> PG 与 MinIO 已恢复；Redis/RabbitMQ 恢复步骤见 deploy/backup/README.md"
