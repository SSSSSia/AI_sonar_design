// npm run dev 前置：自动释放 5173——结束占用进程，保证每次启动都拿到全新 dev server。
// 背景：vite 端口被占默认静默顺延 +1，曾攒出 5173/5174/5175 三个并存实例
// （devlog 2026-09-24 补充 73）；vite.config 的 strictPort 保留兜底——本脚本
// 清理失败时 vite 显式报错退出，而不是换端口。端口与 vite.config.ts 保持一致。
import { execFileSync } from 'node:child_process'

const PORT = 5173

const run = (cmd, args) =>
  execFileSync(cmd, args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })

function listenersWin() {
  const pids = new Set()
  for (const line of run('netstat', ['-ano']).split('\n')) {
    // TCP  [::1]:5173  [::]:0  LISTENING  <pid>
    const cols = line.trim().split(/\s+/)
    if (cols.length >= 5 && cols[0] === 'TCP' && cols[3] === 'LISTENING'
        && cols[1].endsWith(`:${PORT}`)) {
      const pid = Number(cols[4])
      if (pid > 0) pids.add(pid)
    }
  }
  return [...pids]
}

function listenersPosix() {
  try {
    return run('lsof', ['-ti', `:${PORT}`, '-sTCP:LISTEN'])
      .split('\n').map(Number).filter(Boolean)
  } catch {
    return [] // 无 lsof 时视为无占用，交给 vite + strictPort 兜底
  }
}

const pids = process.platform === 'win32' ? listenersWin() : listenersPosix()
for (const pid of pids) {
  let name = ''
  try {
    if (process.platform === 'win32') {
      const row = run('tasklist', ['/FI', `PID eq ${pid}`, '/FO', 'CSV']).split('\n')[1]
      if (row) name = ` (${row.split('","')[0].replace(/^"/, '')})`
    }
  } catch { /* 进程名仅用于展示 */ }
  try {
    console.log(`[dev-port] 释放端口 ${PORT}：结束占用进程 ${pid}${name}`)
    if (process.platform === 'win32') {
      run('taskkill', ['/F', '/T', '/PID', String(pid)]) // /T 连 esbuild 等子进程一起带走
    } else {
      process.kill(pid, 'SIGKILL')
    }
  } catch {
    console.error(`[dev-port] 结束进程 ${pid} 失败（权限不足？）——` +
                  `请手工检查：netstat -ano | grep :${PORT}`)
    process.exit(1)
  }
}
if (pids.length > 0) {
  // 给内核一点时间真正释放监听套接字，避免 vite 起来时端口尚未解绑
  await new Promise((r) => setTimeout(r, 500))
}
