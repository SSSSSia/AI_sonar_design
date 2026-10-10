import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { App, Badge, Button, Card, Empty, Grid, List, Popconfirm, Progress, Select, Space, Statistic, Table, Tabs, Tag, Tooltip, Typography } from 'antd'
import type { TableProps } from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { deleteSession, deleteSessions, listSessions, listProjects } from '../api/sessions'
import { errText } from '../api/client'
import { getKbStatus } from '../api/kb'
import type { DesignSession } from '../api/types'
import { useSessionSockets } from '../hooks/useSessionSockets'
import { useProgressStore, useWsStatusStore, type SessionProgress } from '../stores/progressStore'
import { useAuthStore } from '../stores/authStore'
import { stageLabel } from '../lib/stageLabel'
import EChart, { CHART_COLORS } from '../components/EChart'
import type { EChartsCoreOption } from 'echarts/core'

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

/** 统计卡（ADR-011）：渐变卡底 + 右上角彩色模糊光斑（装饰限定于统计卡，语义色由数值区表达） */
function StatCard({ title, value, suffix, orb, valueStyle }: {
  title: string
  value: string | number
  suffix?: React.ReactNode
  /** 光斑色（装饰）；仅取语义/交互色，不做随机色 */
  orb: string
  valueStyle?: React.CSSProperties
}) {
  return (
    <Card styles={{ body: { padding: '14px 16px', position: 'relative', overflow: 'hidden' } }} style={{ background: 'var(--card-grad)', borderColor: 'var(--border)' }}>
      <span
        aria-hidden
        style={{
          position: 'absolute', top: -18, right: -18, width: 76, height: 76, borderRadius: '50%',
          background: orb, opacity: .13, filter: 'blur(22px)', pointerEvents: 'none',
        }}
      />
      <Statistic title={title} value={value} suffix={suffix} valueStyle={valueStyle} />
    </Card>
  )
}

export default function DashboardPage() {
  const navigate = useNavigate()
  const screens = useBreakpoint()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const isAdmin = useAuthStore((s) => s.roles.includes('admin'))
  const displayName = useAuthStore((s) => s.displayName)
  const username = useAuthStore((s) => s.username)
  const [refreshKey, setRefreshKey] = useState(0)
  const [statusFilter, setStatusFilter] = useState('all')
  const [selectedKeys, setSelectedKeys] = useState<React.Key[]>([])
  const [batching, setBatching] = useState(false)
  const [searchParams, setSearchParams] = useSearchParams()
  const [projectFilter, setProjectFilter] = useState(searchParams.get('project') ?? 'all')

  // ?project= 双向同步：承接项目管理页「查看会话」跳转；本页切换时回写地址栏（可分享/刷新保持）
  useEffect(() => {
    const p = searchParams.get('project') ?? 'all'
    if (p !== projectFilter) setProjectFilter(p)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams])
  function changeProject(v: string) {
    setProjectFilter(v)
    setSelectedKeys([])
    setSearchParams(v === 'all' ? {} : { project: v }, { replace: true })
  }

  const projectsQuery = useQuery({ queryKey: ['projects'], queryFn: () => listProjects({ page: 1, size: 100 }) })
  const projects = projectsQuery.data?.records ?? []

  async function removeSession(r: Row) {
    const force = r.displayStatus === 'running' || r.displayStatus === 'paused'
    try {
      const res = await deleteSession(r.id, force)
      message.success(`已删除「${r.title}」：阶段产物 ${res.ir_snapshots} 份 · 审阅记录 ${res.review_records} 条 · 溯源记录 ${res.trace_chain_rows} 条；沉淀入库的知识已保留`)
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
      setRefreshKey((k) => k + 1)
    } catch (e) {
      message.error(errText(e, 160))
    }
  }

  async function removeSelected() {
    if (selectedKeys.length === 0) return
    const selected = rows.filter((r) => selectedKeys.includes(r.id))
    const force = selected.some((r) => r.displayStatus === 'running' || r.displayStatus === 'paused')
    setBatching(true)
    try {
      const res = await deleteSessions(selectedKeys.map(String), force)
      const parts = [`成功 ${res.deleted_count} 项`]
      if (res.failed_count > 0) {
        const firstErr = res.results.find((r) => !r.deleted)?.error
        parts.push(`失败 ${res.failed_count} 项${firstErr ? `（如：${String(firstErr).slice(0, 60)}）` : ''}`)
      }
      if (res.deleted_count > 0) parts.push('已沉淀知识保留')
      if (res.failed_count === 0) message.success(`批量删除完成：${parts.join(' · ')}`)
      else message.warning(`批量删除部分完成：${parts.join(' · ')}`)
      setSelectedKeys([])
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
      setRefreshKey((k) => k + 1)
    } catch (e) {
      message.error(errText(e, 160))
    } finally {
      setBatching(false)
    }
  }

  const sessionsQuery = useQuery({
    queryKey: ['sessions', refreshKey, projectFilter],
    queryFn: () => listSessions({ page: 1, size: 100, projectId: projectFilter === 'all' ? undefined : projectFilter }),
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

  // 知识文档现状（审查 P2-1：mockup 02 第 4/5 卡）；py-engine 不可达时如实 '—'
  const kbQuery = useQuery({ queryKey: ['kb-status-dash'], queryFn: getKbStatus, retry: false, refetchInterval: 60_000 })

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

  // 状态筛选（审查 P1-3）：全部/进行中/待审阅/已完成/失败
  const filtered: Row[] = statusFilter === 'all' ? rows : rows.filter((r) => r.displayStatus === statusFilter)
  // 运行中但长时间无进展：如实标注，不臆断失败（审查 P1-3）
  const staleHour = 1
  const isStale = (r: Row) => r.displayStatus === 'running' && !r.live && dayjs().diff(dayjs(r.updatedAt), 'hour') >= staleHour

  // ---- ADR-011 图表（全部由真实会话记录聚合，无 mock 数据；A-16） ----
  // 近 7 天活动趋势：按 updatedAt 逐日聚合并同时统计其中完成数（客户端口径，如实展示）
  const trend = useMemo(() => {
    const days: { day: string; updates: number; completed: number }[] = []
    for (let i = 6; i >= 0; i--) {
      const d = dayjs().subtract(i, 'day')
      const key = d.format('YYYY-MM-DD')
      days.push({
        day: d.format('MM-DD'),
        updates: sessions.filter((s) => dayjs(s.updatedAt).format('YYYY-MM-DD') === key).length,
        completed: sessions.filter((s) => s.pipelineStatus === 'completed' && dayjs(s.updatedAt).format('YYYY-MM-DD') === key).length,
      })
    }
    return days
  }, [sessions])

  const trendOption: EChartsCoreOption = {
    tooltip: { trigger: 'axis' },
    legend: { data: ['会话更新', '其中完成'], textStyle: { color: CHART_COLORS.text2 }, top: 0, itemWidth: 14, itemHeight: 8 },
    grid: { left: 36, right: 16, top: 34, bottom: 26 },
    xAxis: {
      type: 'category', data: trend.map((d) => d.day), boundaryGap: false,
      axisLine: { lineStyle: { color: CHART_COLORS.split } }, axisLabel: { color: CHART_COLORS.text3 },
    },
    yAxis: {
      type: 'value', minInterval: 1,
      splitLine: { lineStyle: { color: CHART_COLORS.split } }, axisLabel: { color: CHART_COLORS.text3 },
    },
    series: [
      {
        name: '会话更新', type: 'line', smooth: true, symbolSize: 6,
        data: trend.map((d) => d.updates), itemStyle: { color: CHART_COLORS.accent },
        areaStyle: { color: 'rgba(6, 182, 212, .12)' },
      },
      {
        name: '其中完成', type: 'line', smooth: true, symbolSize: 6,
        data: trend.map((d) => d.completed), itemStyle: { color: CHART_COLORS.ok },
      },
    ],
  }

  // 状态分布（环形）：会话五态 + 降级，0 值项不显示
  const statusDist = useMemo(() => {
    const order: [string, string, string][] = [
      ['pending', '待启动', CHART_COLORS.text3],
      ['running', '运行中', CHART_COLORS.accent],
      ['paused', '待审阅', CHART_COLORS.warn],
      ['completed', '已完成', CHART_COLORS.ok],
      ['failed', '失败', CHART_COLORS.err],
      ['degraded', '降级完成', CHART_COLORS.info],
    ]
    return order
      .map(([k, name, color]) => ({
        name,
        value: rows.filter((r) => r.displayStatus === k).length,
        itemStyle: { color },
      }))
      .filter((d) => d.value > 0)
  }, [rows])

  const distOption: EChartsCoreOption = {
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, textStyle: { color: CHART_COLORS.text2 }, itemWidth: 10, itemHeight: 10, icon: 'circle' },
    series: [{
      type: 'pie',
      radius: ['46%', '68%'],
      center: ['50%', '44%'],
      label: { color: CHART_COLORS.text2, formatter: '{b} {c}' },
      labelLine: { lineStyle: { color: CHART_COLORS.split } },
      itemStyle: { borderColor: '#0f2035', borderWidth: 2 },
      data: statusDist,
    }],
  }

  const columns: TableProps<Row>['columns'] = [
    {
      title: '会话',
      key: 'title',
      render: (_, r) => (
        <a onClick={() => navigate(`/sessions/${r.id}`)} style={{ fontWeight: 500 }}>{r.title}</a>
      ),
    },
    {
      title: '所属项目',
      key: 'project',
      width: 130,
      render: (_, r) => {
        const name = r.projectName ?? projects.find((p) => p.id === r.projectId)?.name
        const label = name ?? (r.projectId ? r.projectId.slice(0, 8) : '—')
        if (projectFilter === r.projectId) return <Tag style={{ marginInlineEnd: 0 }}>{label}</Tag>
        return (
          <a
            onClick={(e) => { e.stopPropagation(); changeProject(r.projectId) }}
            title="筛选该项目的会话"
          >{label}</a>
        )
      },
    },
    { title: '状态', key: 'status', width: 110, render: (_, r) => <StatusTag row={r} /> },
    {
      title: '当前阶段',
      key: 'stage',
      width: 240,
      render: (_, r) => (
        <div>
          <div>{stageLabel(r.live?.stage ?? r.currentStage)}{r.live ? ` · ${Math.round(r.live.progress)}%` : ''}</div>
          {r.displayStatus === 'running' && (
            <Progress
              percent={Math.round(r.live?.progress ?? 0)}
              size="small"
              showInfo={false}
              style={{ maxWidth: 180, marginTop: 2, marginBottom: 0 }}
            />
          )}
          {isStale(r) && (
            <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 2 }}>长时间无进展 · 最近更新 {dayjs(r.updatedAt).fromNow()}</div>
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
    {
      title: '操作',
      key: 'act',
      width: 130,
      render: (_, r) => {
        const label = r.displayStatus === 'paused' ? '去审阅' : r.displayStatus === 'completed' ? '交付物' : r.displayStatus === 'failed' ? '查看日志' : '查看'
        const force = r.displayStatus === 'running' || r.displayStatus === 'paused'
        return (
          <span onClick={(e) => e.stopPropagation()}>
            <a onClick={() => navigate(`/sessions/${r.id}`)} style={{ marginRight: 10 }}>{label}</a>
            {isAdmin && (
              <Popconfirm
                title={force ? `强制删除「${r.title}」？` : `删除「${r.title}」？`}
                description={force
                  ? '会话尚未结束，在途任务的后续写入会失败；快照/裁决/溯源/MinIO 产物将一并清除（已沉淀知识保留）。'
                  : '将一并清除快照、裁决记录、溯源链与 MinIO 产物；已沉淀入图谱的知识保留。不可恢复。'}
                okText={force ? '强制删除' : '删除'}
                okButtonProps={{ danger: true }}
                onConfirm={() => removeSession(r)}
              >
                <a style={{ color: '#ef4444' }}>删除</a>
              </Popconfirm>
            )}
          </span>
        )
      },
    },
  ]

  return (
    <div style={{ maxWidth: 1320, margin: '0 auto' }}>
      <div className="row" style={{ marginBottom: 16, flexWrap: 'wrap' }}>
        <div className="page-head">仪表盘</div>
        <span style={{ flex: 1 }} />
        {wsSummary && (
          <Tag color={wsSummary.tone === 'default' ? undefined : wsSummary.tone}>{wsSummary.text}</Tag>
        )}
        <Select
          style={{ minWidth: 150 }}
          value={projectFilter}
          onChange={changeProject}
          popupMatchSelectWidth={false}
          options={[
            { value: 'all', label: '全部项目' },
            ...projects.map((p) => ({ value: p.id, label: p.name })),
          ]}
        />
        <Button icon={<ReloadOutlined />} onClick={() => setRefreshKey((k) => k + 1)}>刷新</Button>
      </div>

      {/* 欢迎横幅（ADR-011：网格纹理装饰仅限横幅；快捷入口为真实路由） */}
      <div
        className="grid-texture"
        style={{
          position: 'relative', overflow: 'hidden', borderRadius: 10, border: '1px solid var(--border)',
          background: 'linear-gradient(135deg, #0f2035 0%, #0a1628 65%)',
          padding: screens.md ? '20px 24px' : '16px',
          marginBottom: 16, display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap',
        }}
      >
        <div style={{ flex: 1, minWidth: 240 }}>
          <div style={{ fontSize: 18, fontWeight: 600 }}>
            欢迎回来，{displayName || username}
          </div>
          <div style={{ color: 'var(--text-2)', fontSize: 12.5, marginTop: 4 }}>
            把一页需求，变成可审阅、可溯源的声呐装备设计。
          </div>
        </div>
        <Space wrap>
          <Button onClick={() => navigate('/projects')}>管理项目</Button>
          <Button
            type="primary" icon={<PlusOutlined />}
            onClick={() => navigate(projectFilter !== 'all' ? `/sessions/new?project=${projectFilter}` : '/sessions/new')}
          >
            新建设计会话
          </Button>
        </Space>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: screens.md ? 'repeat(5, 1fr)' : '1fr', gap: 16, marginBottom: 16 }}>
        <StatCard title="设计会话" value={stats.total} orb={CHART_COLORS.accent} />
        <StatCard title="进行中" value={stats.running} orb={CHART_COLORS.accent2} />
        <StatCard
          title="待审阅" value={stats.paused} orb={CHART_COLORS.warn}
          valueStyle={stats.paused > 0 ? { color: '#f59e0b' } : undefined}
        />
        <StatCard title="已完成" value={stats.completed} orb={CHART_COLORS.ok} />
        <StatCard
          title="知识文档" value={kbQuery.data?.documents ?? '—'} orb={CHART_COLORS.info}
          suffix={kbQuery.data?.documents != null ? <span style={{ fontSize: 12, color: 'var(--text-3)' }}>· {kbQuery.data.chunks ?? '—'} 块</span> : undefined}
        />
      </div>

      {/* 活动趋势 + 状态分布（会话为空时如实给空态，不画空图） */}
      {sessions.length === 0 ? null : (
        <div style={{ display: 'grid', gridTemplateColumns: screens.md ? '2fr 1fr' : '1fr', gap: 16, marginBottom: 16 }}>
          <Card title="近 7 天会话活动" styles={{ body: { paddingTop: 8 } }}>
            <EChart option={trendOption} height={230} />
          </Card>
          <Card title="会话状态分布" styles={{ body: { paddingTop: 8 } }}>
            <EChart option={distOption} height={230} />
          </Card>
        </div>
      )}

      <Card
        title="设计会话"
        extra={<Typography.Text type="secondary" style={{ fontSize: 12 }}>状态经 WebSocket 实时推送</Typography.Text>}
        styles={{ body: { padding: screens.md ? undefined : 0 } }}
      >
        <Tabs
          size="small"
          activeKey={statusFilter}
          onChange={(k) => { setStatusFilter(k); setSelectedKeys([]) }}
          style={{ marginBottom: 0 }}
          items={[
            { key: 'all', label: `全部（${rows.length}）` },
            { key: 'running', label: `进行中（${rows.filter((r) => r.displayStatus === 'running').length}）` },
            { key: 'paused', label: `待审阅（${rows.filter((r) => r.displayStatus === 'paused').length}）` },
            { key: 'completed', label: `已完成（${rows.filter((r) => r.displayStatus === 'completed').length}）` },
            { key: 'failed', label: `失败（${rows.filter((r) => r.displayStatus === 'failed').length}）` },
          ]}
        />
        {filtered.length === 0 && !sessionsQuery.isLoading ? (
          <Empty
            description={statusFilter === 'all'
              ? projectFilter === 'all'
                ? '暂无会话——点右上角「新建会话」开始'
                : '该项目下暂无会话——可点右上角「新建会话」'
              : '该状态下暂无会话'}
            style={{ padding: 32 }}
          />
        ) : screens.md ? (
          <>
            {isAdmin && selectedKeys.length > 0 && (
              <div className="row" style={{ padding: '8px 4px', background: 'rgba(6,182,212,.06)', borderRadius: 6, margin: '8px 0' }}>
                <Typography.Text style={{ fontSize: 12.5 }}>已选 <b>{selectedKeys.length}</b> 项会话</Typography.Text>
                <span style={{ flex: 1 }} />
                <Button size="small" onClick={() => setSelectedKeys([])}>取消选择</Button>
                <Popconfirm
                  title={(() => {
                    const sel = rows.filter((r) => selectedKeys.includes(r.id))
                    const anyLive = sel.some((r) => r.displayStatus === 'running' || r.displayStatus === 'paused')
                    return anyLive ? `强制删除所选 ${selectedKeys.length} 项？` : `删除所选 ${selectedKeys.length} 项？`
                  })()}
                  description={(() => {
                    const sel = rows.filter((r) => selectedKeys.includes(r.id))
                    const anyLive = sel.some((r) => r.displayStatus === 'running' || r.displayStatus === 'paused')
                    return anyLive
                      ? '所选含未结束会话：在途任务的后续写入会失败；快照/裁决/溯源/MinIO 产物将一并清除（已沉淀知识保留）。'
                      : '将一并清除快照、裁决记录、溯源链与 MinIO 产物；已沉淀入图谱的知识保留。不可恢复。'
                  })()}
                  okText="删除"
                  okButtonProps={{ danger: true }}
                  onConfirm={removeSelected}
                >
                  <Button size="small" danger type="primary" loading={batching}>批量删除</Button>
                </Popconfirm>
              </div>
            )}
            <Table
              rowKey="id"
              dataSource={filtered}
              columns={columns}
              rowSelection={isAdmin ? { selectedRowKeys: selectedKeys, onChange: setSelectedKeys } : undefined}
              pagination={{ pageSize: 10, hideOnSinglePage: true, showSizeChanger: false }}
              loading={sessionsQuery.isLoading}
              onRow={(r) => ({
                onClick: (e) => {
                  // 复选框/弹层内点击不触发行导航
                  if ((e.target as HTMLElement).closest('.ant-table-selection-column, .ant-checkbox-wrapper, .ant-popover, .ant-btn')) return
                  navigate(`/sessions/${r.id}`)
                },
                style: { cursor: 'pointer' },
              })}
            />
          </>
        ) : (
          <List
            loading={sessionsQuery.isLoading}
            dataSource={filtered}
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
                      {r.projectName ? `${r.projectName} · ` : ''}{stageLabel(r.currentStage)}
                      {r.live ? ` · ${Math.round(r.live.progress)}%` : ''} · {dayjs(r.updatedAt).fromNow()}
                    </span>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </Card>    </div>
  )
}
