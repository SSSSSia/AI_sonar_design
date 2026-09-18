import { Card, Tag, Typography } from 'antd'
import type { Ir1 } from '../../api/workflow'

/** 总览页签：IR 快照版本条 + IR1 目标摘要 + 缺失项如实展示。 */
export function OverviewTab({
  sessionId,
  ir1,
  versions,
}: {
  sessionId: string
  ir1: Ir1 | null
  versions: { IR2?: number; IR3?: number; IR4?: number; IR5?: number }
}) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,2fr) minmax(260px,1fr)', gap: 16 }}>
      <Card title="执行记录" styles={{ body: { paddingTop: 8 } }}>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          阶段转移与裁决历史见「审阅与参数」页签的裁决记录；执行日志推送（log 消息）随 worker 日志流在 T4.3 后补。
        </Typography.Paragraph>
        {ir1 && (
          <div>
            <Typography.Text strong>IR1 检索子查询（{ir1.sub_queries.length} 条）</Typography.Text>
            <ol style={{ fontSize: 12, color: '#57606a', paddingLeft: 18, margin: '6px 0 0' }}>
              {ir1.sub_queries.map((q) => <li key={q}>{q}</li>)}
            </ol>
          </div>
        )}
        <Typography.Paragraph type="secondary" style={{ fontSize: 11, marginTop: 12 }}>
          会话 {sessionId}
        </Typography.Paragraph>
      </Card>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <Card title="IR 快照" extra={<Typography.Text type="secondary" style={{ fontSize: 11 }}>不可变 · A-12</Typography.Text>} size="small">
          {[
            { k: 'IR1 设计目标', v: 'v1' },
            { k: 'IR2 参数方案', v: versions.IR2 },
            { k: 'IR3 建模产物', v: versions.IR3 },
            { k: 'IR4 校验报告', v: versions.IR4 },
            { k: 'IR5 知识候选', v: versions.IR5 },
          ].map((row) => (
            <div key={row.k} className="row" style={{ padding: '5px 0', fontSize: 12.5, borderBottom: '1px solid #f0f1f3' }}>
              <span>{row.k}</span>
              <span style={{ flex: 1 }} />
              {row.v ? <Typography.Text code style={{ fontSize: 11 }}>v{row.v}</Typography.Text>
                : <Typography.Text type="secondary" style={{ fontSize: 11 }}>待生成</Typography.Text>}
            </div>
          ))}
        </Card>

        {ir1 && (
          <Card title="IR1 目标摘要" size="small">
            <div style={{ display: 'grid', gridTemplateColumns: '84px 1fr', rowGap: 6, fontSize: 12.5 }}>
              {ir1.objectives.map((o) => (
                <div key={o.dimension} style={{ display: 'contents' }}>
                  <span style={{ color: '#8b949e' }}>{o.dimension}</span>
                  <span>{String(o.value)} <Tag style={{ fontSize: 10, lineHeight: '14px', marginInlineEnd: 0 }}>{o.explicit ? '显式' : '推断'}</Tag></span>
                </div>
              ))}
            </div>
            {ir1.missing_items.length > 0 && (
              <div style={{ marginTop: 10, borderLeft: '3px solid #d97706', background: '#fdf3e3', padding: '6px 10px', fontSize: 12, borderRadius: 4 }}>
                <b>缺失项（如实）：</b>{ir1.missing_items.join('、')}
              </div>
            )}
          </Card>
        )}
      </div>
    </div>
  )
}
