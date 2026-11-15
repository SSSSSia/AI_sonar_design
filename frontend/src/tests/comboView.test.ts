// T15.6/C-2：comboView 组合卡纯函数用例（对齐 paramMapping.test.ts 先例）。
import { describe, expect, it } from 'vitest'
import {
  aggregateUnresolved,
  buildVersionList,
  diffIr2Params,
  latestApprovedGate2,
  parseReviewMetadata,
} from '../pages/session/comboView'
import type { Ir1, Ir2Param, Ir4, ReviewRecord } from '../api/workflow'

const meta = (overrides: Record<string, unknown> = {}) =>
  JSON.stringify({ contract: 'ADR-014§5', policy_version: '1', ...overrides })

const record = (over: Partial<ReviewRecord>): ReviewRecord => ({
  id: 'r1',
  gate: 'gate2',
  decision: 'approved',
  annotations: [],
  decided_at: '2026-11-15T10:00:00+08:00',
  ...over,
})

const COMBO = {
  IR2: { snapshot_id: 'ir2-2', version: 2, content_sha256: 'a'.repeat(64), valid_at_decision: 't0' },
  IR3: { snapshot_id: 'ir3-3', version: 3, content_sha256: 'b'.repeat(64) },
  IR4: { snapshot_id: 'ir4-1', version: 1, content_sha256: 'c'.repeat(64) },
}

describe('parseReviewMetadata / latestApprovedGate2', () => {
  it('metadata 缺失/空串/坏 JSON → null（组合契约前历史记录诚实降级）', () => {
    expect(parseReviewMetadata(record({ metadata: undefined }))).toBeNull()
    expect(parseReviewMetadata(record({ metadata: '' }))).toBeNull()
    expect(parseReviewMetadata(record({ metadata: 'not-json' }))).toBeNull()
  })

  it('取最近一次 gate2 批准（列表 decided_at DESC，取首个）；跳过驳回/其他关/无凭证记录', () => {
    const reviews = [
      record({ id: 'r-latest', metadata: meta({ snapshot_combo: COMBO }) }),
      record({ id: 'r-rej', decision: 'rejected', metadata: meta({ snapshot_combo: COMBO }) }),
      record({ id: 'r-g1', gate: 'gate1', metadata: meta({ snapshot_combo: COMBO }) }),
      record({ id: 'r-nometa' }),
      record({ id: 'r-older', metadata: meta({ snapshot_combo: COMBO }) }),
    ]
    const combo = latestApprovedGate2(reviews)
    expect(combo?.record.id).toBe('r-latest')
    expect(combo?.metadata.snapshot_combo?.IR2?.version).toBe(2)
  })

  it('无任何可用凭证 → null（组合卡整体隐藏）', () => {
    expect(latestApprovedGate2([record({ decision: 'rejected' }), record({ id: 'r2', gate: 'gate1' })])).toBeNull()
    expect(latestApprovedGate2(undefined)).toBeNull()
  })
})

describe('buildVersionList', () => {
  it('组合版本 vs 当前 latest 对照与钉定状态', () => {
    const rows = buildVersionList(COMBO, { IR2: 4, IR3: 3, IR4: 1 })
    expect(rows.map((r) => [r.ir_type, r.combo_version, r.latest_version, r.pinned_is_latest])).toEqual([
      ['IR2', 2, 4, false],
      ['IR3', 3, 3, true],
      ['IR4', 1, 1, true],
    ])
    expect(rows[0].sha16).toBe('a'.repeat(16))
  })

  it('缺组合条目/缺 latest → null 状态（不做判定）', () => {
    const rows = buildVersionList({ IR2: COMBO.IR2 }, {})
    expect(rows[0].pinned_is_latest).toBeNull()
    expect(rows[1].combo_version).toBeNull()
    expect(rows[1].pinned_is_latest).toBeNull()
  })
})

describe('aggregateUnresolved', () => {
  const ir4: Ir4 = {
    overall: 'incomplete',
    issues: [
      { constraint_id: 'CHK-SEM-DIAMETER', severity: 'unverified', related_params: [], related_features: [] },
      { constraint_id: 'CHK-SEM-DIAMETER', severity: 'unverified', related_params: [], related_features: [] },
      { constraint_id: 'CHK-FORMULA-1', severity: 'pass' as never, related_params: [], related_features: [] },
    ],
  }
  const ir1 = { missing_items: ['耐压指标缺失'], objectives: [], sub_queries: [], retrieved_context: [] } as unknown as Ir1

  it('凭证内 + 现场 IR4 unverified + IR1 missing 合并去重', () => {
    const items = aggregateUnresolved(
      { unresolved: [{ source: 'IR4', ref: 'CHK-SEM-DIAMETER' }, { source: 'IR1', ref: '旧缺失项' }] },
      ir4, ir1,
    )
    expect(items).toEqual([
      { source: 'IR4', ref: 'CHK-SEM-DIAMETER', from_record: true },
      { source: 'IR1', ref: '旧缺失项', from_record: true },
      { source: 'IR1', ref: '耐压指标缺失', from_record: false },
    ])
  })

  it('无凭证（旧记录）也能聚合现场未决项', () => {
    const items = aggregateUnresolved(undefined, ir4, ir1)
    expect(items.map((i) => `${i.source}:${i.ref}`)).toEqual(['IR4:CHK-SEM-DIAMETER', 'IR1:耐压指标缺失'])
  })
})

describe('diffIr2Params', () => {
  const p = (id: string, value: number | string, unit = 'mm'): Ir2Param =>
    ({ id, name: `参数${id}`, value, unit }) as Ir2Param

  it('四态：一致/变更（值与单位）/批准后新增/批准后移除', () => {
    const rows = diffIr2Params(
      [p('P-001', 27), p('P-002', 24, 'm'), p('P-003', 100)],
      [p('P-001', 27), p('P-002', 30, 'm'), p('P-004', 5)],
    )
    const byId = Object.fromEntries(rows.map((r) => [r.param_id, r]))
    expect(byId['P-001'].state).toBe('same')
    expect(byId['P-002'].state).toBe('changed')
    expect(byId['P-002'].latest_value).toBe('30 m')
    expect(byId['P-003'].state).toBe('removed')
    expect(byId['P-004'].state).toBe('added')
  })

  it('空基准 → 全部按批准后新增呈现（不崩溃）', () => {
    const rows = diffIr2Params(undefined, [p('P-001', 27)])
    expect(rows).toHaveLength(1)
    expect(rows[0].state).toBe('added')
    expect(rows[0].combo_value).toBe('—')
  })
})
