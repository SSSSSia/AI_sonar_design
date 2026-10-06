import { describe, expect, it } from 'vitest'
import { buildLogSearchParams } from '../api/logs'

describe('T12.5 日志检索参数构造', () => {
  it('空值剔除', () => {
    expect(buildLogSearchParams({ session_id: 'abc', level: undefined, keyword: '', hours: undefined }))
      .toEqual({ session_id: 'abc' })
  })

  it('数值与枚举保留', () => {
    expect(buildLogSearchParams({ level: 'error', kind: 'log', hours: 24, limit: 100, offset: 0 }))
      .toEqual({ level: 'error', kind: 'log', hours: 24, limit: 100, offset: 0 })
  })
})
