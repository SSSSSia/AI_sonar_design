import { useEffect, useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import {
  Avatar, Badge, Button, Drawer, Dropdown, Empty, Grid, Layout, List, Menu, Popover, Space, Tag, Tooltip, Typography,
} from 'antd'
import {
  BellOutlined, DashboardOutlined, DatabaseOutlined, DeleteOutlined, FolderOutlined, LogoutOutlined,
  MenuFoldOutlined, MenuOutlined, MenuUnfoldOutlined, SettingOutlined, UserOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { useAuthStore } from '../stores/authStore'
import { checkNavGuard } from '../lib/navGuard'
import { useNotifyStore, type AppNotification } from '../stores/notifyStore'
import { gateLabel } from '../lib/stageLabel'
import {
  clearNotifications, deleteNotification, listNotifications, markAllNotificationsRead, markNotificationRead,
} from '../api/notifications'
import type { ServerNotification } from '../api/notifications'

dayjs.extend(relativeTime)

const { Sider, Header, Content } = Layout

const ROLE_LABEL: Record<string, string> = {
  admin: '管理员', designer: '设计', reviewer: '审阅', viewer: '只读',
}

/** 菜单按角色过滤（审查 P1-5）+ 分组标题（审查 P2-2：工作台/知识/系统）。 */
function menuGroupsFor(roles: string[]) {
  const groups = [
    {
      key: 'g-workbench', label: '工作台', type: 'group' as const,
      children: [
        { key: '/', icon: <DashboardOutlined />, label: '仪表盘' },
        { key: '/projects', icon: <FolderOutlined />, label: '项目管理' },
      ],
    },
    {
      key: 'g-knowledge', label: '知识', type: 'group' as const,
      children: [{ key: '/kb', icon: <DatabaseOutlined />, label: '知识库管理' }],
    },
  ]
  if (roles.includes('admin')) {
    groups.push({
      key: 'g-system', label: '系统', type: 'group' as const,
      children: [{ key: '/admin', icon: <SettingOutlined />, label: '系统管理' }],
    })
  }
  return groups
}

/** 品牌标记：石墨底声呐同心环。中性灰绘制，装饰不占用交互蓝。
 *  导出供项目卡片等处复用（2026-09-20 项目页视觉重塑）。 */
export function SonarMark({ size = 24 }: { size?: number }) {
  return (
    <span
      aria-hidden
      style={{
        width: size, height: size, borderRadius: Math.max(5, size * 0.22), background: '#1f2329',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: 'none',
      }}
    >
      <span
        style={{
          width: size * 0.6, height: size * 0.6, borderRadius: '50%',
          border: '1.5px solid rgba(255,255,255,.82)',
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        }}
      >
        <span style={{ width: size * 0.16, height: size * 0.16, borderRadius: '50%', background: '#fff' }} />
      </span>
    </span>
  )
}

function Brand({ collapsed = false }: { collapsed?: boolean }) {
  return (
    <div
      style={{
        padding: collapsed ? '16px 0 12px' : '13px 16px 11px',
        borderBottom: '1px solid var(--hairline)',
        display: 'flex', alignItems: 'center', justifyContent: collapsed ? 'center' : 'flex-start', gap: 9,
      }}
    >
      <SonarMark />
      {!collapsed && (
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 13.5, display: 'flex', alignItems: 'center', gap: 6 }}>
            Sonar-CAD
            <span
              className="mono"
              style={{ fontSize: 10, color: 'var(--text-2)', background: 'var(--hairline)', padding: '0 5px', borderRadius: 4, lineHeight: '16px' }}
            >
              v2
            </span>
          </div>
          <div style={{ color: 'var(--text-3)', fontSize: 11, marginTop: 1 }}>智能辅助声呐装备设计系统</div>
        </div>
      )}
    </div>
  )
}

/** 服务端通知行 → 本地展示模型（T11.1）；review_request 无服务端标题时沿用 gate 派生。 */
function toAppNotification(row: ServerNotification): AppNotification {
  const p = (row.payload ?? {}) as Record<string, unknown>
  let title = row.title ?? ''
  let meta: string | undefined
  if (row.type === 'review_request') {
    title = title || `${gateLabel(String(p.gate ?? ''))} 待裁决`
    meta = typeof p.summary === 'string' ? p.summary : undefined
  } else if (row.type === 'alert') {
    title = title || `系统告警 ${String(p.status ?? '')}`.trim()
    meta = Array.isArray(p.summaries) ? p.summaries.join('；') : undefined
  }
  return {
    id: row.id,
    key: row.client_key,
    kind: (row.type as AppNotification['kind']) ?? 'info',
    title,
    meta,
    sessionId: row.session_id || undefined,
    time: Date.parse(row.created_at ?? '') || 0,
    read: row.read,
  }
}

function NotificationBell() {
  const navigate = useNavigate()
  const token = useAuthStore((s) => s.token)
  const items = useNotifyStore((s) => s.items)
  const markRead = useNotifyStore((s) => s.markRead)
  const markAllRead = useNotifyStore((s) => s.markAllRead)
  const remove = useNotifyStore((s) => s.remove)
  const clearRead = useNotifyStore((s) => s.clearRead)
  const clearAll = useNotifyStore((s) => s.clearAll)
  const [allOpen, setAllOpen] = useState(false)
  const unread = items.filter((i) => !i.read).length

  // T11.1：登录后拉取服务端通知（事实源），与 WS 实时项按 key 合并；失败静默（WS 仍在）
  useEffect(() => {
    if (!token) return
    let alive = true
    listNotifications(50)
      .then((resp) => {
        if (alive) useNotifyStore.getState().hydrate(resp.items.map(toAppNotification))
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [token])

  // 列表项（通知中心与「全部通知」抽屉共用）：点击直达会话，悬停显示删除
  const renderItem = (item: (typeof items)[number], inDrawer: boolean) => (
    <List.Item
      style={{
        background: item.read ? undefined : '#f7f9fd', padding: '8px 10px', borderRadius: 6,
        cursor: item.sessionId ? 'pointer' : undefined,
      }}
      actions={[
        <Button
          key="del" type="text" size="small" aria-label="删除该通知"
          icon={<DeleteOutlined style={{ color: '#c92a2a' }} />}
          onClick={(e) => {
            e.stopPropagation()
            remove(item.id)
            void deleteNotification(item.id).catch(() => {})
          }}
        />,
      ]}
      onClick={() => {
        if (!item.sessionId) return
        markRead(item.id)
        void markNotificationRead(item.id).catch(() => {})
        if (inDrawer) setAllOpen(false)
        navigate(`/sessions/${item.sessionId}`)
      }}
    >
      <List.Item.Meta
        title={
          <span style={{ fontSize: inDrawer ? 13.5 : 13, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            {!item.read && (
              <span aria-hidden style={{
                width: 6, height: 6, borderRadius: 3, flex: 'none',
                background: item.kind === 'alert' ? '#c92a2a' : '#2563eb',
              }} />
            )}
            {item.title}
          </span>
        }
        description={
          <span style={{ fontSize: 12 }}>
            {item.meta ? `${item.meta} · ` : ''}{dayjs(item.time).fromNow()}
            {item.sessionId ? ' · 点击直达' : ''}
          </span>
        }
      />
    </List.Item>
  )

  // 管理动作条（popover 头部与抽屉 extra 共用）：状态更新与持久化并行，失败不影响本地视图
  const manageBar = (inDrawer: boolean) => (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      {unread > 0 && (
        <Button type="link" size="small" onClick={() => {
          markAllRead()
          void markAllNotificationsRead().catch(() => {})
        }}>全部已读</Button>
      )}
      {items.some((i) => i.read) && (
        <Button type="link" size="small" onClick={() => {
          clearRead()
          void clearNotifications('read').catch(() => {})
        }}>清除已读</Button>
      )}
      {items.length > 0 && (
        <Button type="link" size="small" danger onClick={() => {
          clearAll()
          void clearNotifications('all').catch(() => {})
          if (inDrawer) setAllOpen(false)
        }}>清空</Button>
      )}
    </span>
  )

  const popoverContent = (
    <div style={{ width: 330 }}>
      <div className="row" style={{ padding: '4px 4px 8px' }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>通知</Typography.Text>
        <span style={{ flex: 1 }} />
        {manageBar(false)}
      </div>
      {items.length === 0 ? (
        <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', textAlign: 'center', padding: 16 }}>
          暂无通知
        </Typography.Text>
      ) : (
        <>
          <List size="small" dataSource={items.slice(0, 8)} renderItem={(item) => renderItem(item, false)} />
          {items.length > 8 && (
            <Button type="link" size="small" block onClick={() => setAllOpen(true)}>
              查看全部 {items.length} 条
            </Button>
          )}
        </>
      )}
    </div>
  )

  return (
    <>
      <Popover content={popoverContent} trigger="click" placement="bottomRight">
        <Badge count={unread} size="small">
          <Button type="text" icon={<BellOutlined />} aria-label="通知中心" />
        </Badge>
      </Popover>
      <Drawer
        title={`全部通知（${items.length} 条${unread > 0 ? ` · 未读 ${unread}` : ''}）`}
        width={420}
        open={allOpen}
        onClose={() => setAllOpen(false)}
        extra={manageBar(true)}
      >
        {items.length === 0 ? (
          <Empty description="暂无通知" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <List size="small" dataSource={items} renderItem={(item) => renderItem(item, true)} />
        )}
        <Typography.Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 12 }}>
          通知已持久化到服务端（T11.1，按用户隔离）：刷新/换设备登录后仍在；待裁决类仅推送给审阅角色；删除/清空同步生效。历史裁决记录不受影响，可在各会话「审阅与参数」页签查看。
        </Typography.Text>
      </Drawer>
    </>
  )
}

export default function AppLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const screens = Grid.useBreakpoint()
  const [collapsed, setCollapsed] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)
  const username = useAuthStore((s) => s.username)
  const displayName = useAuthStore((s) => s.displayName)
  const roles = useAuthStore((s) => s.roles)
  const clear = useAuthStore((s) => s.clear)

  // 响应式（PHASE-004 §2.5）：<768 侧栏收进抽屉；<1200 自动折叠为图标栏
  const hideSider = screens.md === false
  const autoCollapse = screens.xl === false
  const effectiveCollapsed = autoCollapse || collapsed

  const MENU_ITEMS = menuGroupsFor(roles)
  const flatKeys = MENU_ITEMS.flatMap((g) => ('children' in g ? g.children : [])).map((m) => m.key)
  const selectedKey =
    flatKeys.find((k) => k !== '/' && location.pathname.startsWith(k)) ?? '/'

  // 收缩态把分组平铺为纯图标（分组标题在窄栏里会以文字夹在图标间，观感差），
  // 组间插分隔线保留层级；antd 收缩态对平铺项自动出悬浮提示。
  const siderMenuItems = effectiveCollapsed
    ? MENU_ITEMS.flatMap((g, gi) => {
        const kids = ('children' in g ? g.children.map((m) => ({ ...m })) : [])
        return gi === MENU_ITEMS.length - 1 ? kids : [...kids, { type: 'divider' as const, key: `${g.key}-div` }]
      })
    : MENU_ITEMS

  return (
    <Layout style={{ height: '100vh', overflow: 'hidden' }}>
      {!hideSider && (
        <Sider
          theme="light"
          width={216}
          collapsed={effectiveCollapsed}
          trigger={null}
          className="app-sider"
          style={{ borderRight: '1px solid var(--border)' }}
        >
          <Brand collapsed={effectiveCollapsed} />
          <Menu
            mode="inline"
            style={{ borderInlineEnd: 'none', paddingTop: 8, flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden' }}
            selectedKeys={[selectedKey]}
            items={siderMenuItems}
            onClick={({ key }) => {
              // 草稿守卫（新会话页注册）：返回 false 表示确认框待处理，取消本次跳转
              if (!checkNavGuard(key)) return
              navigate(key)
            }}
          />
          {/* 自绘收起/展开按钮替代 antd 默认触发条；窄屏强制收缩时该按钮无效，隐藏 */}
          {!autoCollapse && (
            <div style={{ borderTop: '1px solid var(--hairline)', padding: '6px 8px 8px' }}>
              <Tooltip title={effectiveCollapsed ? '展开侧栏' : undefined}>
                <Button
                  type="text" block
                  aria-label={effectiveCollapsed ? '展开侧栏' : '收起侧栏'}
                  icon={effectiveCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                  onClick={() => setCollapsed(!effectiveCollapsed)}
                >
                  {!effectiveCollapsed && '收起侧栏'}
                </Button>
              </Tooltip>
            </div>
          )}
        </Sider>
      )}
      <Drawer
        placement="left"
        width={264}
        open={mobileNav}
        onClose={() => setMobileNav(false)}
        title={<Brand />}
        styles={{ body: { padding: '8px 0' }, header: { padding: 0, border: 'none' } }}
      >
        <Menu
          mode="inline"
          style={{ borderInlineEnd: 'none' }}
          selectedKeys={[selectedKey]}
          items={MENU_ITEMS}
          onClick={({ key }) => {
            if (!checkNavGuard(key)) return
            navigate(key)
            setMobileNav(false)
          }}
        />
      </Drawer>
      <Layout style={{ overflowY: 'auto' }}>
        <Header
          style={{
            background: '#fff', borderBottom: '1px solid var(--border)',
            padding: screens.md ? '0 24px' : '0 12px',
            display: 'flex', alignItems: 'center', gap: 12, lineHeight: '52px',
            position: 'sticky', top: 0, zIndex: 20,
          }}
        >
          {hideSider && (
            <>
              <Button type="text" icon={<MenuOutlined />} onClick={() => setMobileNav(true)} aria-label="打开导航" />
              <Space size={8}>
                <SonarMark size={22} />
                <span style={{ fontWeight: 600, fontSize: 13.5 }}>Sonar-CAD</span>
              </Space>
            </>
          )}
          <span style={{ flex: 1 }} />
          <NotificationBell />
          <Dropdown
            menu={{
              items: [
                {
                  key: 'logout',
                  icon: <LogoutOutlined />,
                  label: '退出登录',
                  onClick: () => {
                    clear()
                    navigate('/login')
                  },
                },
              ],
            }}
          >
            <Space style={{ cursor: 'pointer' }}>
              <Avatar size={26} icon={<UserOutlined />} style={{ background: '#e8eaed', color: '#57606a' }} />
              <span style={{ fontSize: 13 }}>{displayName || username}</span>
              <Space size={2}>
                {roles.slice(0, 2).map((r) => (
                  <Tag key={r} style={{ fontSize: 11, lineHeight: '16px', marginInlineEnd: 0 }}>
                    {ROLE_LABEL[r] ?? r}
                  </Tag>
                ))}
              </Space>
            </Space>
          </Dropdown>
        </Header>
        <Content style={{ padding: screens.md ? '20px 24px 48px' : '12px 12px 32px' }}>
          <div style={{ maxWidth: 1320, margin: '0 auto' }}>
            <Outlet />
          </div>
        </Content>
      </Layout>
    </Layout>
  )
}
