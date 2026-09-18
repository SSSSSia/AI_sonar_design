import type { WsMessage } from './types'

export type SocketStatus = 'connecting' | 'connected' | 'reconnecting' | 'closed'

const MAX_BACKOFF_MS = 30_000

/** 指数退避：1s → 2s → 4s … 30s 封顶（attempt 从 1 计）。 */
export function backoffMs(attempt: number): number {
  return Math.min(1000 * 2 ** Math.max(attempt - 1, 0), MAX_BACKOFF_MS)
}

/** dev 排障：socket 生命周期日志挂 window.__wslog（控制台/排查用，无副作用）。 */
function wslog(event: string, sid: string, detail?: unknown) {
  try {
    const w = window as unknown as { __wslog?: Array<Record<string, unknown>> }
    w.__wslog = w.__wslog ?? []
    w.__wslog.push({ t: Date.now(), event, sid, detail })
    if (w.__wslog.length > 200) w.__wslog.shift()
  } catch {
    // 忽略
  }
}

/**
 * 会话频道 WS 客户端：JWT 建连（?token=）+ 断线指数退避重连。
 * 推送是增强不是权威——重连成功后由调用方做 HTTP 对账刷新。
 */
export class SessionSocket {
  private ws: WebSocket | null = null
  private attempts = 0
  private closedByUser = false
  private timer: number | undefined

  constructor(
    private readonly sid: string,
    private readonly getToken: () => string | null,
    private readonly onMessage: (m: WsMessage) => void,
    private readonly onStatus?: (status: SocketStatus, attempt: number) => void,
  ) {}

  connect() {
    this.closedByUser = false
    this.open()
  }

  close() {
    this.closedByUser = true
    wslog('close-by-user', this.sid)
    if (this.timer !== undefined) window.clearTimeout(this.timer)
    this.ws?.close()
    this.ws = null
  }

  private open() {
    const token = this.getToken()
    if (!token || this.closedByUser) return
    wslog('connect', this.sid)
    this.onStatus?.('connecting', this.attempts)
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(
      `${proto}://${location.host}/ws/api/v1/ws/sessions/${this.sid}?token=${encodeURIComponent(token)}`,
    )
    this.ws = ws
    ws.onopen = () => {
      this.attempts = 0
      wslog('open', this.sid)
      this.onStatus?.('connected', 0)
    }
    ws.onmessage = (ev) => {
      try {
        this.onMessage(JSON.parse(ev.data as string) as WsMessage)
      } catch {
        // 契约外帧（如非 JSON）忽略
      }
    }
    ws.onclose = (ev) => {
      wslog('close', this.sid, `code=${ev.code} clean=${ev.wasClean}`)
      if (this.closedByUser) {
        this.onStatus?.('closed', 0)
        return
      }
      this.attempts += 1
      this.onStatus?.('reconnecting', this.attempts)
      this.timer = window.setTimeout(() => this.open(), backoffMs(this.attempts))
    }
    ws.onerror = () => ws.close()
  }
}
