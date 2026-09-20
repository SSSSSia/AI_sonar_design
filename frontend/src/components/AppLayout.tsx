import { useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import {
  Avatar, Badge, Button, Drawer, Dropdown, Grid, Layout, List, Menu, Popover, Space, Tag, Typography,
} from 'antd'
import {
  BellOutlined, DashboardOutlined, DatabaseOutlined, EditOutlined, FolderOutlined, LogoutOutlined,
  MenuOutlined, SettingOutlined, UserOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { useAuthStore } from '../stores/authStore'
import { useNotifyStore } from '../stores/notifyStore'

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
        { key: '/sessions/new', icon: <EditOutlined />, label: '需求输入' },
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

/** 品牌标记：石墨底声呐同心环。中性灰绘制，装饰不占用交互蓝。 */
function SonarMark({ size = 24 }: { size?: number }) {
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

function NotificationBell() {
  const navigate = useNavigate()
  const items = useNotifyStore((s) => s.items)
  const markRead = useNotifyStore((s) => s.markRead)
  const markAllRead = useNotifyStore((s) => s.markAllRead)
  const unread = items.filter((i) => !i.read).length

  const content = (
    <div style={{ width: 320 }}>
      <div className="row" style={{ padding: '4px 4px 8px' }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>通知</Typography.Text>
        <span style={{ flex: 1 }} />
        {unread > 0 && (
          <Button type="link" size="small" onClick={markAllRead}>全部已读</Button>
        )}
      </div>
      {items.length === 0 ? (
        <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', textAlign: 'center', padding: 16 }}>
          暂无通知
        </Typography.Text>
      ) : (
        <List
          size="small"
          dataSource={items.slice(0, 8)}
          renderItem={(item) => (
            <List.Item
              style={{
                background: item.read ? undefined : '#f7f9fd', padding: '8px 10px', borderRadius: 6,
                cursor: item.sessionId ? 'pointer' : undefined,
              }}
              onClick={() => {
                if (!item.sessionId) return
                markRead(item.id)
                navigate(`/sessions/${item.sessionId}`)
              }}
            >
              <List.Item.Meta
                title={
                  <span style={{ fontSize: 13, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    {!item.read && <span aria-hidden style={{ width: 6, height: 6, borderRadius: 3, background: '#2563eb', flex: 'none' }} />}
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
          )}
        />
      )}
    </div>
  )

  return (
    <Popover content={content} trigger="click" placement="bottomRight">
      <Badge count={unread} size="small">
        <Button type="text" icon={<BellOutlined />} aria-label="通知中心" />
      </Badge>
    </Popover>
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

  const MENU_ITEMS = menuGroupsFor(roles)
  const flatKeys = MENU_ITEMS.flatMap((g) => ('children' in g ? g.children : [])).map((m) => m.key)
  const selectedKey =
    flatKeys.find((k) => k !== '/' && location.pathname.startsWith(k)) ?? '/'

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {!hideSider && (
        <Sider
          theme="light"
          width={216}
          collapsible
          collapsed={autoCollapse || collapsed}
          onCollapse={setCollapsed}
          style={{ borderRight: '1px solid var(--border)' }}
        >
          <Brand collapsed={autoCollapse || collapsed} />
          <Menu
            mode="inline"
            style={{ borderInlineEnd: 'none', paddingTop: 8 }}
            selectedKeys={[selectedKey]}
            items={MENU_ITEMS}
            onClick={({ key }) => navigate(key)}
          />
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
            navigate(key)
            setMobileNav(false)
          }}
        />
      </Drawer>
      <Layout>
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
