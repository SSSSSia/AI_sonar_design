import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { App, Alert, Button, Card, Grid, Input, Modal, Radio, Select, Space, Table, Tag, Tooltip, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { postReview, type Ir3, type Ir4, type Ir4Issue, type Ir5, type ReviewRecord } from '../../api/workflow'
import { ApiError, getBlob } from '../../api/client'
import { listBases, type KbBase } from '../../api/kb'
import { hasAnyRole, useAuthStore } from '../../stores/authStore'
import { ReviewAnnotation } from '../../components/ReviewAnnotation'
import { CandidateContent, CandidateDedupTag, CandidateKindTag, writebackToastText } from '../../components/CandidateContent'
import { gateLabel, WRITEBACK_LABEL, checkLabel } from '../../lib/stageLabel'
import EChart, { CHART_COLORS } from '../../components/EChart'
import type { EChartsCoreOption } from 'echarts/core'

const SEVERITY_META: Record<string, { label: string; color: string }> = {
  error: { label: '错误', color: 'error' },
  warning: { label: '警告', color: 'warning' },
  unverified: { label: '未验证', color: 'default' },
  info: { label: '提示', color: 'default' },
}

/** CSR 口径（T9.2，与 PHASE-009 §2.2 及 A4 agent 注释同文案） */
const CSR_TOOLTIP =
  'CSR（约束满足率）= 通过的约束检查数 ÷（通过 + 违反）× 100。' +
  '计数对象为 A4 确定性检查：公式复算 / 军标核对 / 几何数值复核；' +
  'warning（存疑）与 info（提示）不计入分母；' +
  '无任何有效检查时显示「—」（不显示 0% 或 100%）。'

/** 覆盖率口径（T14.4，与 A4 agent coverage 注释同文案） */
const COVERAGE_TOOLTIP =
  '验证覆盖率 = 已产出确定性判定的必检项数 ÷ 适用必检项总数 × 100。' +
  '必检项 = 公式参数复算 + 适用军标表达式条目 + 已联动几何期望比对；' +
  '跳过/未验证项计入本指标分母（与 CSR 分母口径不同，两者并列互补）。'

/** overall 三态徽标（T14.4）：incomplete=应检未检，不得显示为绿色通过 */
function OVERALL_TAG({ overall }: { overall: Ir4['overall'] }) {
  if (overall === 'fail') return <Tag color="error">总体结论 fail</Tag>
  if (overall === 'incomplete') return <Tag color="warning">总体结论 incomplete（应检未检）</Tag>
  return <Tag color="success">总体结论 pass</Tag>
}

/** 四级风险横条分布（ADR-011）：IR4 真实 issues 聚合；四级全列出，0 值如实显示为 0 */
function severityChartOption(issues: Ir4Issue[]): EChartsCoreOption {
  // y 轴类目自下而上渲染：错误置于顶部
  const order: [keyof typeof SEV_COLOR, string][] = [
    ['info', '提示'],
    ['unverified', '未验证'],
    ['warning', '警告'],
    ['error', '错误'],
  ]
  const count = (sev: string) => issues.filter((i) => i.severity === sev).length
  return {
    grid: { left: 56, right: 34, top: 10, bottom: 24 },
    xAxis: {
      type: 'value', minInterval: 1,
      splitLine: { lineStyle: { color: CHART_COLORS.split } }, axisLabel: { color: CHART_COLORS.text3 },
    },
    yAxis: {
      type: 'category', data: order.map(([, label]) => label),
      axisLabel: { color: CHART_COLORS.text2 }, axisLine: { lineStyle: { color: CHART_COLORS.split } },
    },
    series: [{
      type: 'bar', barWidth: 14,
      data: order.map(([sev]) => ({
        value: count(sev),
        itemStyle: { color: SEV_COLOR[sev], borderRadius: [0, 4, 4, 0] },
      })),
      label: { show: true, position: 'right', color: CHART_COLORS.text2 },
    }],
  }
}

const SEV_COLOR = {
  error: CHART_COLORS.err,
  warning: CHART_COLORS.warn,
  unverified: CHART_COLORS.text3,
  info: CHART_COLORS.info,
} as const

export function ReportTab({
  sessionId,
  ir4,
  ir4Version,
  ir5,
  ir5Version,
  ir3,
  reviews,
  pipelineStatus,
  ir2Empty,
  onDecided,
}: {
  sessionId: string
  ir4: Ir4 | null
  ir4Version?: number
  ir5: Ir5 | null
  ir5Version?: number
  ir3: Ir3 | null
  reviews: ReviewRecord[]
  pipelineStatus?: string
  /** IR2 为 0 参数（降级生成）——A4 的 pass 是空转，需要显式提示避免误导 */
  ir2Empty?: boolean
  onDecided: () => void
}) {
  const { message } = App.useApp()
  const screens = Grid.useBreakpoint()
  const roles = useAuthStore((s) => s.roles)
  const canDecide = hasAnyRole(roles, ['admin', 'reviewer'])
  const [annotations, setAnnotations] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [targetKbId, setTargetKbId] = useState<string | undefined>(undefined)

  const basesQuery = useQuery({
    queryKey: ['kb-bases'],
    queryFn: listBases,
    enabled: ir5?.writeback_status === 'pending' || ir5?.writeback_status === 'skipped',
  })
  const bases: KbBase[] = basesQuery.data?.bases ?? []

  const wbStatus = ir5?.writeback_status
  const knowledgePending = wbStatus === 'pending'
  const knowledgeSkipped = wbStatus === 'skipped'
  const newCandidates = ir5?.candidates.filter((c) => c.dedup_status === 'new') ?? []
  const dupCount = ir5 ? ir5.candidates.length - newCandidates.length : 0
  const allDuplicates = !!ir5 && ir5.candidates.length > 0 && newCandidates.length === 0

  function confirmWrite() {
    const target = bases.find((b) => b.id === targetKbId)
    Modal.confirm({
      title: allDuplicates
        ? `确认本会话无新增知识（${dupCount} 条重复）？`
        : `确认写入 ${newCandidates.length} 条知识候选${target ? `到「${target.name}」` : ''}？`,
      content: allDuplicates
        ? '候选均与库内已有知识重复，批准后不会写入任何内容，仅记录本次确认归档。也可选「全部跳过」直接关闭。'
        : `写入知识图谱后成为共享知识（${target ? `归属「${target.name}」` : '归属会话首选知识库'}），后续新会话即可复用；${dupCount > 0 ? `${dupCount} 条重复项自动放弃（不会重复写入）。` : ''}驳回则本会话候选不入图。`,
      okText: allDuplicates ? '确认归档' : '批准写入',
      cancelText: '再想想',
      onOk: () => decideKnowledge('approved'),
    })
  }

  async function decideKnowledge(decision: 'approved' | 'rejected') {
    setSubmitting(true)
    try {
      const effects = await postReview(sessionId, 'knowledge', {
        decision,
        annotations: annotations.trim() ? [annotations.trim()] : [],
        ...(decision === 'approved' && targetKbId ? { kbId: targetKbId } : {}),
      })
      if (decision === 'rejected') message.success('本次知识沉淀已跳过——候选不入库，会话流程结束')
      else if (effects.writeback === 'failed') message.warning(writebackToastText(effects.writeback))
      else message.success(effects.writeback ? writebackToastText(effects.writeback) : '知识归档完成')
      onDecided()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '裁决提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  // T11.5 军标格式 Word 导出（P2-11）：IR2 三章渲染，docx 二进制下载
  const [exporting, setExporting] = useState(false)
  async function exportDocx() {
    setExporting(true)
    try {
      const { blob, disposition } = await getBlob(`/cad/sessions/${sessionId}/export/docx`)
      const m = /filename\*=UTF-8''([^;]+)/.exec(disposition ?? '')
      const filename = m ? decodeURIComponent(m[1]) : `设计方案_${sessionId}.docx`
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
      message.success('已导出 Word 文档（内置模板；正式军标模板待授权后替换）')
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '导出失败')
    } finally {
      setExporting(false)
    }
  }

  // T13.5 军标格式 PDF 导出（§5.2）：docx 模板链复用 → gotenberg(LibreOffice) 转换；未部署 503 如实
  const [exportingPdf, setExportingPdf] = useState(false)
  async function exportPdf() {
    setExportingPdf(true)
    try {
      const { blob, disposition } = await getBlob(`/cad/sessions/${sessionId}/export/pdf`)
      const m = /filename\*=UTF-8''([^;]+)/.exec(disposition ?? '')
      const filename = m ? decodeURIComponent(m[1]) : `设计方案_${sessionId}.pdf`
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
      message.success('已导出 PDF 文档（内置模板；正式军标模板待授权后替换）')
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '导出失败')
    } finally {
      setExportingPdf(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Card
        title={<>IR4 合规校验报告 {ir4Version ? <Typography.Text code style={{ fontSize: 12 }}>v{ir4Version}</Typography.Text> : null}</>}
        extra={
          <Space size={8}>
            {ir4 ? <OVERALL_TAG overall={ir4.overall} /> : null}
            <Button size="small" loading={exporting} onClick={() => void exportDocx()}>导出 Word</Button>
            <Button size="small" loading={exportingPdf} onClick={() => void exportPdf()}>导出 PDF</Button>
          </Space>
        }
      >
        {!ir4 ? (
          <Typography.Text type="secondary">IR4 尚未生成</Typography.Text>
        ) : (
          <>
            {ir2Empty && (
              <Alert
                type="warning" showIcon style={{ marginBottom: 12 }}
                message="本次校验基于空参数方案"
                description="IR2 参数方案为 0 项（LLM 不可用时降级生成），A4 无实质检查项——总体结论为 incomplete（应检未检），不代表设计合规。建议到「审阅与参数」页签驳回并回退 A2，待 LLM 恢复后重算。"
              />
            )}
            <div style={{ marginBottom: 12, display: 'flex', alignItems: 'baseline', gap: 16, flexWrap: 'wrap' }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <Typography.Text type="secondary">CSR 约束满足率</Typography.Text>
                <Tooltip title={CSR_TOOLTIP}>
                  <Typography.Text strong style={{ fontSize: 22, cursor: 'help' }}>
                    {ir4.csr == null ? '—' : `${ir4.csr.toFixed(2)}%`}
                  </Typography.Text>
                </Tooltip>
                {ir4.csr == null && (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    （无有效检查项，A-16 不显示 0%/100%）
                  </Typography.Text>
                )}
              </div>
              {ir4.coverage != null && (
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                  <Typography.Text type="secondary">验证覆盖率</Typography.Text>
                  <Tooltip title={COVERAGE_TOOLTIP}>
                    <Typography.Text strong style={{ fontSize: 22, cursor: 'help' }}>
                      {ir4.coverage.ratio == null ? '—' : `${ir4.coverage.ratio.toFixed(2)}%`}
                    </Typography.Text>
                  </Tooltip>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    （{ir4.coverage.checked}/{ir4.coverage.applicable} 必检项）
                  </Typography.Text>
                </div>
              )}
            </div>
            {ir4.issues.length > 0 ? (
              <div style={{ display: 'grid', gridTemplateColumns: screens.md ? 'minmax(280px, 340px) 1fr' : '1fr', gap: 16, alignItems: 'start' }}>
                <Card
                  type="inner" size="small" title="风险等级分布"
                  styles={{ body: { paddingTop: 4 } }}
                >
                  <EChart option={severityChartOption(ir4.issues)} height={Math.max(140, ir4.issues.length * 24)} />
                </Card>
                <div style={{ minWidth: 0 }}>
                  <IssueGroups issues={ir4.issues} />
                </div>
              </div>
            ) : (
              <IssueGroups issues={ir4.issues} />
            )}
          </>
        )}
      </Card>

      {ir5 && (
        <Card
          title={<>IR5 知识沉淀候选 {ir5Version ? <Typography.Text code style={{ fontSize: 12 }}>v{ir5Version}</Typography.Text> : null}</>}
          extra={<Tag color={wbStatus === 'written' ? 'success' : wbStatus === 'skipped' ? 'default' : 'warning'}>{WRITEBACK_LABEL[wbStatus ?? ''] ?? wbStatus}</Tag>}
          styles={{ body: { padding: 0 } }}
        >
          <CandidateTable candidates={ir5.candidates} ir3={ir3} />
          <div style={{ padding: '10px 16px', borderTop: '1px solid var(--hairline)' }}>
            <Alert
              type="info"
              showIcon
              message={
                knowledgeSkipped
                  ? '本次沉淀已跳过：候选未写入图谱（候选如实保留）。如需补做，可直接「批准写入」，批准后照常入图。'
                  : pipelineStatus === 'completed' && knowledgePending
                    ? '管线已完成，知识沉淀确认可随时补做（完成前未确认的候选不会自动入图）'
                    : '知识审阅关（A-10）：未经人工确认不会写入图谱；重复项不静默覆盖。'
              }
              description="沉淀作用：过审方案提炼为知识（参数关系 / 设计案例 / 约束经验）写入图谱后，后续会话的 A1 检索与 A2 参数参考即可复用——系统随使用积累越用越准。"
              style={{ marginBottom: 10 }}
            />
            {knowledgePending && allDuplicates && (
              <Alert
                type="info" showIcon style={{ marginBottom: 10 }}
                message={`本会话 ${dupCount} 条候选均与知识库已有内容重复`}
                description="A5 会把本会话知识与库内（图谱+语料）自动比对：全部重复说明方案参数均源自现有库、未产生新知识，属正常结果。可「全部跳过」直接关闭本次沉淀（推荐），「确认归档」也不会重复写入任何内容。"
              />
            )}
            {(knowledgePending || knowledgeSkipped) && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12, alignItems: 'start' }}>
                <div>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>裁决</Typography.Text>
                  <div style={{ marginTop: 6 }}>
                    <Radio checked disabled>
                      {allDuplicates
                        ? `无新增知识（${dupCount} 条候选均与库内重复）`
                        : `确认 ${newCandidates.length} 条新增知识入图${dupCount > 0 ? `（另 ${dupCount} 条重复自动放弃）` : ''}`}
                    </Radio>
                  </div>
                </div>
                <div>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    写入的知识归属哪个库
                    <span style={{ fontSize: 11, marginLeft: 6 }}>（默认会话首选库；后续可被选该库的会话复用）</span>
                  </Typography.Text>
                  <Select
                    style={{ marginTop: 6, width: '100%' }}
                    allowClear
                    placeholder="不选 = 会话首选知识库（未选库则默认库）"
                    loading={basesQuery.isLoading}
                    value={targetKbId}
                    onChange={(v) => setTargetKbId(v)}
                    options={bases.map((b) => ({ value: b.id, label: b.is_default ? `${b.name}（默认）` : b.name }))}
                  />
                  <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 10 }}>批注（可选，留痕到裁决记录）</Typography.Text>
                  <Input.TextArea style={{ marginTop: 6 }} rows={2} value={annotations} onChange={(e) => setAnnotations(e.target.value)} placeholder="确认/放弃依据…" />
                  <SpaceForButtons>
                    <Button size="small" type="primary" disabled={!canDecide} loading={submitting} onClick={confirmWrite}>
                      {allDuplicates ? '确认归档' : '批准写入'}
                    </Button>
                    {knowledgePending && (
                      <Button size="small" disabled={!canDecide} loading={submitting} onClick={() => decideKnowledge('rejected')}>全部跳过</Button>
                    )}
                  </SpaceForButtons>
                </div>
              </div>
            )}
          </div>
        </Card>
      )}

      {reviews.length > 0 && (
        <Card title="裁决历史" size="small" styles={{ body: { padding: 0 } }}>
          <Table<ReviewRecord>
            rowKey="id" size="small" dataSource={reviews} pagination={false}
            columns={[
              { title: '审阅关', dataIndex: 'gate', width: 110, render: (v) => gateLabel(v) },
              { title: '裁决', dataIndex: 'decision', width: 90, render: (v) => <Tag color={v === 'approved' ? 'success' : 'error'}>{v === 'approved' ? '批准' : '驳回'}</Tag> },
              {
                title: '审阅人', key: 'reviewer', width: 110,
                render: (_, r) => <span style={{ fontSize: 12 }}>{r.reviewer_name || (r.reviewer_id ? `${r.reviewer_id.slice(0, 8)}…` : '—')}</span>,
              },
              { title: '批注', key: 'a', render: (_, r) => <ReviewAnnotation record={r} /> },
              { title: '时间', dataIndex: 'decided_at', width: 170, render: (v) => <span style={{ fontSize: 12 }}>{String(v).replace('T', ' ').slice(0, 19)}</span> },
            ]}
          />
        </Card>
      )}
    </div>
  )
}

function SpaceForButtons({ children }: { children: React.ReactNode }) {
  return <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>{children}</div>
}

function IssueGroups({ issues }: { issues: Ir4Issue[] }) {
  const order: Ir4Issue['severity'][] = ['error', 'warning', 'unverified', 'info']
  const groups = order
    .map((sev) => ({ sev, items: issues.filter((i) => i.severity === sev) }))
    .filter((g) => g.items.length > 0)
  if (issues.length === 0) {
    return <Typography.Text type="secondary">无问题项——各项检查通过。</Typography.Text>
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {groups.map((g) => (
        <div key={g.sev}>
          <Typography.Text strong style={{ fontSize: 12.5 }}>
            {SEVERITY_META[g.sev].label}（{g.items.length}）
          </Typography.Text>
          <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {g.items.map((i) => (
              <div key={i.constraint_id} style={{ border: '1px solid var(--border)', borderLeft: `3px solid ${g.sev === 'error' ? '#ef4444' : g.sev === 'warning' ? '#f59e0b' : g.sev === 'unverified' ? '#94a3b8' : '#475569'}`, borderRadius: 6, padding: '8px 12px' }}>
                <div style={{ fontSize: 12.5 }}>
                  <Tag color={SEVERITY_META[g.sev].color} style={{ marginInlineEnd: 6 }}>{checkLabel(i.constraint_id)}</Tag>
                  <span className="mono" style={{ fontSize: 10.5, color: 'var(--text-3)', marginRight: 6 }}>{i.constraint_id}</span>
                  {i.suggestion && <Tag color="warning">建议 {i.suggestion.replace('back_to_', '回退 ')}</Tag>}
                </div>
                {i.message && <div style={{ fontSize: 12, color: 'var(--text-2)', marginTop: 4, lineHeight: 1.7 }}>{i.message}</div>}
                {(() => {
                  // 关联参数/特征去重 + 超量截断（审查 P2-16：避免同名重复列举占行）
                  const cap = (arr: string[]) => {
                    const uniq = [...new Set(arr.filter(Boolean))]
                    return uniq.length > 6 ? `${uniq.slice(0, 6).join('、')} 等 ${uniq.length} 项` : uniq.join('、')
                  }
                  const params = cap(i.related_params ?? [])
                  const feats = cap(i.related_features ?? [])
                  if (!params && !feats) return null
                  return (
                    <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 4, fontFamily: 'monospace' }}>
                      {[params && `参数 ${params}`, feats && `特征 ${feats}`].filter(Boolean).join(' · ')}
                    </div>
                  )
                })()}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

function CandidateTable({ candidates, ir3 }: { candidates: Ir5['candidates']; ir3: Ir3 | null }) {
  const columns: ColumnsType<Ir5['candidates'][number]> = [
    { title: '类型', dataIndex: 'type', width: 80, render: (v) => <CandidateKindTag type={v} /> },
    {
      title: '内容',
      key: 'payload',
      render: (_, r) => <CandidateContent candidate={r} />,
    },
    {
      title: '去重',
      dataIndex: 'dedup_status',
      width: 90,
      render: (v) => <CandidateDedupTag status={v} />,
    },
  ]
  return (
    <Table
      rowKey={(_, i) => String(i)}
      size="small"
      dataSource={candidates}
      columns={columns}
      pagination={false}
      locale={{ emptyText: ir3 ? '本会话无候选知识' : 'IR5 尚未生成' }}
    />
  )
}
