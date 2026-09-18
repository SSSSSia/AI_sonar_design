import { useState } from 'react'
import { Button, Card, Form, Input, Typography, App } from 'antd'
import { useNavigate } from 'react-router-dom'
import { login } from '../api/auth'
import { ApiError } from '../api/client'
import { useAuthStore } from '../stores/authStore'

export default function LoginPage() {
  const navigate = useNavigate()
  const { message } = App.useApp()
  const setAuth = useAuthStore((s) => s.setAuth)
  const [submitting, setSubmitting] = useState(false)

  async function onFinish(values: { username: string; password: string }) {
    setSubmitting(true)
    try {
      const result = await login(values.username, values.password)
      setAuth(result)
      message.success(`欢迎，${result.displayName || result.username}`)
      navigate('/', { replace: true })
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : '登录失败，请稍后重试'
      message.error(msg)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh', display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center', background: '#f5f6f8', padding: 16,
      }}
    >
      <Card style={{ width: 380, borderRadius: 10 }} styles={{ body: { padding: '32px 32px 22px' } }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <div style={{ fontSize: 17, fontWeight: 700 }}>Sonar-CAD v2</div>
          <div style={{ color: '#8b949e', fontSize: 12, marginTop: 4 }}>智能辅助声呐装备设计系统</div>
        </div>
        <Form layout="vertical" onFinish={onFinish} initialValues={{ username: '' }}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input placeholder="用户名" autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password placeholder="密码" autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block style={{ marginTop: 4 }} loading={submitting}>
            登 录
          </Button>
        </Form>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, textAlign: 'center', marginTop: 14, marginBottom: 0 }}>
          角色：admin / designer / reviewer / viewer
        </Typography.Paragraph>
      </Card>
    </div>
  )
}
