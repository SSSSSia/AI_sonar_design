// T16.0 A0-4：IR3 reference_used 展示文案（纯函数，便于 vitest 覆盖）。
// 旧快照无 reference_used → null（不渲染该行）；used=false 如实展示未注入原因（A-14/A-16）。
import type { Ir3ReferenceUsed } from '../../api/workflow'

const GRADE_LABEL: Record<string, string> = {
  type_matched: '标题命中',
  weak_keyword: '关键词匹配',
}

const SOURCE_LABEL: Record<string, string> = {
  fresh_llm: '新生成',
  cache_hit: '缓存命中',
}

const REASON_LABEL: Record<string, string> = {
  no_catalog: '无范例索引',
  no_match: '无匹配',
  weak_filtered: '弱匹配未注入',
  read_failed: '范例读取失败',
}

export function formatReferenceUsed(ru?: Ir3ReferenceUsed): string | null {
  if (!ru) return null
  if (!ru.used) {
    const why = ru.reason ? REASON_LABEL[ru.reason] ?? ru.reason : '未注入'
    return `未使用参考范例（${why}）`
  }
  const parts: string[] = []
  if (ru.file) parts.push(`参考范例：${ru.file}`)
  if (ru.match_grade) parts.push(GRADE_LABEL[ru.match_grade] ?? ru.match_grade)
  if (typeof ru.score === 'number') parts.push(`匹配度 ${ru.score.toFixed(2)}`)
  if (ru.generation_source) parts.push(SOURCE_LABEL[ru.generation_source] ?? ru.generation_source)
  return parts.length > 0 ? parts.join(' · ') : '已使用参考范例'
}
