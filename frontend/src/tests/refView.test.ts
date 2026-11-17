// T16.1 范例库展示纯函数用例（对齐 referenceUsed.test.ts 先例）。
import { describe, expect, it } from 'vitest'
import {
  formatRefScope, importReportText, latestVersionIds, refSourceLabel, refStatusMeta, shortSha,
} from '../pages/kb/refView'
import type { RefScriptRow } from '../api/references'

function row(over: Partial<RefScriptRow>): RefScriptRow {
  return {
    id: 'r1', ref_id: 'towed_array_system_twin', version: 1, file_name: 'towed.py',
    sonar_types: [], keywords: [], bucket: 'ref-scripts', object_key: 'k',
    content_sha256: 'a'.repeat(64), status: 'pending', source_type: 'catalog_import',
    created_at: '2026-11-15T00:00:00Z', updated_at: '2026-11-15T00:00:00Z',
    ...over,
  } as RefScriptRow
}

describe('refStatusMeta / refSourceLabel', () => {
  it('三态映射 + 未知状态原样透出（A-16 不修饰）', () => {
    expect(refStatusMeta('pending')).toEqual({ label: '待确认', color: 'orange' })
    expect(refStatusMeta('active').color).toBe('green')
    expect(refStatusMeta('disabled').color).toBe('default')
    expect(refStatusMeta('unknown_x').label).toBe('unknown_x')
  })

  it('来源类型映射；deposit=批准沉淀（T16.3 激活），未知类型仍原样透出', () => {
    expect(refSourceLabel('catalog_import')).toBe('目录导入')
    expect(refSourceLabel('manual_upload')).toBe('上传登记')
    expect(refSourceLabel('deposit')).toBe('批准沉淀')
    expect(refSourceLabel('future_type')).toBe('future_type')
  })
})

describe('shortSha / formatRefScope', () => {
  it('sha 短显 + 空值不造值', () => {
    expect(shortSha('a'.repeat(64))).toBe('aaaaaaaa…')
    expect(shortSha('')).toBe('—')
    expect(shortSha(null)).toBe('—')
  })

  it('适用范围拼接；全空返回 null（不渲染该行）', () => {
    expect(formatRefScope({ domain: 'towed_array', domain_rules: 'towed_array_v1', sonar_types: ['拖曳线列阵声呐'] }))
      .toBe('towed_array · towed_array_v1 · 拖曳线列阵声呐')
    expect(formatRefScope({ domain: '', domain_rules: null, sonar_types: [] })).toBeNull()
  })
})

describe('latestVersionIds', () => {
  it('同 ref_id 取最大版本；不同 ref_id 各自保留', () => {
    const rows = [
      row({ id: 'v1', ref_id: 'a', version: 1 }),
      row({ id: 'v3', ref_id: 'a', version: 3 }),
      row({ id: 'v2b', ref_id: 'b', version: 2 }),
      row({ id: 'v1b', ref_id: 'b', version: 1 }),
    ]
    const latest = latestVersionIds(rows)
    expect(latest.has('v3')).toBe(true)
    expect(latest.has('v2b')).toBe(true)
    expect(latest.has('v1')).toBe(false)
    expect(latest.has('v1b')).toBe(false)
    expect(latest.size).toBe(2)
  })

  it('乱序输入也能正确取最新（自身排序，不依赖 API 返回顺序）', () => {
    const rows = [row({ id: 'old', ref_id: 'a', version: 1 }), row({ id: 'new', ref_id: 'a', version: 9 })]
    const latest = latestVersionIds(rows)
    expect(latest.has('new')).toBe(true)
    expect(latest.has('old')).toBe(false)
  })
})

describe('importReportText', () => {
  it('正常报告 + 失败明细如实列出', () => {
    expect(importReportText([], 2, 1)).toBe('导入完成：新增 2 条（待确认），内容一致 1 条')
    const text = importReportText(
      [{ ref_id: 'x', file: 'x.py', action: 'error', error: 'ValueError: boom' }],
      0, 0,
    )
    expect(text).toContain('失败 1 条')
    expect(text).toContain('ValueError: boom')
  })
})
