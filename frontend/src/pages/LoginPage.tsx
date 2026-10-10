import { useRef, useState } from 'react'
import { Button, Card, Form, Input, Typography, App } from 'antd'
import type { InputRef } from 'antd'
import { useNavigate } from 'react-router-dom'
import { login } from '../api/auth'
import { ApiError } from '../api/client'
import { useAuthStore } from '../stores/authStore'

const PIPELINE_CHIPS = ['A1 需求解析', 'A2 方案生成', '审阅关 ①', 'A3 CAD 出图', 'A4 合规校验', '审阅关 ②', 'A5 知识沉淀']

function BrandLine() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <span
        aria-hidden
        style={{
          width: 30, height: 30, borderRadius: 7, background: '#1f2329',
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: 'none',
        }}
      >
        <span
          style={{
            width: 17, height: 17, borderRadius: '50%', border: '1.5px solid rgba(255,255,255,.82)',
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <span style={{ width: 4, height: 4, borderRadius: '50%', background: '#fff' }} />
        </span>
      </span>
      <div>
        <div style={{ fontWeight: 600, fontSize: 15 }}>Sonar-CAD <span style={{ fontWeight: 400, color: 'var(--text-3)' }}>v2</span></div>
        <div style={{ color: 'var(--text-3)', fontSize: 11.5 }}>智能辅助声呐装备设计系统</div>
      </div>
    </div>
  )
}

export default function LoginPage() {
  const navigate = useNavigate()
  const { message } = App.useApp()
  const setAuth = useAuthStore((s) => s.setAuth)
  const [submitting, setSubmitting] = useState(false)
  const [loginError, setLoginError] = useState('')
  const passwordRef = useRef<InputRef>(null)

  async function onFinish(values: { username: string; password: string }) {
    setSubmitting(true)
    setLoginError('')
    try {
      const result = await login(values.username, values.password)
      setAuth(result)
      message.success(`欢迎，${result.displayName || result.username}`)
      navigate('/', { replace: true })
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : '登录失败，请稍后重试'
      setLoginError(msg)
      passwordRef.current?.focus()
    } finally {
      setSubmitting(false)
    }
  }

  const form = (
    <Form layout="vertical" onFinish={onFinish} initialValues={{ username: '' }}>
      <Form.Item
        name="username" label="用户名"
        rules={[{ required: true, message: '请输入用户名' }]}
        validateStatus={loginError ? 'error' : undefined}
      >
        <Input placeholder="用户名" autoComplete="username" onChange={() => setLoginError('')} />
      </Form.Item>
      <Form.Item
        name="password" label="密码"
        rules={[{ required: true, message: '请输入密码' }]}
        validateStatus={loginError ? 'error' : undefined}
        help={loginError || undefined}
      >
        <Input.Password ref={passwordRef} placeholder="密码" autoComplete="current-password" onChange={() => setLoginError('')} />
      </Form.Item>
      <Button type="primary" htmlType="submit" block style={{ marginTop: 4 }} loading={submitting}>
        登 录
      </Button>
    </Form>
  )

  return (
    <div className="login-split">
      <div className="login-brand">
        {/* 声呐扫描纹样：同心环 + 缓扫，主题根植于声呐本身；reduced-motion 下静止 */}
        <div className="motif" aria-hidden>
          <span className="ring" />
          <span className="ring r2" />
          <span className="ring r3" />
          <span className="ring r4" />
          <span className="sweep" />
          <span className="core" />
        </div>
        <BrandLine />
        <div style={{ position: 'relative', maxWidth: 430 }}>
          <div style={{ fontSize: 19, fontWeight: 600, lineHeight: 1.5 }}>
            把一页需求，变成可审阅、可溯源的声呐装备设计。
          </div>
          <div className="pipe-chips" style={{ marginTop: 18, rowGap: 6 }}>
            {PIPELINE_CHIPS.map((c) => (
              <span key={c} className="chip">{c}</span>
            ))}
          </div>
        </div>
        <div style={{ position: 'relative', color: 'var(--text-3)', fontSize: 12 }}>
          参数可反查，门禁可裁决，全程留痕。
        </div>
      </div>
      <div className="login-main">
        <Card style={{ width: 380, borderRadius: 10 }} styles={{ body: { padding: '28px 32px 22px' } }}>
          <div className="login-compact">
            <BrandLine />
          </div>
          <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 4 }}>登录</div>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 18 }}>
            使用平台账号进入工作台
          </Typography.Paragraph>
          {form}
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, textAlign: 'center', marginTop: 14, marginBottom: 0 }}>
            角色：admin / designer / reviewer / viewer
          </Typography.Paragraph>
        </Card>
      </div>
    </div>
  )
}
