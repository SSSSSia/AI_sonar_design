import { useEffect, useRef } from 'react'
import { Card, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import type { Ir1 } from '../../api/workflow'
import { useLogStore } from '../../stores/logStore'

const LOG_LEVEL_COLOR: Record<string, string> = {
  debug: '#8b949e', info: '#57606a', ok: '#2b8a3e', warn: '#d97706', error: '#c92a2a',
}

/** 实时日志面板（WS log 消息）。日志是流不是账本：不落库、刷新即空（如实标注）。 */
function LiveLog({ sessionId }: { sessionId: string }) {
  const lines = useLogStore((s) => s.bySession[sessionId])
  const boxRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    boxRef.current?.scrollTo({ top: boxRef.current.scrollHeight })
  }, [lines?.length])

  return (
    <Card
      title="实时日志"
      size="small"
      extra={<Typography.Text type="secondary" style={{ fontSize: 11 }}>worker 步骤推送 · 不落库</Typography.Text>}
    >
      {(lines?.length ?? 0) === 0 ? (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          暂无实时日志。会话运行时各阶段步骤在此滚动显示；刷新后清空，权威记录见 IR 快照与溯源链。
        </Typography.Text>
      ) : (
        <div
          ref={boxRef}
          className="mono"
          style={{ maxHeight: 220, overflow: 'auto', background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 12px', fontSize: 11.5, lineHeight: 1.9 }}
        >
          {lines!.slice(-80).map((l, i) => (
            <div key={`${l.at}-${i}`}>
              <span style={{ color: '#8b949e', marginRight: 8 }}>{dayjs(l.at).format('HH:mm:ss')}</span>
              <span style={{ color: LOG_LEVEL_COLOR[l.level] ?? '#57606a' }}>{l.text}</span>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}

/** 总览页签：实时日志 + IR 快照版本条 + IR1 目标摘要 + 缺失项如实展示。 */
export function OverviewTab({
  sessionId,
  ir1,
  versions,
}: {
  sessionId: string
  ir1: Ir1 | null
  versions: { IR1?: number; IR2?: number; IR3?: number; IR4?: number; IR5?: number }
}) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,2fr) minmax(260px,1fr)', gap: 16 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <Card title="执行记录" styles={{ body: { paddingTop: 8 } }}>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
            阶段转移与裁决历史见「审阅与参数」页签的裁决记录。
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
        <LiveLog sessionId={sessionId} />
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <Card title="IR 快照" extra={<Typography.Text type="secondary" style={{ fontSize: 11 }}>不可变 · A-12</Typography.Text>} size="small">
          {[
            { k: 'IR1 设计目标', v: versions.IR1 },
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
