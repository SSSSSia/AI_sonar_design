// T18.6 gate2 裁决材料呈现纯函数（ADR-017：参考信号·非判分依据；诚实三态 A-16）
import type { Ir3VisionCheck } from '../../api/workflow'

export type VisionCardState = {
  kind: 'off' | 'skipped' | 'checked'
  text: string
}

/** 三态映射：无字段=未启用（关闭态如实）；skipped 带原因；checked。未知 status 原样透传不造。 */
export function visionCardState(vc?: Ir3VisionCheck): VisionCardState {
  if (!vc || !vc.status) {
    return { kind: 'off', text: '未启用视觉校验（CAD_VISION_CHECK 默认关）——审阅以数值校验与三维预览为准' }
  }
  if (vc.status === 'skipped') {
    return { kind: 'skipped', text: `视觉校验未完成：${vc.detail ?? '未知原因'}` }
  }
  if (vc.status === 'checked') {
    return { kind: 'checked', text: vc.verdict_text ?? '' }
  }
  // 未知 status（schema enum 之外，理论不可达）：原样透传不造语义
  return { kind: 'skipped', text: `视觉校验状态异常：${String(vc.status)}` }
}

/** findings 级别色（warning=gold 提示可核对；info=灰）。 */
export function findingColor(severity: string | undefined): 'gold' | 'default' {
  return severity === 'warning' ? 'gold' : 'default'
}

/** 元信息条：模型/时间，缺失不造（A-16）。 */
export function visionMetaLine(vc: Ir3VisionCheck): string | null {
  const parts: string[] = []
  if (vc.model) parts.push(`模型 ${vc.model}`)
  if (vc.checked_at) parts.push(`判词时间 ${vc.checked_at}`)
  return parts.length ? parts.join(' · ') : null
}

/** 判词全文（checked 必有；缺失返回 null 不造空段）。 */
export function verdictText(vc: Ir3VisionCheck): string | null {
  const t = (vc.verdict_text ?? '').trim()
  return t ? t : null
}
