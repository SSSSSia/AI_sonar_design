import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Badge, Button, Card, Empty, Grid, List, Progress, Statistic, Table, Tag, Tooltip, Typography } from 'antd'
import type { TableProps } from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { listSessions } from '../api/sessions'
import type { DesignSession } from '../api/types'
import { useSessionSockets } from '../hooks/useSessionSockets'
import { useProgressStore, useWsStatusStore, type SessionProgress } from '../stores/progressStore'
import { stageLabel } from '../lib/stageLabel'

dayjs.extend(relativeTime)

const { useBreakpoint } = Grid

interface Row extends DesignSession {
  displayStatus: string
  live?: SessionProgress
}

const BADGE_STATUS: Record<string, 'default' | 'processing' | 'warning' | 'success' | 'error'> = {
  pending: 'default',
  running: 'processing',
  paused: 'warning',
  completed: 'success',
  failed: 'error',
  degraded: 'warning',
}

const STATUS_TEXT: Record<string, string> = {
  pending: '待启动',
  running: '运行中',
  paused: '待审阅',
  completed: '已完成',
  failed: '失败',
  degraded: '降级完成',
}

function StatusTag({ row }: { row: Row }) {
  return (
    <Badge
      status={BADGE_STATUS[row.displayStatus] ?? 'default'}
      text={STATUS_TEXT[row.displayStatus] ?? row.displayStatus}
    />
  )
}

export default function DashboardPage() {
  const navigate = useNavigate()
  const screens = useBreakpoint()
  const [refreshKey, setRefreshKey] = useState(0)

  const sessionsQuery = useQuery({
    queryKey: ['sessions', refreshKey],
    queryFn: () => listSessions({ page: 1, size: 50 }),
    refetchInterval: 20_000, // HTTP 对账兜底：WS 不可用时进度仍能收敛
  })

  const sessions = sessionsQuery.data?.records ?? []
  const activeSids = useMemo(
    () => sessions.filter((s) => s.pipelineStatus === 'running' || s.pipelineStatus === 'paused').map((s) => s.id),
    [sessions],
  )
  useSessionSockets(activeSids)

  const liveProgress = useProgressStore((s) => s.bySession)
  const wsStatus = useWsStatusStore((s) => s.bySession)

  const stats = useMemo(() => {
    const running = sessions.filter((s) => s.pipelineStatus === 'running').length
    const paused = sessions.filter((s) => s.pipelineStatus === 'paused').length
    const completed = sessions.filter((s) => s.pipelineStatus === 'completed').length
    return { total: sessionsQuery.data?.total ?? sessions.length, running, paused, completed }
  }, [sessions, sessionsQuery.data?.total])

  const wsSummary = useMemo(() => {
    const states = activeSids.map((sid) => wsStatus[sid]?.status).filter(Boolean)
    if (states.length === 0) return null
    if (states.some((s) => s === 'reconnecting')) {
      const attempt = Math.max(...activeSids.map((sid) => wsStatus[sid]?.attempt ?? 0))
      return { tone: 'warning' as const, text: `WS 重连中（第 ${attempt} 次）` }
    }
    if (states.every((s) => s === 'connected')) return { tone: 'success' as const, text: 'WS 已连接' }
    return { tone: 'default' as const, text: 'WS 未连接 · 轮询对账中' }
  }, [activeSids, wsStatus])

  // 会话行：WS 进度比列表快照新时用实时值覆盖展示
  const rows: Row[] = sessions.map((s) => {
    const live = liveProgress[s.id]
    if (live && live.updatedAt > dayjs(s.updatedAt).valueOf()) {
      return { ...s, live, displayStatus: live.status }
    }
    return { ...s, displayStatus: s.pipelineStatus }
  })

  const columns: TableProps<Row>['columns'] = [
    {
      title: '会话',
      key: 'title',
      render: (_, r) => (
        <a onClick={() => navigate(`/sessions/${r.id}`)} style={{ fontWeight: 500 }}>{r.title}</a>
      ),
    },
    { title: '状态', key: 'status', width: 110, render: (_, r) => <StatusTag row={r} /> },
    {
      title: '当前阶段',
      key: 'stage',
      width: 240,
      render: (_, r) => (
        <div>
          <div>{stageLabel(r.currentStage)}{r.live ? ` · ${Math.round(r.live.progress)}%` : ''}</div>
          {r.displayStatus === 'running' && (
            <Progress
              percent={Math.round(r.live?.progress ?? 0)}
              size="small"
              showInfo={false}
              style={{ maxWidth: 180, marginTop: 2, marginBottom: 0 }}
            />
          )}
        </div>
      ),
    },
    {
      title: '更新时间',
      key: 'updatedAt',
      width: 120,
      render: (_, r) => (
        <Tooltip title={dayjs(r.updatedAt).format('YYYY-MM-DD HH:mm:ss')}>
          {dayjs(r.updatedAt).fromNow()}
        </Tooltip>
      ),
    },
  ]

  return (
    <div style={{ maxWidth: 1320, margin: '0 auto' }}>
      <div className="row" style={{ marginBottom: 16, flexWrap: 'wrap' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>仪表盘</Typography.Title>
        <span style={{ flex: 1 }} />
        {wsSummary && (
          <Tag color={wsSummary.tone === 'default' ? undefined : wsSummary.tone}>{wsSummary.text}</Tag>
        )}
        <Button icon={<ReloadOutlined />} onClick={() => setRefreshKey((k) => k + 1)}>刷新</Button>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/sessions/new')}>
          新建设计会话
        </Button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: screens.md ? 'repeat(4, 1fr)' : '1fr', gap: 16, marginBottom: 16 }}>
        <Card styles={{ body: { padding: '14px 16px' } }}><Statistic title="设计会话" value={stats.total} /></Card>
        <Card styles={{ body: { padding: '14px 16px' } }}><Statistic title="进行中" value={stats.running} /></Card>
        <Card styles={{ body: { padding: '14px 16px' } }}>
          <Statistic title="待审阅" value={stats.paused} valueStyle={stats.paused > 0 ? { color: '#d97706' } : undefined} />
        </Card>
        <Card styles={{ body: { padding: '14px 16px' } }}><Statistic title="已完成" value={stats.completed} /></Card>
      </div>

      <Card
        title="设计会话"
        extra={<Typography.Text type="secondary" style={{ fontSize: 12 }}>状态经 WebSocket 实时推送</Typography.Text>}
        styles={{ body: { padding: screens.md ? undefined : 0 } }}
      >
        {rows.length === 0 && !sessionsQuery.isLoading ? (
          <Empty description="暂无会话——点右上角「新建设计会话」开始" style={{ padding: 32 }} />
        ) : screens.md ? (
          <Table
            rowKey="id"
            dataSource={rows}
            columns={columns}
            pagination={false}
            loading={sessionsQuery.isLoading}
            onRow={(r) => ({ onClick: () => navigate(`/sessions/${r.id}`), style: { cursor: 'pointer' } })}
          />
        ) : (
          <List
            loading={sessionsQuery.isLoading}
            dataSource={rows}
            renderItem={(r) => (
              <List.Item
                style={{ cursor: 'pointer' }}
                onClick={() => navigate(`/sessions/${r.id}`)}
                actions={[<StatusTag key="st" row={r} />]}
              >
                <List.Item.Meta
                  title={r.title}
                  description={
                    <span style={{ fontSize: 12 }}>
                      {stageLabel(r.currentStage)}
                      {r.live ? ` · ${Math.round(r.live.progress)}%` : ''} · {dayjs(r.updatedAt).fromNow()}
                    </span>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </Card>
    </div>
  )
}
