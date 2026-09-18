import { useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { Alert, Badge, Card, Tabs, Tag, Typography } from 'antd'
import { getSession } from '../../api/sessions'
import { getIr, listReviews, parseIr, type Ir1, type Ir2, type Ir3, type Ir4, type Ir5 } from '../../api/workflow'
import { useSessionSockets } from '../../hooks/useSessionSockets'
import { PipelineTimeline, buildTimeline } from '../../components/PipelineTimeline'
import { stageLabel, statusTag } from '../../lib/stageLabel'
import { OverviewTab } from './OverviewTab'
import { ReviewTab } from './ReviewTab'
import { ReportTab } from './ReportTab'
import { StubPage } from '../StubPage'

/** 会话工作台：总览 / 审阅与参数 / 校验报告 / 三维与交付物（T4.3 交付）。 */
export default function SessionPage() {
  const { id } = useParams<{ id: string }>()
  const queryClient = useQueryClient()

  const sessionQuery = useQuery({
    queryKey: ['session', id],
    queryFn: () => getSession(id!),
    enabled: !!id,
    refetchInterval: 10_000, // HTTP 对账兜底
  })
  const session = sessionQuery.data
  useSessionSockets(session && session.pipelineStatus !== 'completed' && session.pipelineStatus !== 'failed' ? [session.id] : [])

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

  const timeline = useMemo(
    () => buildTimeline(session?.currentStage, session?.pipelineStatus ?? 'pending'),
    [session?.currentStage, session?.pipelineStatus],
  )

  if (!session) return <Typography.Text type="secondary">加载中…</Typography.Text>
  const tag = statusTag(session.pipelineStatus)
  const pausedAtGate = session.pipelineStatus === 'paused' && (session.currentStage === 'gate1' || session.currentStage === 'gate2')

  const ir1Data = parseIr<Ir1>(ir1.data)
  const ir2Data = parseIr<Ir2>(ir2.data)
  const ir3Data = parseIr<Ir3>(ir3.data)
  const ir4Data = parseIr<Ir4>(ir4.data)
  const ir5Data = parseIr<Ir5>(ir5.data)

  return (
    <div style={{ maxWidth: 1320, margin: '0 auto' }}>
      <div className="row" style={{ marginBottom: 14, flexWrap: 'wrap' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>{session.title}</Typography.Title>
        <Badge status={session.pipelineStatus === 'running' ? 'processing' : session.pipelineStatus === 'paused' ? 'warning' : session.pipelineStatus === 'completed' ? 'success' : session.pipelineStatus === 'failed' ? 'error' : 'default'} text={tag.text} />
        <span style={{ flex: 1 }} />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>会话 {session.id}</Typography.Text>
      </div>

      <Card styles={{ body: { padding: '8px 16px 14px' } }}>
        <PipelineTimeline nodes={timeline} />
      </Card>

      {pausedAtGate && (
        <Alert
          type="warning"
          showIcon
          style={{ marginTop: 14 }}
          message={`${stageLabel(session.currentStage)} 待人工裁决`}
          description="请在「审阅与参数」或「校验报告」页签完成批准/驳回。驳回将触发局部回算（A-11），下游版本标记失效。"
        />
      )}

      <Tabs
        style={{ marginTop: 8 }}
        items={[
          {
            key: 'overview',
            label: '总览',
            children: (
              <OverviewTab
                sessionId={session.id}
                ir1={ir1Data}
                versions={{ IR2: ir2.data?.version, IR3: ir3.data?.version, IR4: ir4.data?.version, IR5: ir5.data?.version }}
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
                ir4={ir4Data}
                reviews={reviewsQuery.data ?? []}
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
            label: <span>校验报告{ir4Data?.overall === 'fail' ? <Tag color="error" style={{ marginLeft: 6, marginInlineEnd: 0 }}>fail</Tag> : ir4Data ? <Tag color="success" style={{ marginLeft: 6, marginInlineEnd: 0 }}>pass</Tag> : ''}</span>,
            children: (
              <ReportTab
                sessionId={session.id}
                ir4={ir4Data}
                ir4Version={ir4.data?.version}
                ir5={ir5Data}
                ir5Version={ir5.data?.version}
                ir3={ir3Data}
                reviews={reviewsQuery.data ?? []}
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
            children: ir3Data ? (
              <StubPage title="三维预览与交付物下载" phase="T4.3" note={`IR3 v${ir3.data?.version} 已就绪（step_file: ${ir3Data.step_file.bucket}/${ir3Data.step_file.object_key}）`} />
            ) : (
              <StubPage title="三维预览与交付物下载" phase="T4.3" note="IR3 尚未生成" />
            ),
          },
        ]}
      />

      <Typography.Paragraph type="secondary" style={{ fontSize: 11, marginTop: 16 }}>
        创建 {session.createdAt} · 更新 {session.updatedAt} · 创建人 {session.createdBy ?? '—'}
      </Typography.Paragraph>
    </div>
  )
}
