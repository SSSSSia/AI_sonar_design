#!/usr/bin/env bash
# Sonar-CAD v2 全栈一键启动（Windows Git Bash）
# 用法：bash scripts/start-all.sh
# 行为：逐层探测，已在跑的跳过、缺的补起；数据底座容器复用 Docker。
#   1) 数据底座：docker compose（deploy/docker-compose.yml，9 容器）
#   2) Java 五服务：gateway 8080 / auth 8081 / workflow 8082 / biz 8083 / notify 8084
#   3) py-engine：uvicorn 8090 + celery worker（必须带根目录 .env，裸起会 RabbitMQ 403）
#   4) 前端：vite 5173（绑定 [::1]，浏览器开 http://localhost:5173）
# 停止单个服务：netstat -ano | grep :<端口> 找 PID → taskkill //F //PID <pid>
#   （不要 taskkill //IM python.exe——会连 uvicorn+celery 一起杀；java 同理按 PID）
set -o pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

say() { printf '[start-all] %s\n' "$*"; }
listen() { netstat -ano | grep LISTENING | grep -qE ":$1\s"; }

# 0) 环境统一注入（.env 含数据库口令/JWT/CELERY_BROKER_URL 插值，CRLF 需剥）
if [ ! -f .env ]; then say "缺少根目录 .env（参考 .env.example）"; exit 1; fi
set -a; source <(sed 's/\r$//' .env); set +a

# 1) 数据底座
if ! docker info >/dev/null 2>&1; then
  say "Docker Desktop 未运行，请先启动它再重试"; exit 1
fi
if docker ps --format '{{.Names}}' | grep -q sonar-postgres; then
  say "数据底座容器：已运行（$(docker ps --format '{{.Names}}' | grep -c sonar-) 个 sonar-* 容器）"
else
  say "数据底座启动中：docker compose up -d（拉起+健康检查需 1~3 分钟）"
  docker compose -f deploy/docker-compose.yml up -d
  say "等待容器健康（轮询 90s）…"
  for _ in $(seq 1 18); do
    n=$(docker ps --format '{{.Names}}\t{{.Status}}' | grep -c ' (healthy)')
    [ "$n" -ge 9 ] && break
    sleep 5
  done
fi

# 2) Java 五服务（并行后台，日志落根目录 logs-java-<名>.log）
start_java() { # $1=名 $2=jar 相对路径 $3=端口
  if listen "$3"; then say "java $1(:$3) 已在跑"; return; fi
  ( cd "$ROOT/java-platform" && nohup java -jar "$2" > "$ROOT/logs-java-$1.log" 2>&1 & )
  say "java $1(:$3) 已启动 → logs-java-$1.log"
}
start_java gateway  gateway/target/gateway-0.1.0-SNAPSHOT.jar  8080
start_java auth     auth/target/auth-0.1.0-SNAPSHOT.jar        8081
start_java workflow workflow/target/workflow-0.1.0-SNAPSHOT.jar 8082
start_java biz      biz/target/biz-0.1.0-SNAPSHOT.jar          8083
start_java notify   notify/target/notify-0.1.0-SNAPSHOT.jar    8084

# 3) py-engine（API + worker；均需上面的 .env 环境）
if listen 8090; then say "py-engine API(:8090) 已在跑"; else
  ( cd "$ROOT/py-engine" && nohup python -m uvicorn api.main:app --host 127.0.0.1 --port 8090 --timeout-keep-alive "${UVICORN_TIMEOUT_KEEP_ALIVE_S:-30}" > "$ROOT/logs-py-api.log" 2>&1 & )
  say "py-engine API(:8090) 已启动 → logs-py-api.log"
fi
CELERY_CNT=$(wmic process where "name='python.exe' and commandline like '%celery%'" get processid 2>/dev/null | grep -cE '^[0-9]+')
if [ "${CELERY_CNT:-0}" -ge 1 ]; then
  say "celery worker 已在跑"
else
  ( cd "$ROOT/py-engine" && nohup python -m celery -A worker.celery_app worker --pool=solo -l info > "$ROOT/logs-py-worker.log" 2>&1 & )
  say "celery worker 已启动 → logs-py-worker.log"
fi

# 4) 前端
if listen 5173; then say "前端 vite(:5173) 已在跑"; else
  ( cd "$ROOT/frontend" && nohup npm run dev > "$ROOT/logs-vite.log" 2>&1 & )
  say "前端 vite(:5173) 已启动 → logs-vite.log"
fi

# 5) 就绪探测（Java 冷启约 15~30s）
say "等待服务就绪（最多 90s）…"
ok=0
for _ in $(seq 1 18); do
  sleep 5
  PY=$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8090/healthz || true)
  GW=$(curl -s -m 3 -o /dev/null -w '%{http_code}' -X POST http://localhost:8080/api/v1/auth/login \
        -H 'Content-Type: application/json' -d '{"username":"admin","password":"admin123"}' || true)
  if [ "$PY" = "200" ] && [ "$GW" = "200" ]; then ok=1; break; fi
done
if [ "$ok" = "1" ]; then
  say "全栈就绪 ✅  浏览器打开 http://localhost:5173（admin / admin123）"
else
  say "部分服务仍在启动，稍后自查：curl 127.0.0.1:8090/healthz / 看根目录 logs-*.log"
fi
