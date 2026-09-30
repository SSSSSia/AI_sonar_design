/*
 * T10.2：429 限流降级探针（独立脚本——不混入 stress_mixed 的错误率口径）。
 *
 * 单用户突发超限打 py-engine 路由（RL_PY 默认 replenish=20/s、burst=60，键控=同一用户），
 * 预期网关 RequestRateLimiter 依次返回：桶内 200 → 超限 429（结构化 JSON + Retry-After 头）。
 * M4 证据 = 429 出现次数 > 0，且每个 429 带 code=429 JSON 体与 Retry-After 头。
 *
 * 环境变量：BASE_URL（默认 http://gateway:8080）、BURST（默认 120，> 60 才会触顶）。
 */
import http from 'k6/http';
import { check, fail } from 'k6';
import { Counter } from 'k6/metrics';

const BASE = __ENV.BASE_URL || 'http://gateway:8080';
const BURST = Number(__ENV.BURST || 120);
const ADMIN_USER = __ENV.ADMIN_USER || 'admin';
const ADMIN_PASS = __ENV.ADMIN_PASS || 'admin123';

const hit429 = new Counter('probe_429_count');
const structured = new Counter('probe_429_structured_ok');

export const options = {
  scenarios: {
    burst: {
      executor: 'shared-iterations',
      vus: 1,
      iterations: BURST,
      maxDuration: '2m',
    },
  },
  thresholds: {
    probe_429_count: ['count>0'],           // 必须触到限流
    probe_429_structured_ok: ['count>0'],   // 且降级体结构化
    checks: ['rate>0.99'],
  },
};

export function setup() {
  const r = http.post(`${BASE}/api/v1/auth/login`,
    JSON.stringify({ username: ADMIN_USER, password: ADMIN_PASS }),
    { headers: { 'Content-Type': 'application/json' } });
  const token = r.json('data.token');
  if (!token) fail('setup 登录失败');
  return { token };
}

export default function (data) {
  const r = http.get(`${BASE}/api/v1/kb/bases`,
    { headers: { Authorization: `Bearer ${data.token}` } }, { tags: { name: 'probe' } });
  if (r.status === 429) {
    hit429.add(1);
    const hasRetryAfter = r.headers['Retry-After'] !== undefined || r.headers['retry-after'] !== undefined;
    const bodyOk = (() => {
      try {
        const b = r.json();
        return b.code === 429 && typeof b.retryAfterSeconds === 'number' && b.trace_id === 'gateway';
      } catch (_) {
        return false;
      }
    })();
    check(r, {
      '429 带结构化 JSON 体': () => bodyOk,
      '429 带 Retry-After 头': () => hasRetryAfter,
    });
    if (bodyOk && hasRetryAfter) structured.add(1);
  } else {
    check(r, { '200 或 429': (x) => x.status === 200 });
  }
}
