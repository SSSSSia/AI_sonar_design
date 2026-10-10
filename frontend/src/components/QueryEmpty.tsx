import { Alert, Button, Spin } from 'antd'
import type { UseQueryResult } from '@tanstack/react-query'

function shortErr(e: unknown): string {
  const anyE = e as { message?: string; response?: { status?: number; data?: { message?: string } } }
  const msg = anyE?.response?.data?.message || anyE?.message || String(e)
  return String(msg).slice(0, 140)
}

/** 列表查询三态（审查 P0-2）：失败给红字 + 重试，与业务「暂无数据」明确区分——禁止把接口失败渲染成空库。
 *  emptyText 可定制业务化空态文案（审查 P2-10）。 */
export function QueryEmpty({ query, subject, emptyText }: { query: UseQueryResult<unknown, unknown>; subject: string; emptyText?: string }) {
  if (query.isError) {
    return (
      <div style={{ padding: '14px 16px' }}>
        <Alert
          type="error"
          showIcon
          message={`${subject}获取失败`}
          description={
            <div style={{ fontSize: 12 }}>
              <div style={{ color: '#57606a' }}>数据可能存在，仅暂时不可达：{shortErr(query.error)}</div>
              <Button size="small" style={{ marginTop: 8 }} onClick={() => void query.refetch()}>重试</Button>
            </div>
          }
        />
      </div>
    )
  }
  if (query.isLoading) {
    return (
      <div style={{ padding: 28, textAlign: 'center' }}>
        <Spin />
        <div style={{ marginTop: 8, fontSize: 12, color: '#8b949e' }}>加载中…</div>
      </div>
    )
  }
  return <div style={{ padding: 28, textAlign: 'center', color: '#8b949e', fontSize: 12.5 }}>{emptyText ?? '暂无数据'}</div>
}
