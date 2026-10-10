import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Card, Empty, Input, Select, Space, Tag, Typography } from 'antd'
import { errText } from '../../api/client'
import { listBases, searchKb, type KbSearchHit, type KbSearchResult } from '../../api/kb'

/**
 * U3.10 知识检索 Tab：包装三级降级检索（POST /api/v1/kb/search）。
 * 命中层级与降级轨迹如实展示（A-14/A-16）；分数仅向量级真实存在（Milvus 距离/重排序分），
 * 非向量级聚合呈现且不带分数。范围可选库（多选）或全部（含未归库文档不参与检索）。
 */

const LEVEL_LABEL: Record<string, { text: string; color: string }> = {
  local: { text: 'local · SDK 图谱', color: 'purple' },
  basic: { text: 'basic · SDK 向量', color: 'geekblue' },
  'vector+graph': { text: 'vector+graph · 向量+图谱', color: 'cyan' },
  vector: { text: 'vector · 向量（Milvus）', color: 'cyan' },
  graph: { text: 'graph · 图谱（Neo4j 实体层）', color: 'green' },
  text: { text: 'text · 文本关键词', color: 'orange' },
  unavailable: { text: 'unavailable · 范围内无可检索知识', color: 'red' },
}

function HitCard({ hit }: { hit: KbSearchHit }) {
  return (
    <div
      style={{
        border: '1px solid var(--border)', borderRadius: 8, padding: '12px 14px',
        background: 'var(--card-deep, rgba(255,255,255,.02))',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        {hit.score !== null && hit.score !== undefined ? (
          <span className="mono" style={{ fontSize: 11, color: 'var(--accent)', background: 'var(--accent-bg)', borderRadius: 4, padding: '1px 6px' }}>
            {hit.score.toFixed(3)}
          </span>
        ) : (
          <Tag style={{ fontSize: 11 }}>聚合</Tag>
        )}
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>{hit.doc_name || '未知来源'}</Typography.Text>
        {hit.location && <span className="mono" style={{ fontSize: 11, color: 'var(--text-3)' }}>{hit.location}</span>}
      </div>
      <div
        style={{
          marginTop: 8, fontSize: 12.5, color: 'var(--text-2)', lineHeight: 1.9,
          borderLeft: '2px solid rgba(6,182,212,.35)', paddingLeft: 10,
          whiteSpace: 'pre-wrap', maxHeight: 180, overflow: 'auto',
        }}
      >
        {hit.text}
      </div>
    </div>
  )
}

export function SearchTab() {
  const [query, setQuery] = useState('')
  const [scope, setScope] = useState<string[]>([])
  const [submitted, setSubmitted] = useState<{ q: string; kbIds: string[] | null } | null>(null)

  const bases = useQuery({ queryKey: ['kb-bases'], queryFn: listBases })

  const search = useQuery({
    queryKey: ['kb-search', submitted?.q, submitted?.kbIds],
    queryFn: () => searchKb({ query: submitted!.q, kb_ids: submitted!.kbIds }),
    enabled: submitted !== null,
    retry: false,
  })

  const result: KbSearchResult | undefined = search.data
  const level = result ? (LEVEL_LABEL[result.level] ?? { text: result.level, color: 'default' }) : null

  return (
    <Card size="small" title="知识检索" styles={{ body: { padding: 16 } }}>
      <Space.Compact style={{ width: '100%', maxWidth: 720 }} block>
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onPressEnter={() => query.trim() && setSubmitted({ q: query.trim(), kbIds: scope.length ? scope : null })}
          placeholder="自然语言检索，如：拖曳线列阵 阵元间距 与 波束成形频率上限 的关系"
          allowClear
        />
        <Button
          type="primary"
          loading={search.isFetching}
          disabled={!query.trim()}
          onClick={() => setSubmitted({ q: query.trim(), kbIds: scope.length ? scope : null })}
        >
          检索
        </Button>
      </Space.Compact>
      <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>范围</Typography.Text>
        <Select
          mode="multiple"
          allowClear
          style={{ minWidth: 260 }}
          placeholder="全部知识库（未归库文档不参与检索）"
          value={scope}
          onChange={setScope}
          loading={bases.isLoading}
          options={(bases.data?.bases ?? []).map((b) => ({ value: b.id, label: b.is_default ? `${b.name}（默认）` : b.name }))}
        />
      </div>

      {search.isError && (
        <Alert type="error" showIcon style={{ marginTop: 12 }} message="检索失败" description={errText(search.error, 200)} />
      )}

      {result && (
        <div style={{ marginTop: 14 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <Tag color={level!.color}>{level!.text}</Tag>
            {result.degraded_from.length > 0 && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                降级轨迹：{result.degraded_from.join(' → ')}（A-14 如实交代）
              </Typography.Text>
            )}
            <span style={{ flex: 1 }} />
            <span className="mono" style={{ fontSize: 11, color: 'var(--text-3)' }}>{result.elapsed_ms} ms</span>
          </div>
          <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {result.hits.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="范围内无可检索知识（如实呈现）" />
            ) : (
              result.hits.map((h, i) => <HitCard key={i} hit={h} />)
            )}
          </div>
          {result.level === 'text' && (
            <Typography.Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 10 }}>
              当前命中为文本级（关键词）；向量/图谱层不可用或未启用时如实降级，分数一栏不造值。
            </Typography.Text>
          )}
        </div>
      )}
    </Card>
  )
}
