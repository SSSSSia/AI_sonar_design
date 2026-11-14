// T15.2/T15.3：paramMapping 展示文案纯函数用例（对齐 referenceUsed.test.ts 先例）。
import { describe, expect, it } from 'vitest'
import {
  formatDisplayTransforms,
  formatMappedObjectParams,
  formatParamMapping,
} from '../pages/session/paramMapping'
import type { Ir3DisplayTransform, Ir3ParamMapping } from '../api/workflow'

const pm = (objects: Ir3ParamMapping['objects']): Ir3ParamMapping => ({
  version: '1',
  identity_basis: 'declared_objects',
  objects,
})

describe('formatParamMapping', () => {
  it('旧快照无字段 → null（不渲染该行）', () => {
    expect(formatParamMapping(undefined)).toBeNull()
    expect(formatParamMapping({ version: '1', identity_basis: 'declared_objects', objects: [] })).toBeNull()
  })

  it('三态计数汇总', () => {
    const line = formatParamMapping(pm([
      { object_id: 'a', role: 'r', feature_id: 'F-001', status: 'verified', basis: 'declared_tag', params: [] },
      { object_id: 'b', role: 'r', feature_id: null, status: 'ambiguous', params: [] },
      { object_id: 'c', role: 'r', feature_id: null, status: 'unmatched', params: [] },
      { object_id: 'd', role: 'r', feature_id: 'F-002', status: 'verified', basis: 'symmetric_class', symmetric_size: 16, params: [] },
    ]))
    expect(line).toBe('对象映射：4 个声明对象 · 2 已验证 · 1 歧义 · 1 未匹配（声明制身份 v1）')
  })

  it('未知 status 降级为原文（A-16 不修饰）', () => {
    const line = formatParamMapping(pm([
      { object_id: 'a', role: 'r', feature_id: null, status: 'weird' as never, params: [] },
    ]))
    expect(line).toBe('对象映射：1 个声明对象 · 0 已验证 · 0 歧义 · 0 未匹配（声明制身份 v1）')
  })
})

describe('formatMappedObjectParams', () => {
  it('verified 对象输出实测摘要', () => {
    const s = formatMappedObjectParams({
      object_id: 'array_section', role: 'array_section', status: 'verified',
      params: [
        { param_id: 'P-001', dim_type: 'diameter', measured_mm: 27, measurement: 'radius_derived' },
        { param_id: 'P-004', dim_type: 'length', measured_mm: 24000, measurement: 'bbox_axis' },
      ],
    })
    expect(s).toBe('array_section（array_section）：孔径 27 mm、长度 24000 mm')
  })

  it('非 verified / 无参数 → null', () => {
    expect(formatMappedObjectParams({
      object_id: 'x', role: 'r', status: 'ambiguous', params: [],
    })).toBeNull()
    expect(formatMappedObjectParams({
      object_id: 'y', role: 'r', status: 'verified',
      params: [{ param_id: 'P', dim_type: 'thickness', measured_mm: null, measurement: 'none' }],
    })).toBe('y（r）：壁厚 未测')
  })
})

describe('formatDisplayTransforms', () => {
  it('旧快照无字段/空数组 → null', () => {
    expect(formatDisplayTransforms(undefined)).toBeNull()
    expect(formatDisplayTransforms([])).toBeNull()
  })

  it('有参数关联 + 规则', () => {
    const t: Ir3DisplayTransform[] = [
      { param_id: 'P-AMP', physical_mm: 1500, visual_mm: 450, rule: 'min(doc_amplitude,radius*40)' },
    ]
    expect(formatDisplayTransforms(t)).toBe(
      '显示变换：参数 P-AMP 物理 1500 → 视觉 450 mm，规则 min(doc_amplitude,radius*40)',
    )
  })

  it('无关联参数多条以「；」相连', () => {
    const t: Ir3DisplayTransform[] = [
      { param_id: null, physical_mm: 1500, visual_mm: 400 },
      { param_id: null, physical_mm: -1500, visual_mm: -400 },
    ]
    expect(formatDisplayTransforms(t)).toBe(
      '显示变换：（无关联参数） 物理 1500 → 视觉 400 mm；（无关联参数） 物理 -1500 → 视觉 -400 mm',
    )
  })
})
