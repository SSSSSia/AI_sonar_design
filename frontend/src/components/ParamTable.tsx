import { useState } from 'react'
import { Button, Table, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { FileTextOutlined } from '@ant-design/icons'
import { ConfidenceBadge, SourceBadge } from './sourceMeta'
import type { Ir2Param } from '../api/workflow'

/** 取值 + 单位智能拼接：单位空/占位不显示，值内已含单位不重复（审查 P1-1）。 */
function joinValueUnit(value: unknown, unit?: string): string {
  const v = String(value ?? '').trim()
  const u = (unit ?? '').trim()
  if (!u || u === '-') return v
  if (v.includes(u)) return v
  return `${v} ${u}`
}

/** 解析 source_ref 的 doc#location（"towed_array_cad_knowledge.txt#第3页" 形态；KB 检索引用）。 */
export function parseKbRef(sourceRef: string | undefined): { docId: string; location: string } | null {
  const s = String(sourceRef ?? '')
  const hash = s.indexOf('#')
  if (hash <= 0 || hash >= s.length - 1) return null
  const docId = s.slice(0, hash).trim()
  const location = s.slice(hash + 1).trim()
  // kb_id 式引用或纯公式键不算知识文档引用（无 doc 段）
  if (!docId || docId.includes('kb_formulas:')) return null
  return { docId, location }
}

/**
 * IR2 参数表：source_type 四色徽标 + 置信度 + 行点击溯源（A-06 四件套齐全）。
 * 出处列两行制 + 截断展开（审查 P1-1）：全文进溯源抽屉，表格保持可扫读。
 * onKbRef（PHASE-006）：retrieved 出处参数显示「原文」按钮，弹知识库原文预览。
 */
export function ParamTable({ params, onTrace, onKbRef }: {
  params: Ir2Param[]
  onTrace: (p: Ir2Param) => void
  onKbRef?: (p: Ir2Param, ref: { docId: string; location: string }) => void
}) {
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
    { title: '来源', dataIndex: 'source_type', width: 96, render: (v) => <SourceBadge type={v} /> },
    {
      title: '出处 / 推导',
      key: 'ref',
      render: (_, r) => {
        const ref = r.source_type === 'retrieved' ? parseKbRef(r.source_ref) : null
        return (
          <div style={{ maxWidth: 360 }}>
            <Typography.Paragraph ellipsis={{ rows: 1, expandable: true, symbol: '展开' }} style={{ fontSize: 12, marginBottom: 0 }}>
              {String(r.source_ref ?? '')}
            </Typography.Paragraph>
            <Typography.Paragraph
              ellipsis={{ rows: 1, expandable: true, symbol: '展开' }}
              style={{ fontSize: 11, color: 'var(--text-3)', fontFamily: 'monospace', marginBottom: 0 }}
            >
              {String(r.derivation ?? '')}
            </Typography.Paragraph>
            {onKbRef && ref && (
              <Button
                size="small"
                type="link"
                icon={<FileTextOutlined />}
                style={{ padding: 0, fontSize: 11.5, height: 18 }}
                onClick={(e) => { e.stopPropagation(); onKbRef(r, ref) }}
              >
                查看知识原文
              </Button>
            )}
          </div>
        )
      },
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
        style: { cursor: 'pointer', background: hover === r.id ? 'rgba(6,182,212,.06)' : undefined },
        onMouseEnter: () => setHover(r.id),
        onMouseLeave: () => setHover(null),
      })}
    />
  )
}
