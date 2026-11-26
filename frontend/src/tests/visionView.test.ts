// T18.6 视觉校验卡呈现纯函数单测（诚实三态/级别色/元信息不造 A-16）
import { describe, expect, it } from 'vitest'
import { findingColor, verdictText, visionCardState, visionMetaLine } from '../pages/session/visionView'
import type { Ir3VisionCheck } from '../api/workflow'

describe('visionCardState', () => {
  it('off：无字段/空对象=未启用（关闭态如实）', () => {
    expect(visionCardState(undefined)).toEqual({
      kind: 'off',
      text: '未启用视觉校验（CAD_VISION_CHECK 默认关）——审阅以数值校验与三维预览为准',
    })
    expect(visionCardState({} as Ir3VisionCheck).kind).toBe('off')
  })

  it('skipped：带原因透传', () => {
    const s = visionCardState({ status: 'skipped', detail: 'render_failed: timeout' })
    expect(s.kind).toBe('skipped')
    expect(s.text).toContain('render_failed: timeout')
  })

  it('checked：text=判词全文', () => {
    const s = visionCardState({ status: 'checked', verdict_text: '未见明显异常' })
    expect(s).toEqual({ kind: 'checked', text: '未见明显异常' })
  })

  it('未知 status 原样透传不造语义', () => {
    const s = visionCardState({ status: 'weird' } as unknown as Ir3VisionCheck)
    expect(s.kind).toBe('skipped')
    expect(s.text).toContain('weird')
  })
})

describe('findingColor', () => {
  it('warning=gold，其余=default', () => {
    expect(findingColor('warning')).toBe('gold')
    expect(findingColor('info')).toBe('default')
    expect(findingColor(undefined)).toBe('default')
  })
})

describe('visionMetaLine / verdictText（缺失不造）', () => {
  it('全缺→null', () => {
    expect(visionMetaLine({ status: 'checked' })).toBeNull()
    expect(verdictText({ status: 'checked' })).toBeNull()
    expect(verdictText({ status: 'checked', verdict_text: '  ' })).toBeNull()
  })

  it('齐全→拼接', () => {
    const line = visionMetaLine({ status: 'checked', model: 'Qwen3-VL', checked_at: '2026-11-26T12:00:00' })
    expect(line).toBe('模型 Qwen3-VL · 判词时间 2026-11-26T12:00:00')
    expect(verdictText({ status: 'checked', verdict_text: '结论' })).toBe('结论')
  })
})
