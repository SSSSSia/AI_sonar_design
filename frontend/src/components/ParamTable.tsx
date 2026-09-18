import { useState } from 'react'
import { Table, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ConfidenceBadge, SourceBadge } from './sourceMeta'
import type { Ir2Param, SourceType } from '../api/workflow'

/**
 * IR2 参数表：source_type 四色徽标 + 置信度 + 行点击溯源（A-06 四件套齐全）。
 * 响应式：横向滚动承载列，不换行折断。
 */
export function ParamTable({ params, onTrace }: { params: Ir2Param[]; onTrace: (p: Ir2Param) => void }) {
  const [hover, setHover] = useState<string | null>(null)
  const columns: ColumnsType<Ir2Param> = [
    { title: 'ID', dataIndex: 'id', width: 76, render: (v) => <Typography.Text code style={{ fontSize: 11 }}>{v}</Typography.Text> },
    {
      title: '参数',
      dataIndex: 'name',
      width: 160,
      render: (v, r) => (
        <a onClick={() => onTrace(r)} style={{ fontWeight: 500 }}>{v}</a>
      ),
    },
    {
      title: '取值',
      key: 'value',
      width: 130,
      render: (_, r) => <span><b>{String(r.value)}</b> {r.unit}</span>,
    },
    { title: '来源', dataIndex: 'source_type', width: 96, render: (v: SourceType) => <SourceBadge type={v} /> },
    {
      title: '出处 / 推导',
      key: 'ref',
      render: (_, r) => (
        <div>
          <div style={{ fontSize: 12 }}>{r.source_ref}</div>
          <div style={{ fontSize: 11, color: '#8b949e', fontFamily: 'monospace' }}>{r.derivation}</div>
        </div>
      ),
    },
    { title: '置信度', dataIndex: 'confidence', width: 90, render: (v) => <ConfidenceBadge value={v} /> },
  ]
  return (
    <Table<Ir2Param>
      rowKey="id"
      size="small"
      dataSource={params}
      columns={columns}
      pagination={false}
      scroll={{ x: 720 }}
      onRow={(r) => ({
        onClick: () => onTrace(r),
        style: { cursor: 'pointer', background: hover === r.id ? '#f7f9fd' : undefined },
        onMouseEnter: () => setHover(r.id),
        onMouseLeave: () => setHover(null),
      })}
    />
  )
}
