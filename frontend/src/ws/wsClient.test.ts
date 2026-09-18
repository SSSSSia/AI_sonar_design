import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { backoffMs, SessionSocket, type SocketStatus } from './wsClient'
import type { WsMessage } from './types'

describe('backoffMs 指数退避', () => {
  it('1s 起步、翻倍、30s 封顶', () => {
    expect(backoffMs(1)).toBe(1000)
    expect(backoffMs(2)).toBe(2000)
    expect(backoffMs(3)).toBe(4000)
    expect(backoffMs(9)).toBe(30_000)
    expect(backoffMs(50)).toBe(30_000)
  })
})

/** 最小 WebSocket 桩：记录实例，允许测试手动触发事件。 */
class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onclose: ((ev?: { code: number; wasClean: boolean }) => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  constructor(public url: string) {
    FakeWebSocket.instances.push(this)
  }
  close() {
    this.closed = true
  }
  fireClose = () => this.onclose?.({ code: 1000, wasClean: true })
}

const progressMsg: WsMessage = {
  type: 'task_progress',
  session_id: 's1',
  timestamp: '2026-09-17T00:00:00Z',
  payload: { stage: 'A2', status: 'running', progress: 47 },
}

describe('SessionSocket', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('解析 JSON 帧并回调；非 JSON 忽略', () => {
    const messages: WsMessage[] = []
    const socket = new SessionSocket('s1', () => 'tok', (m) => messages.push(m))
    socket.connect()
    const ws = FakeWebSocket.instances[0]
    expect(ws.url).toContain('/ws/api/v1/ws/sessions/s1?token=tok')
    ws.onopen?.()
    ws.onmessage?.({ data: JSON.stringify(progressMsg) })
    ws.onmessage?.({ data: 'not-json' })
    expect(messages).toEqual([progressMsg])
    socket.close()
  })

  it('无 token 时不建连', () => {
    const socket = new SessionSocket('s1', () => null, () => {})
    socket.connect()
    expect(FakeWebSocket.instances).toHaveLength(0)
  })

  it('断线重连且状态回调按序翻转', () => {
    vi.useFakeTimers()
    const statuses: SocketStatus[] = []
    const socket = new SessionSocket('s1', () => 'tok', () => {}, (s) => statuses.push(s))
    socket.connect()
    const ws = FakeWebSocket.instances[0]
    ws.onopen?.()
    ;(ws as FakeWebSocket).fireClose()
    expect(statuses).toEqual(['connecting', 'connected', 'reconnecting'])
    vi.advanceTimersByTime(backoffMs(1))
    expect(FakeWebSocket.instances).toHaveLength(2)
    socket.close()
    vi.useRealTimers()
  })

  it('close 后不再重连', () => {
    vi.useFakeTimers()
    const socket = new SessionSocket('s1', () => 'tok', () => {})
    socket.connect()
    const ws = FakeWebSocket.instances[0]
    socket.close()
    ws.fireClose()
    vi.advanceTimersByTime(60_000)
    expect(FakeWebSocket.instances).toHaveLength(1)
    vi.useRealTimers()
  })
})
