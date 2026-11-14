import { describe, expect, it } from 'vitest'
import { formatReferenceUsed } from '../pages/session/referenceUsed'

describe('formatReferenceUsed（T16.0 A0-4）', () => {
  it('旧快照无 reference_used → null（不渲染该行）', () => {
    expect(formatReferenceUsed(undefined)).toBeNull()
  })

  it('used=false 如实展示未注入原因', () => {
    expect(formatReferenceUsed({ used: false, reason: 'no_match' })).toBe('未使用参考范例（无匹配）')
    expect(formatReferenceUsed({ used: false, reason: 'weak_filtered' })).toBe('未使用参考范例（弱匹配未注入）')
    expect(formatReferenceUsed({ used: false })).toBe('未使用参考范例（未注入）')
  })

  it('used=true 完整来源行：文件 · 匹配分级 · 匹配度 · 生成来源', () => {
    expect(formatReferenceUsed({
      used: true,
      ref_id: 'towed_array_system_twin',
      file: 'towed_array_system_twin.py',
      match_grade: 'type_matched',
      score: 0.6215,
      generation_source: 'fresh_llm',
    })).toBe('参考范例：towed_array_system_twin.py · 标题命中 · 匹配度 0.62 · 新生成')
  })

  it('缓存命中标注来源；缺可选字段时省略对应段', () => {
    expect(formatReferenceUsed({
      used: true,
      file: 'x.py',
      match_grade: 'weak_keyword',
      generation_source: 'cache_hit',
    })).toBe('参考范例：x.py · 关键词匹配 · 缓存命中')
  })

  it('未知枚举值降级显示原值而非崩溃', () => {
    expect(formatReferenceUsed({
      used: true,
      file: 'x.py',
      match_grade: 'future_grade' as never,
    })).toBe('参考范例：x.py · future_grade')
  })
})
