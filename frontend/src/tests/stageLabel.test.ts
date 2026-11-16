// T15.5：IR4 检查码中文标签用例（语义检查器族 CHK-SEM 补全，修裸码显示）。
import { describe, expect, it } from 'vitest'
import { checkLabel } from '../lib/stageLabel'

describe('checkLabel CHK-SEM 族', () => {
  it('语义检查器族四码 → 中文（T15.3/T15.5）', () => {
    expect(checkLabel('CHK-SEM-LENGTH')).toBe('长度语义核对')
    expect(checkLabel('CHK-SEM-DIAMETER')).toBe('孔径语义核对')
    expect(checkLabel('CHK-SEM-THICKNESS')).toBe('壁厚语义核对')
    expect(checkLabel('CHK-SEM-MAPPING')).toBe('映射未决（歧义/未匹配）')
  })

  it('存量 CHK-* 码不回潮', () => {
    expect(checkLabel('CHK-GEO-LINK')).toBe('特征未关联参数')
    expect(checkLabel('CHK-STD-VIOLATION')).toBe('军标条款违规')
  })

  it('未知码原样透出（A-16 不修饰，新增检查码无 UI 风险）', () => {
    expect(checkLabel('CHK-SEM-FUTURE')).toBe('CHK-SEM-FUTURE')
    expect(checkLabel('CHK-OTHER-UNKNOWN')).toBe('CHK-OTHER-UNKNOWN')
  })
})
