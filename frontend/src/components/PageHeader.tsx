import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from 'antd'
import { ArrowLeftOutlined } from '@ant-design/icons'

/**
 * 二级页统一页头（2026-09-24 用户反馈：页面缺返回入口）：
 * 左上角返回按钮 + 标题，extra 落右侧。backTo 用显式路径，不用 history 回退——
 * 外链/刷新直达时 history 上一页可能不是站内页。
 * onBack 提供时替代默认跳转（供页面插入草稿确认等拦截逻辑）。
 */
export function PageHeader({ title, backTo, onBack, extra, children }: {
  title: ReactNode
  backTo: string
  onBack?: () => void
  extra?: ReactNode
  children?: ReactNode
}) {
  const navigate = useNavigate()
  return (
    <div className="row" style={{ marginBottom: 14, flexWrap: 'wrap' }}>
      <Button icon={<ArrowLeftOutlined />} onClick={() => (onBack ? onBack() : navigate(backTo))}>返回</Button>
      <div className="page-head" style={{ margin: 0 }}>{title}</div>
      {children}
      <span style={{ flex: 1 }} />
      {extra}
    </div>
  )
}
