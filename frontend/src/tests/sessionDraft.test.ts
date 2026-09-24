import { describe, expect, it } from 'vitest'
import { clearDraft, isDraftMeaningful, loadDraft, saveDraft, type SessionDraft } from '../lib/sessionDraft'

describe('sessionDraft', () => {
  it('保存后可原样读回（带 savedAt）', () => {
    clearDraft()
    saveDraft({ title: '拖曳阵测试', requirement: '设计一套…', kbIds: ['kb1'] })
    const d = loadDraft()
    expect(d?.title).toBe('拖曳阵测试')
    expect(d?.requirement).toBe('设计一套…')
    expect(d?.kbIds).toEqual(['kb1'])
    expect(d?.savedAt).toBeTruthy()
  })

  it('clearDraft 后读不到', () => {
    saveDraft({ title: 'x' })
    clearDraft()
    expect(loadDraft()).toBeNull()
  })

  it('isDraftMeaningful：需求/标题/知识库任一非空即有意义，仅选项目不算', () => {
    expect(isDraftMeaningful(null)).toBe(false)
    expect(isDraftMeaningful({ projectId: 'p1' })).toBe(false)
    expect(isDraftMeaningful({ title: '标题' })).toBe(true)
    expect(isDraftMeaningful({ requirement: '  ' })).toBe(false)
    expect(isDraftMeaningful({ kbIds: ['k'] })).toBe(true)
  })

  it('坏 JSON 不抛错，按无草稿处理', () => {
    localStorage.setItem('sonarcad.new-session.draft', '{oops')
    expect(loadDraft()).toBeNull()
  })
})

describe('draft 类型兼容 SessionDraft', () => {
  it('最小对象合法', () => {
    const d: SessionDraft = { savedAt: new Date().toISOString() }
    expect(d.savedAt).toBeTruthy()
  })
})
