import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { Alert, App as AntdApp, Badge, Button, Card, Tabs, Tag, Typography } from 'antd'
import { PageHeader } from '../../components/PageHeader'
import dayjs from 'dayjs'
import { getSession, fireEvent } from '../../api/sessions'
import { ApiError } from '../../api/client'
import { getIr, listReviews, parseIr, type Ir1, type Ir2, type Ir3, type Ir4, type Ir5 } from '../../api/workflow'
import { useSessionSockets } from '../../hooks/useSessionSockets'
import { PipelineTimeline, buildTimeline } from '../../components/PipelineTimeline'
import { stageLabel, statusTag, fmtTime, normalizeStage } from '../../lib/stageLabel'
import { hasAnyRole, useAuthStore } from '../../stores/authStore'
import { useProgressStore } from '../../stores/progressStore'
import { OverviewTab } from './OverviewTab'
import { ReviewTab } from './ReviewTab'
import { ReportTab } from './ReportTab'
import { ModelTab } from './ModelTab'

/** 会话工作台：总览 / 审阅与参数 / 校验报告 / 三维与交付物（T4.3 交付）。 */
export default function SessionPage() {
  const { id } = useParams<{ id: string }>()
  const queryClient = useQueryClient()
  const { message } = AntdApp.useApp()
  const roles = useAuthStore((s) => s.roles)
  const canRetry = hasAnyRole(roles, ['admin', 'designer'])
  const [retrying, setRetrying] = useState(false)
  const [activeTab, setActiveTab] = useState('overview')

  const sessionQuery = useQuery({
    queryKey: ['session', id],
    queryFn: () => getSession(id!),
    enabled: !!id,
    refetchInterval: 10_000, // HTTP 对账兜底
  })
  const session = sessionQuery.data
  useSessionSockets(session && session.pipelineStatus !== 'completed' && session.pipelineStatus !== 'failed' ? [session.id] : [])
  // WS 实时进度叠加（比 10s 轮询快）：时间线/状态徽标先用推送值，HTTP 对账兜底
  const live = useProgressStore((s) => (id ? s.bySession[id] : undefined))
  const liveNewer = !!(session && live && live.updatedAt > dayjs(session.updatedAt).valueOf())
  // WS 暂停推送用契约名 review_gate_*，归一到库中 gate1/gate2（横幅/时间线比对统一口径）
  const displayStage = normalizeStage(liveNewer ? live.stage : session?.currentStage)
  const displayStatus = liveNewer ? live.status : session?.pipelineStatus

  const reviewsQuery = useQuery({
    queryKey: ['reviews', id],
    queryFn: () => listReviews(id!),
    enabled: !!id,
  })

  const ir1 = useQuery({ queryKey: ['ir', id, 'IR1'], queryFn: () => getIr(id!, 'IR1'), enabled: !!id, retry: false })
  const ir2 = useQuery({ queryKey: ['ir', id, 'IR2'], queryFn: () => getIr(id!, 'IR2'), enabled: !!id, retry: false })
  const ir3 = useQuery({ queryKey: ['ir', id, 'IR3'], queryFn: () => getIr(id!, 'IR3'), enabled: !!id, retry: false })
  const ir4 = useQuery({ queryKey: ['ir', id, 'IR4'], queryFn: () => getIr(id!, 'IR4'), enabled: !!id, retry: false })
  const ir5 = useQuery({ queryKey: ['ir', id, 'IR5'], queryFn: () => getIr(id!, 'IR5'), enabled: !!id, retry: false })
  const ir0 = useQuery({ queryKey: ['ir', id, 'IR0'], queryFn: () => getIr(id!, 'IR0'), enabled: !!id, retry: false })

  const timeline = useMemo(
    () => buildTimeline(displayStage, displayStatus ?? 'pending'),
    [displayStage, displayStatus],
  )

  async function retryFailedStage() {
    if (!id || !session) return
    setRetrying(true)
    try {
      await fireEvent(id, 'RETRY')
      message.success(`已重新派发 ${stageLabel(session.currentStage)}`)
      void queryClient.invalidateQueries({ queryKey: ['session', id] })
      void queryClient.invalidateQueries({ queryKey: ['ir', id] })
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '重试派发失败')
    } finally {
      setRetrying(false)
    }
  }

  if (!session) return <Typography.Text type="secondary">加载中…</Typography.Text>
  const tag = statusTag(displayStatus ?? session.pipelineStatus)
  const pausedAtGate = displayStatus === 'paused' && (displayStage === 'gate1' || displayStage === 'gate2')

  const ir1Data = parseIr<Ir1>(ir1.data)
  const ir2Data = parseIr<Ir2>(ir2.data)
  const ir3Data = parseIr<Ir3>(ir3.data)
  const ir4Data = parseIr<Ir4>(ir4.data)
  const ir5Data = parseIr<Ir5>(ir5.data)
  const ir0RawText = parseIr<{ raw_text?: string }>(ir0.data)?.raw_text ?? null

  return (
    <div style={{ maxWidth: 1320, margin: '0 auto' }}>
      <PageHeader
        title={session.title}
        backTo="/"
        extra={
          <Typography.Text type="secondary" style={{ fontSize: 12 }} copyable={{ text: session.id, tooltips: ['复制完整会话 ID', '已复制'] }}>
            会话 <span className="mono">{session.id.slice(0, 8)}…</span>
          </Typography.Text>
        }
      >
        <Badge status={displayStatus === 'running' ? 'processing' : displayStatus === 'paused' ? 'warning' : displayStatus === 'completed' ? 'success' : displayStatus === 'failed' ? 'error' : 'default'} text={tag.text} style={{ marginLeft: 10 }} />
      </PageHeader>

      <Card styles={{ body: { padding: '8px 16px 14px' } }}>
        <PipelineTimeline nodes={timeline} />
      </Card>

      {pausedAtGate && activeTab === 'overview' && (
        (() => {
          const ir2Empty = !!ir2Data && (ir2Data.parameters ?? []).length === 0
          const desc = ir2Empty
            ? '本会话为降级产物：IR2 参数方案为 0 项（LLM 不可用时降级生成），后续 A3 模型与 A4 校验均基于空方案产生——A4 的「pass」只表示无可校验内容，不代表设计合规。建议驳回并回退，待 LLM 恢复后重算。'
              : displayStage === 'gate2'
                ? '审阅前可打开 FreeCAD，在 SonarCADLink 面板下拉选择本会话——模型会自动载入 3D 视口，可对话微调或直接手动调整后「上传当前模型」，定稿后审阅的即最新版本。请先在「校验报告」页签查看 IR4 合规结论，再到「审阅与参数」页签底部完成批准/驳回。驳回将触发局部回算（A-11），下游版本标记失效。'
              : '请在「审阅与参数」页签查看参数方案并在页面底部完成批准/驳回。驳回将触发局部回算（A-11），下游版本标记失效。'
          return (
            <Alert
              type={ir2Empty ? 'error' : 'warning'}
              showIcon
              style={{ marginTop: 14 }}
              message={ir2Empty ? `${stageLabel(displayStage)} 待人工裁决——注意：方案为空` : `${stageLabel(displayStage)} 待人工裁决`}
              description={desc}
              action={
                <Button
                  size="small" type="primary" danger={ir2Empty}
                  onClick={() => setActiveTab(displayStage === 'gate2' ? 'report' : 'review')}
                >
                  {displayStage === 'gate2' ? (ir2Empty ? '查看情况' : '去校验报告') : '去审阅参数'}
                </Button>
              }
            />
          )
        })()
      )}

      {session.pipelineStatus === 'failed' && (
        <Alert
          type="error"
          showIcon
          style={{ marginTop: 14 }}
          message={`${stageLabel(session.currentStage)} 阶段执行失败`}
          description="常见于临时性故障（LLM 网络抖动/限流、转换超时）。可重新执行该阶段，不会覆盖已生成的快照；若同一阶段反复失败，建议新建会话并在审阅关驳回回退。"
          action={
            <Button size="small" type="primary" danger loading={retrying} disabled={!canRetry} onClick={retryFailedStage}>
              重新执行{session.currentStage ? ` ${session.currentStage}` : ''}
            </Button>
          }
        />
      )}

      <Tabs
        style={{ marginTop: 8 }}
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: 'overview',
            label: '总览',
            children: (
              <OverviewTab
                sessionId={session.id}
                ir1={ir1Data}
                ir0RawText={ir0RawText}
                sessionKbNames={session.kbNames}
                versions={{ IR1: ir1.data?.version, IR2: ir2.data?.version, IR3: ir3.data?.version, IR4: ir4.data?.version, IR5: ir5.data?.version }}
              />
            ),
          },
          {
            key: 'review',
            label: <span>审阅与参数{ir2Data ? `（${ir2Data.parameters.length} 参数）` : ''}</span>,
            children: (
              <ReviewTab
                sessionId={session.id}
                gate={session.currentStage === 'gate1' ? 'gate1' : session.currentStage === 'gate2' ? 'gate2' : null}
                paused={session.pipelineStatus === 'paused'}
                ir1={ir1Data}
                ir2={ir2Data}
                ir3={ir3Data}
                ir4={ir4Data}
                reviews={reviewsQuery.data ?? []}
                versions={{ IR1: ir1.data?.version, IR2: ir2.data?.version, IR3: ir3.data?.version, IR4: ir4.data?.version, IR5: ir5.data?.version }}
                onGoReport={() => setActiveTab('report')}
                onDecided={() => {
                  void queryClient.invalidateQueries({ queryKey: ['session', id] })
                  void queryClient.invalidateQueries({ queryKey: ['ir', id] })
                  void queryClient.invalidateQueries({ queryKey: ['reviews', id] })
                  void queryClient.invalidateQueries({ queryKey: ['sessions'] })
                }}
              />
            ),
          },
          {
            key: 'report',
            label: <span>校验报告{ir4Data?.overall === 'fail' ? <Tag color="error" style={{ marginLeft: 6, marginInlineEnd: 0 }}>fail</Tag> : ir4Data?.overall === 'incomplete' ? <Tag color="warning" style={{ marginLeft: 6, marginInlineEnd: 0 }}>incomplete</Tag> : ir4Data ? <Tag color="success" style={{ marginLeft: 6, marginInlineEnd: 0 }}>pass</Tag> : ''}</span>,
            children: (
              <ReportTab
                sessionId={session.id}
                ir4={ir4Data}
                ir4Version={ir4.data?.version}
                ir5={ir5Data}
                ir5Version={ir5.data?.version}
                ir3={ir3Data}
                reviews={reviewsQuery.data ?? []}
                pipelineStatus={session.pipelineStatus}
                ir2Empty={!!ir2Data && (ir2Data.parameters ?? []).length === 0}
                onDecided={() => {
                  void queryClient.invalidateQueries({ queryKey: ['session', id] })
                  void queryClient.invalidateQueries({ queryKey: ['ir', id] })
                  void queryClient.invalidateQueries({ queryKey: ['reviews', id] })
                }}
              />
            ),
          },
          {
            key: 'model',
            label: '三维与交付物',
            children: <ModelTab sessionId={session.id} ir3={ir3Data} ir3Version={ir3.data?.version} ir2Version={ir2.data?.version} ir4Version={ir4.data?.version} />,
          },
        ]}
      />

      <Typography.Paragraph type="secondary" style={{ fontSize: 11, marginTop: 16 }}>
        创建 {fmtTime(session.createdAt)} · 更新 {fmtTime(session.updatedAt)} · 创建人 {session.createdByName ?? (session.createdBy ? `${session.createdBy.slice(0, 8)}…` : '—')}
      </Typography.Paragraph>
    </div>
  )
}
