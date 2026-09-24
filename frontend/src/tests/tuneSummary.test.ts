import { describe, expect, it } from 'vitest'
import type { TuneSession } from '../api/cadAgent'
import { roundStatusLabel, summarizeTunes } from '../pages/session/tuneSummary'

function tune(partial: Partial<TuneSession>): TuneSession {
  return {
    id: 't1',
    design_session_id: 's1',
    status: 'active',
    baseline_ir_version: 1,
    rounds: [],
    ...partial,
  }
}

describe('summarizeTunes', () => {
  it('空/undefined 安全返回未开始', () => {
    for (const input of [undefined, null, []]) {
      const s = summarizeTunes(input as TuneSession[] | null)
      expect(s.active).toBeNull()
      expect(s.latest).toBeNull()
      expect(s.rows).toEqual([])
    }
  })

  it('轮次倒序 + 版本/问答/失败整形', () => {
    const s = summarizeTunes([
      tune({
        status: 'active',
        rounds: [
          { id: 'r1', round_no: 1, baseline_ir_version: 1, prompt: '加厚声学罩', status: 'done', result_ir3_version: 2, llm_trace: { tools: [{}, {}] } },
          { id: 'r2', round_no: 2, baseline_ir_version: 2, prompt: '问：总长多少', status: 'done' },
          { id: 'r3', round_no: 3, baseline_ir_version: 2, prompt: '再加孔阵', status: 'failed', error: '质量门禁连续阻断' },
        ],
      }),
    ])
    expect(s.active?.id).toBe('t1')
    expect(s.rows.map((r) => r.roundNo)).toEqual([3, 2, 1])
    expect(s.rows[2].irVersion).toBe(2)
    expect(s.rows[2].toolCount).toBe(2)
    expect(s.rows[1].isQuestion).toBe(true)
    expect(s.rows[0].failedError).toBe('质量门禁连续阻断')
  })

  it('活跃会话未收敛轮计数（审阅页警示口径）', () => {
    const s = summarizeTunes([
      tune({
        status: 'active',
        rounds: [
          { id: 'r1', round_no: 1, baseline_ir_version: 1, prompt: 'a', status: 'running' },
          { id: 'r2', round_no: 2, baseline_ir_version: 1, prompt: 'b', status: 'awaiting_upload' },
          { id: 'r3', round_no: 3, baseline_ir_version: 1, prompt: 'c', status: 'done' },
        ],
      }),
    ])
    expect(s.activeRoundCount).toBe(2)
  })

  it('无活跃会话时 active 为空', () => {
    const s = summarizeTunes([tune({ status: 'finalized' })])
    expect(s.active).toBeNull()
    expect(s.latest?.status).toBe('finalized')
  })

  it('状态中文标签全覆盖', () => {
    expect(roundStatusLabel('running')).toBe('进行中')
    expect(roundStatusLabel('awaiting_upload')).toBe('待上传')
    expect(roundStatusLabel('done')).toBe('完成')
    expect(roundStatusLabel('failed')).toBe('失败')
  })
})
