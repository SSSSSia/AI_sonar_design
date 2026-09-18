import { useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Avatar, Badge, Button, Dropdown, Grid, Layout, List, Menu, Popover, Space, Tag, Typography } from 'antd'
import {
  DashboardOutlined, DatabaseOutlined, EditOutlined, LogoutOutlined, SettingOutlined, UserOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { useAuthStore } from '../stores/authStore'
import { useNotifyStore } from '../stores/notifyStore'

dayjs.extend(relativeTime)

const { Sider, Header, Content } = Layout

const MENU_ITEMS = [
  { key: '/', icon: <DashboardOutlined />, label: '仪表盘' },
  { key: '/sessions/new', icon: <EditOutlined />, label: '需求输入' },
  { key: '/kb', icon: <DatabaseOutlined />, label: '知识库管理' },
  { key: '/admin', icon: <SettingOutlined />, label: '系统管理' },
]

const ROLE_LABEL: Record<string, string> = {
  admin: '管理员', designer: '设计', reviewer: '审阅', viewer: '只读',
}

function NotificationBell() {
  const items = useNotifyStore((s) => s.items)
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
            <List.Item style={{ background: item.read ? undefined : '#f7f9fd', padding: '8px 10px', borderRadius: 6 }}>
              <List.Item.Meta
                title={<span style={{ fontSize: 13 }}>{item.title}</span>}
                description={
                  <span style={{ fontSize: 12 }}>
                    {item.meta ? `${item.meta} · ` : ''}{dayjs(item.time).fromNow()}
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
        <Button type="text" aria-label="通知中心">🔔</Button>
      </Badge>
    </Popover>
  )
}

export default function AppLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const screens = Grid.useBreakpoint()
  const [collapsed, setCollapsed] = useState(false)
  const username = useAuthStore((s) => s.username)
  const displayName = useAuthStore((s) => s.displayName)
  const roles = useAuthStore((s) => s.roles)
  const clear = useAuthStore((s) => s.clear)

  // 响应式（PHASE-004 §2.5）：<1200 侧栏收窄为图标栏，<768 隐藏侧栏
  const hideSider = screens.md === false
  const autoCollapse = screens.xl === false

  const selectedKey =
    MENU_ITEMS.find((m) => m.key !== '/' && location.pathname.startsWith(m.key))?.key ?? '/'

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {!hideSider && (
        <Sider
          theme="light"
          width={216}
          collapsible
          collapsed={autoCollapse || collapsed}
          onCollapse={setCollapsed}
          style={{ borderRight: '1px solid #e5e7eb' }}
        >
          <div style={{ padding: '18px 20px 14px', borderBottom: '1px solid #e5e7eb' }}>
            {!collapsed && (
              <>
                <div style={{ fontWeight: 700, fontSize: 14 }}>Sonar-CAD v2</div>
                <div style={{ color: '#8b949e', fontSize: 11, marginTop: 3 }}>智能辅助声呐装备设计系统</div>
              </>
            )}
          </div>
          <Menu
            mode="inline"
            style={{ borderInlineEnd: 'none', paddingTop: 8 }}
            selectedKeys={[selectedKey]}
            items={MENU_ITEMS}
            onClick={({ key }) => navigate(key)}
          />
        </Sider>
      )}
      <Layout>
        <Header
          style={{
            background: '#fff', borderBottom: '1px solid #e5e7eb', padding: screens.md ? '0 24px' : '0 12px',
            display: 'flex', alignItems: 'center', gap: 12, height: 52, lineHeight: '52px',
          }}
        >
          {hideSider && <span style={{ fontWeight: 700 }}>Sonar-CAD v2</span>}
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
        <Content style={{ padding: screens.md ? '20px 24px 40px' : '12px 12px 32px' }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
