/*
 * T10.2（PHASE-010）：50 并发混合场景压测（M4 验收口径）。
 *
 * 场景（模拟设计院负载）：登录（setup 一次，全 VU 共用 admin token）→ 知识库浏览 →
 * 会话列表 → 按 VU 串行建项目+会话+START（每 VU 同一时刻至多 1 个活跃会话，
 * 创建速率自然对齐 worker 吞吐，防队列雪崩干扰判定）→ 轮询进度（处理
 * gate1/gate2/knowledge 人工闸自动批复；REWORK_RATIO 比例走 gate1 驳回→A2 返工→
 * 复批闭环，即 M4「无环形震荡」的参数联动验证口径）→ KB 浏览循环。
 *
 * 环境变量：
 *   BASE_URL            网关地址（容器网内默认 http://gateway:8080）
 *   VUS                 并发虚拟用户（默认 50）
 *   DURATION            持续时长（默认 30m）
 *   LLM_MODE            mock|real（创建节奏标注；mock 下管线秒级，real 须配真实供应商）
 *   POLL_INTERVAL_S     轮询间隔（默认 6s，对齐前端 HTTP 对账节律）
 *   REWORK_RATIO        gate1 驳回返工比例（默认 0.3）
 *   SESSION_TIMEOUT_MIN 单会话跟踪上限（默认 20，超时 force 删除防泄漏）
 *
 * 通过口径（M4，报告以 summary 判定）：
 *   http_req_failed < 1%（全部请求均为计划内语义，无故意错请求）
 *   session_poll_latency p95 < 3s
 *   uncaught_5xx == 0；无挂死（k6 无 timeout 堆积 + py-api /healthz 心跳正常）
 *   429 降级由 probe_429.js 单独验证（不混入本场景错误率）
 */
import http from 'k6/http';
import { check, sleep, fail } from 'k6';
import { Counter, Trend } from 'k6/metrics';

const BASE = __ENV.BASE_URL || 'http://gateway:8080';
const VUS = Number(__ENV.VUS || 50);
const DURATION = __ENV.DURATION || '30m';
const LLM_MODE = __ENV.LLM_MODE || 'mock';
const POLL_INTERVAL = Number(__ENV.POLL_INTERVAL_S || 6);
const REWORK_RATIO = Number(__ENV.REWORK_RATIO || 0.3);
const SESSION_TIMEOUT_MIN = Number(__ENV.SESSION_TIMEOUT_MIN || 20);
const ADMIN_USER = __ENV.ADMIN_USER || 'admin';
const ADMIN_PASS = __ENV.ADMIN_PASS || 'admin123';

const pollLatency = new Trend('session_poll_latency', true);
const n5xx = new Counter('uncaught_5xx');
const created = new Counter('sessions_created');
const completed = new Counter('sessions_completed');
const failedSess = new Counter('sessions_failed');
const reworked = new Counter('gate1_rework_loops');
const timeouts = new Counter('sessions_timeout_dropped');

export const options = {
  scenarios: {
    mixed: {
      executor: 'constant-vus',
      vus: VUS,
      duration: DURATION,
      gracefulStop: '30s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    session_poll_latency: ['p(95)<3000'],
    checks: ['rate>0.99'],
  },
};

const RUN_TAG = `load-${LLM_MODE}-${Date.now().toString(36)}`;
let mySessions = []; // VU 本地：{sid,pid,startedAt,reworkDone}

export function setup() {
  const r = http.post(`${BASE}/api/v1/auth/login`,
    JSON.stringify({ username: ADMIN_USER, password: ADMIN_PASS }),
    { headers: { 'Content-Type': 'application/json' } });
  check(r, { 'login 200': (res) => res.status === 200 });
  const token = r.json('data.token');
  if (!token) fail('setup 登录失败：' + r.body);
  return { token };
}

// ---- 统一请求助手（headers + name 标签一次带上；k6 的 params 只收一个对象） ----
function req(data) {
  return {
    get(url, name) {
      return http.get(url, { headers: hdr(data), tags: { name } });
    },
    post(url, body, name) {
      return http.post(url, body, { headers: hdr(data), tags: { name } });
    },
    del(url, name) {
      return http.del(url, null, { headers: hdr(data), tags: { name } });
    },
  };
}

function hdr(data) {
  return { Authorization: `Bearer ${data.token}`, 'Content-Type': 'application/json' };
}

function jd(r) { try { return r.json(); } catch (_) { return {}; } }

export default function (data) {
  const api = req(data);

  // ① 知识库浏览（GET 全幂等）
  const rb = api.get(`${BASE}/api/v1/kb/bases`, 'kb_bases');
  check(rb, { 'kb bases ok': (r) => r.status === 200 });
  const bases = jd(rb).data || [];
  if (bases.length > 0 && Math.random() < 0.3) {
    api.get(`${BASE}/api/v1/kb/documents?base_id=${bases[0].id}`, 'kb_docs');
  }

  // ② 会话列表
  api.get(`${BASE}/api/v1/workflow/sessions?page=1&size=10`, 'session_list');

  // ③ 每 VU 至多 1 个活跃会话：无活跃则建（速率自然对齐 worker 吞吐）
  if (mySessions.length === 0) {
    const tag = `${RUN_TAG}-vu${__VU}-${Date.now().toString(36)}`;
    const rp = api.post(`${BASE}/api/v1/projects`, JSON.stringify({ name: tag, code: tag }), 'create_project');
    const pid = rp.status === 200 ? jd(rp).data?.id : null;
    if (check(rp, { 'project 200': (r) => r.status === 200 }) && pid) {
      const rs = api.post(`${BASE}/api/v1/workflow/sessions`,
        JSON.stringify({
          projectId: pid,
          title: ('压测' + tag.slice(-12)).slice(0, 20), // 服务端校验标题 ≤20 字（实测 400 教训）
          requirement: '50 并发压测样例：拖曳线列阵声呐，阵元 96，间距 0.6m，工作频率 100Hz-1kHz，输出 STEP 模型。',
        }),
        'create_session');
      const sid = rs.status === 200 ? jd(rs).data?.id : null;
      if (check(rs, { 'session 200': (r) => r.status === 200 }) && sid) {
        const rstart = api.post(`${BASE}/api/v1/workflow/sessions/${sid}/events`,
          JSON.stringify({ event: 'START' }), 'session_start');
        if (check(rstart, { 'START ok': (r) => r.status === 200 })) {
          created.add(1);
          mySessions.push({ sid, pid, startedAt: Date.now(), reworkDone: false });
        } else {
          api.del(`${BASE}/api/v1/workflow/sessions/${sid}?force=true`, 'cleanup_session');
          api.del(`${BASE}/api/v1/projects/${pid}`, 'cleanup_project');
        }
      } else {
        api.del(`${BASE}/api/v1/projects/${pid}`, 'cleanup_project');
      }
    }
  }

  // ④ 轮询活跃会话：自动过闸 + 返工闭环 + 终态回收
  for (const s of mySessions.slice()) {
    const t0 = Date.now();
    const r = api.get(`${BASE}/api/v1/workflow/sessions/${s.sid}`, 'session_poll');
    pollLatency.add(Date.now() - t0, { name: 'session_poll' });
    if (!check(r, { 'poll 200': (res) => res.status === 200 })) {
      if (r.status >= 500) n5xx.add(1);
      continue;
    }
    const d = jd(r).data || {};
    const status = d.pipelineStatus;
    const stage = d.currentStage;
    if (status === 'paused' && ['gate1', 'gate2', 'knowledge'].includes(stage)) {
      if (stage === 'gate1' && !s.reworkDone && Math.random() < REWORK_RATIO) {
        // 驳回 → A2 局部回算 → 下次轮询再批准（返工闭环 = 无环形震荡验证口径）
        s.reworkDone = true;
        reworked.add(1);
        api.post(`${BASE}/api/v1/workflow/sessions/${s.sid}/reviews/gate1`,
          JSON.stringify({ decision: 'rejected', annotations: ['压测返工闭环'], reworkStage: 'A2' }),
          'gate_reject');
        continue;
      }
      const rr = api.post(`${BASE}/api/v1/workflow/sessions/${s.sid}/reviews/${stage}`,
        JSON.stringify({ decision: 'approved', annotations: ['压测自动过闸'] }),
        'gate_approve');
      check(rr, { 'approve 200': (x) => x.status === 200 });
      continue;
    }
    if (status === 'completed' || status === 'failed') {
      if (status === 'completed') completed.add(1); else failedSess.add(1);
      api.del(`${BASE}/api/v1/workflow/sessions/${s.sid}?force=true`, 'cleanup_session');
      api.del(`${BASE}/api/v1/projects/${s.pid}`, 'cleanup_project');
      mySessions = mySessions.filter((x) => x !== s);
      continue;
    }
    if (Date.now() - s.startedAt > SESSION_TIMEOUT_MIN * 60000) {
      // 超时回收（force 删除防会话/任务泄漏），如实计数
      timeouts.add(1);
      api.del(`${BASE}/api/v1/workflow/sessions/${s.sid}?force=true`, 'cleanup_session');
      api.del(`${BASE}/api/v1/projects/${s.pid}`, 'cleanup_project');
      mySessions = mySessions.filter((x) => x !== s);
    }
  }

  sleep(POLL_INTERVAL + Math.random() * 2); // 0~2s 抖动，避免整步共振
}

export function handleSummary(data) {
  const t = data.metrics;
  const lines = [];
  lines.push('===== 压测 summary（M4 判定用） =====');
  lines.push(`LLM_MODE=${LLM_MODE} VUS=${VUS} DURATION=${DURATION} REWORK_RATIO=${REWORK_RATIO}`);
  for (const k of ['http_req_failed', 'http_req_duration', 'session_poll_latency', 'checks']) {
    if (t[k]) lines.push(`${k}: ${JSON.stringify(t[k].values)}`);
  }
  lines.push(`sessions: created=${t.sessions_created ? t.sessions_created.values.count : 0} ` +
    `completed=${t.sessions_completed ? t.sessions_completed.values.count : 0} ` +
    `failed=${t.sessions_failed ? t.sessions_failed.values.count : 0} ` +
    `reworked=${t.gate1_rework_loops ? t.gate1_rework_loops.values.count : 0} ` +
    `timeout_dropped=${t.sessions_timeout_dropped ? t.sessions_timeout_dropped.values.count : 0}`);
  lines.push(`uncaught_5xx: ${t.uncaught_5xx ? t.uncaught_5xx.values.count : 0}`);
  return { stdout: lines.join('\n') + '\n' };
}
