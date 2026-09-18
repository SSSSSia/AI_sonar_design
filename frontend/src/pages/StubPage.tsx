import { useQuery } from '@tanstack/react-query'
import { Card, Descriptions, Empty, Skeleton, Tag } from 'antd'
import { useParams } from 'react-router-dom'
import { getSession } from '../api/sessions'
import { stageLabel, statusTag } from '../lib/stageLabel'

/** 导航占位页：如实标注交付阶段（A-16），不做假界面。 */
export function StubPage({ title, phase, note }: { title: string; phase: string; note?: string }) {
  return (
    <div style={{ maxWidth: 720, margin: '0 auto' }}>
      <Card>
        <Empty description={null} style={{ padding: '24px 0 8px' }}>
          <div style={{ fontSize: 16, fontWeight: 600 }}>{title}</div>
          <div style={{ color: '#57606a', marginTop: 8 }}>
            本页面在 <Tag>{phase}</Tag> 交付，当前后端 API 已就绪。
          </div>
          {note && <div style={{ color: '#8b949e', fontSize: 12, marginTop: 8 }}>{note}</div>}
        </Empty>
      </Card>
    </div>
  )
}

/** 会话详情占位：展示会话状态机信息（真实数据），审阅/校验界面 T4.2 交付。 */
export function SessionStubPage() {
  const { id } = useParams<{ id: string }>()
  const { data: session, isLoading } = useQuery({
    queryKey: ['session', id],
    queryFn: () => getSession(id!),
    enabled: !!id,
    refetchInterval: 10_000,
  })

  if (isLoading || !session) {
    return <Skeleton active style={{ maxWidth: 720, margin: '0 auto' }} />
  }

  const pausedAtGate = session.pipelineStatus === 'paused'

  return (
    <div style={{ maxWidth: 720, margin: '0 auto' }}>
      <Card title={session.title}>
        <Descriptions column={2} size="small">
          <Descriptions.Item label="状态">
            <Tag color={statusTag(session.pipelineStatus).color === 'default' ? undefined : statusTag(session.pipelineStatus).color}>
              {statusTag(session.pipelineStatus).text}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="当前阶段">{stageLabel(session.currentStage)}</Descriptions.Item>
          <Descriptions.Item label="创建时间">{session.createdAt}</Descriptions.Item>
          <Descriptions.Item label="会话 ID"><span style={{ fontFamily: 'monospace', fontSize: 12 }}>{session.id}</span></Descriptions.Item>
        </Descriptions>
        {pausedAtGate && (
          <Card type="inner" style={{ marginTop: 16 }} title={stageLabel(session.currentStage)}>
            该会话正等待人工裁决。审阅界面（IR 对照、参数表溯源、批准/驳回）在 T4.2 交付——
            当前可经 API {'POST /reviews/{gate}'} 裁决。
          </Card>
        )}
      </Card>
    </div>
  )
}
