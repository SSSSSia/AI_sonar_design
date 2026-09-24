import { describe, expect, it } from 'vitest'
import { mergeLogs } from '../pages/session/OverviewTab'
import type { SessionLogRow } from '../api/workflow'
import type { SessionLogLine } from '../stores/logStore'

const hist = (id: number, created_at: string, text: string): SessionLogRow => ({
  id, level: 'info', text, created_at,
})
const live = (at: number, text: string): SessionLogLine => ({ at, level: 'ok', text })

describe('mergeLogs', () => {
  it('历史在前、实时在后，按序合并', () => {
    const out = mergeLogs([hist(1, '2026-09-24T10:00:00+08:00', 'A1 开始'), hist(2, '2026-09-24T10:00:05+08:00', 'A1 完成')], [live(1000, '实时行')])
    expect(out.map((l) => l.text)).toEqual(['A1 开始', 'A1 完成', '实时行'])
  })

  it('历史与实时重复的行按 时间+文本 去重', () => {
    const t = dayjsTs('2026-09-24T10:00:00+08:00')
    const out = mergeLogs([hist(1, '2026-09-24T10:00:00+08:00', '同一行')], [live(t, '同一行')])
    expect(out).toHaveLength(1)
  })

  it('两侧皆空返回空数组', () => {
    expect(mergeLogs(undefined, undefined)).toEqual([])
  })
})

function dayjsTs(iso: string): number {
  // eslint 下保持简单：直接用 Date 解析（与 dayjs 同为本地时区解析）
  return new Date(iso).getTime()
}
