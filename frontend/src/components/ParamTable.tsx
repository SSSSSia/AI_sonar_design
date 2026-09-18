import { useState } from 'react'
import { Table, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ConfidenceBadge, SourceBadge } from './sourceMeta'
import type { Ir2Param, SourceType } from '../api/workflow'

/** 取值 + 单位智能拼接：单位空/占位不显示，值内已含单位不重复（审查 P1-1）。 */
function joinValueUnit(value: unknown, unit?: string): string {
  const v = String(value ?? '').trim()
  const u = (unit ?? '').trim()
  if (!u || u === '-') return v
  if (v.includes(u)) return v
  return `${v} ${u}`
}

/**
 * IR2 参数表：source_type 四色徽标 + 置信度 + 行点击溯源（A-06 四件套齐全）。
 * 出处列两行制 + 截断展开（审查 P1-1）：全文进溯源抽屉，表格保持可扫读。
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
      render: (_, r) => <span><b>{joinValueUnit(r.value, r.unit)}</b></span>,
    },
    { title: '来源', dataIndex: 'source_type', width: 96, render: (v: SourceType) => <SourceBadge type={v} /> },
    {
      title: '出处 / 推导',
      key: 'ref',
      render: (_, r) => (
        <div style={{ maxWidth: 360 }}>
          <Typography.Paragraph ellipsis={{ rows: 1, expandable: true, symbol: '展开' }} style={{ fontSize: 12, marginBottom: 0 }}>
            {String(r.source_ref ?? '')}
          </Typography.Paragraph>
          <Typography.Paragraph
            ellipsis={{ rows: 1, expandable: true, symbol: '展开' }}
            style={{ fontSize: 11, color: '#8b949e', fontFamily: 'monospace', marginBottom: 0 }}
          >
            {String(r.derivation ?? '')}
          </Typography.Paragraph>
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
