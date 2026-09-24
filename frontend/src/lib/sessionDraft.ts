/**
 * 新建会话草稿（2026-09-24 用户反馈：填一半跳走再回来就没了）。
 * localStorage 持久化——打字即存（防刷新/误退/关页），提交成功即清。
 * 面向表单值原样存取，不做字段级校验（校验归 Form）。
 */

export interface SessionDraft {
  projectId?: string
  title?: string
  kbIds?: string[]
  requirement?: string
  savedAt: string // ISO 时间
}

const KEY = 'sonarcad.new-session.draft'

export function loadDraft(): SessionDraft | null {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return null
    const d = JSON.parse(raw) as SessionDraft
    return typeof d === 'object' && d !== null ? d : null
  } catch {
    return null
  }
}

export function saveDraft(d: Omit<SessionDraft, 'savedAt'>): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...d, savedAt: new Date().toISOString() }))
  } catch {
    /* 存储不可用（隐私模式等）→ 草稿功能静默降级，不阻塞填表 */
  }
}

export function clearDraft(): void {
  try {
    localStorage.removeItem(KEY)
  } catch {
    /* 同上 */
  }
}

/** 是否值得二次确认：需求/标题任一非空，或选了知识库。仅选项目不算（零成本重选）。 */
export function isDraftMeaningful(d: Omit<SessionDraft, 'savedAt'> | null): boolean {
  if (!d) return false
  return !!(d.title?.trim() || d.requirement?.trim() || d.kbIds?.length)
}
