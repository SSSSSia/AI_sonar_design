import { describe, expect, it } from 'vitest'
import type { Ir1, KbSelection } from '../api/workflow'
import { kbHitSummary } from '../pages/session/OverviewTab'

function ir1With(selection: KbSelection | undefined, refs: Ir1['retrieved_context']): Ir1 {
  return {
    objectives: [],
    missing_items: [],
    sub_queries: [],
    retrieved_context: refs,
    ...(selection ? { kb_selection: selection } : {}),
  }
}

describe('kbHitSummary（PHASE-006 总览知识检索卡片）', () => {
  it('manual 模式按 kb_id 分组命中并聚合文档数', () => {
    const sel: KbSelection = {
      mode: 'manual',
      bases: [
        { kb_id: 'kb-a', kb_name: '库A', hits: -1, selected: true },
        { kb_id: 'kb-b', kb_name: '库B', hits: -1, selected: false },
      ],
    }
    const out = kbHitSummary(
      ir1With(sel, [
        { doc_id: 'd1', location: '第1页', snippet: '', kb_id: 'kb-a' },
        { doc_id: 'd1', location: '第2页', snippet: '', kb_id: 'kb-a' },
      ]),
    )
    expect(out.mode).toBe('manual')
    expect(out.groups).toHaveLength(1)
    expect(out.groups[0].kbName).toBe('库A')
    expect(out.groups[0].hitRefs).toHaveLength(2)
    expect(out.groups[0].hitDocs).toBe(1)
  })

  it('auto 零命中兜底：未选中库不展示，fallbackAll 标注', () => {
    const sel: KbSelection = {
      mode: 'auto',
      fallback_all: true,
      bases: [
        { kb_id: 'kb-a', kb_name: '库A', hits: 0, selected: true },
        { kb_id: 'kb-b', kb_name: '库B', hits: 0, selected: true },
      ],
    }
    const out = kbHitSummary(ir1With(sel, []))
    expect(out.fallbackAll).toBe(true)
    expect(out.groups).toHaveLength(2)
    expect(out.groups.every((g) => g.hitRefs.length === 0)).toBe(true)
  })

  it('旧快照无 kb_selection：回退会话库名展示', () => {
    const out = kbHitSummary(ir1With(undefined, []), ['默认知识库', '资料库'])
    expect(out.mode).toBe('unknown')
    expect(out.groups.map((g) => g.kbName)).toEqual(['默认知识库', '资料库'])
  })

  it('旧快照无留痕无库名：引用整体归入"全部知识库"一组', () => {
    const out = kbHitSummary(ir1With(undefined, [
      { doc_id: 'd1', location: 'p1', snippet: '' },
      { doc_id: 'd2', location: 'p2', snippet: '' },
    ]))
    expect(out.groups).toHaveLength(1)
    expect(out.groups[0].kbName).toBe('全部知识库')
    expect(out.groups[0].hitDocs).toBe(2)
  })

  it('引用缺 kb_id（旧链路）：归入"未标注来源库"组', () => {
    const sel: KbSelection = {
      mode: 'manual',
      bases: [{ kb_id: 'kb-a', kb_name: '库A', hits: -1, selected: true }],
    }
    const out = kbHitSummary(ir1With(sel, [{ doc_id: 'd9', location: 'p1', snippet: '' }]))
    expect(out.groups.map((g) => g.kbKey)).toContain('unknown')
  })
})
