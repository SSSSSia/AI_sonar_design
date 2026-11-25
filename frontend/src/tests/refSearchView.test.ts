import { describe, expect, it } from 'vitest'
import { formatPaths, isGraphEntity, PATH_LABEL } from '../pages/kb/refSearchView'

/** T17.6 检索呈现纯函数（ADR-016 §2/§3 契约：单路级不造路名，A-16）。 */
describe('formatPaths', () => {
  it('fusion paths → 中文串', () => {
    expect(formatPaths(['bm25', 'vector'])).toBe('稀疏+向量')
    expect(formatPaths(['bm25', 'vector', 'graph'])).toBe('稀疏+向量+图谱')
  })

  it('single path renders too (单路 bm25 级原始分直出)', () => {
    expect(formatPaths(['bm25'])).toBe('稀疏')
  })

  it('non-fusion levels (null/undefined/empty) → null 不造', () => {
    expect(formatPaths(null)).toBeNull()
    expect(formatPaths(undefined)).toBeNull()
    expect(formatPaths([])).toBeNull()
  })

  it('unknown path name passes through (向前兼容)', () => {
    expect(formatPaths(['sparse_v2' as string])).toBe('sparse_v2')
    expect(Object.keys(PATH_LABEL)).toEqual(['bm25', 'vector', 'graph'])
  })
})

describe('isGraphEntity', () => {
  it('kind=graph → true', () => {
    expect(isGraphEntity('graph')).toBe(true)
  })

  it('chunk items (no kind / other) → false', () => {
    expect(isGraphEntity(null)).toBe(false)
    expect(isGraphEntity(undefined)).toBe(false)
    expect(isGraphEntity('chunk')).toBe(false)
  })
})
