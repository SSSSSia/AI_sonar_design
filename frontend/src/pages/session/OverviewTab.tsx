import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Drawer, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import { useQuery } from '@tanstack/react-query'
import type { Ir1, KbSelection, KnowledgeRef, SessionLogRow } from '../../api/workflow'
import { getSessionLogs } from '../../api/workflow'
import { useLogStore, type SessionLogLine } from '../../stores/logStore'
import { ir1Degraded, normalizeMissingItems } from '../../lib/stageLabel'

const LOG_LEVEL_COLOR: Record<string, string> = {
  debug: '#8b949e', info: '#57606a', ok: '#2b8a3e', warn: '#d97706', error: '#c92a2a',
}

/** 历史留存 + 实时流合并（按时间+文本去重；历史在前，可单测）。 */
export function mergeLogs(
  history: SessionLogRow[] | undefined,
  live: SessionLogLine[] | undefined,
): { at: number; level: string; text: string }[] {
  const seen = new Set<string>()
  const out: { at: number; level: string; text: string }[] = []
  const push = (at: number, level: string, text: string) => {
    const key = `${at}|${text}`
    if (seen.has(key)) return
    seen.add(key)
    out.push({ at, level, text })
  }
  for (const r of history ?? []) push(dayjs(r.created_at).valueOf(), r.level, r.text)
  for (const l of live ?? []) push(l.at, l.level, l.text)
  return out
}

function logLine(at: number, level: string, text: string, key: string) {
  return (
    <div key={key}>
      <span style={{ color: '#8b949e', marginRight: 8 }}>{dayjs(at).format('HH:mm:ss')}</span>
      <span style={{ color: LOG_LEVEL_COLOR[level] ?? '#57606a' }}>{text}</span>
    </div>
  )
}

/** 实时日志面板（WS log 流）+「全部日志」抽屉（V11 落库留存回放，可导出）。 */
function LiveLog({ sessionId }: { sessionId: string }) {
  const lines = useLogStore((s) => s.bySession[sessionId])
  const [allOpen, setAllOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)
  const historyQuery = useQuery({
    queryKey: ['session-logs', sessionId],
    queryFn: () => getSessionLogs(sessionId),
    enabled: allOpen, // 打开抽屉才拉历史留存
  })
  const all = mergeLogs(historyQuery.data?.logs, lines)

  useEffect(() => {
    boxRef.current?.scrollTo({ top: boxRef.current.scrollHeight })
  }, [lines?.length])

  function exportTxt() {
    const body = all
      .map((l) => `${dayjs(l.at).format('YYYY-MM-DD HH:mm:ss')} [${l.level}] ${l.text}`)
      .join('\n')
    const url = URL.createObjectURL(new Blob([body], { type: 'text/plain;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `session-logs-${sessionId.slice(0, 8)}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <Card
      title="实时日志"
      size="small"
      extra={
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>worker 步骤推送 · 留存可回看</Typography.Text>
          <a onClick={() => setAllOpen(true)} style={{ fontSize: 12 }}>全部日志</a>
        </span>
      }
    >
      {(lines?.length ?? 0) === 0 ? (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          暂无实时日志。会话运行时各阶段步骤在此滚动显示；完整运行过程见「全部日志」（历史留存），
          权威记录见 IR 快照与溯源链。
        </Typography.Text>
      ) : (
        <div
          ref={boxRef}
          className="mono"
          style={{ maxHeight: 220, overflow: 'auto', background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 12px', fontSize: 11.5, lineHeight: 1.9 }}
        >
          {lines!.slice(-80).map((l, i) => logLine(l.at, l.level, l.text, `${l.at}-${i}`))}
        </div>
      )}

      <Drawer
        title={`全部日志（${all.length} 条）`}
        width={680}
        open={allOpen}
        onClose={() => setAllOpen(false)}
        extra={<Button size="small" onClick={exportTxt} disabled={all.length === 0}>导出 TXT</Button>}
      >
        <Typography.Text type="secondary" style={{ fontSize: 11.5, display: 'block', marginBottom: 10 }}>
          运行遥测留存（V11）：权威记录仍是 IR 快照与溯源链；功能上线前的历史会话未留存日志，如实留空。
        </Typography.Text>
        {historyQuery.isLoading ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>加载中…</Typography.Text>
        ) : all.length === 0 ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            暂无日志记录。本会话若在日志留存功能上线前运行，则无历史可看；之后的运行自动留存。
          </Typography.Text>
        ) : (
          <div
            className="mono"
            style={{ overflow: 'auto', background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 12px', fontSize: 11.5, lineHeight: 1.9 }}
          >
            {all.map((l, i) => logLine(l.at, l.level, l.text, `${l.at}-${i}`))}
          </div>
        )}
      </Drawer>
    </Card>
  )
}

/** 检索子查询展示（审查 P1-9）：降级模板的每条=全文需求+目标后缀——归一为「目标清单 + 原文一行折叠」。 */
function SubQueriesCard({ subQueries }: { subQueries: string[] }) {
  // 各条目的最长公共前缀即需求原文（模板形如「<原文> <目标>」）；LLM 版无公共前缀时列表原样展示
  let prefix = subQueries[0] ?? ''
  for (const q of subQueries) {
    let i = 0
    while (i < prefix.length && i < q.length && prefix[i] === q[i]) i++
    prefix = prefix.slice(0, i)
  }
  const raw = prefix.length > 30 ? prefix.trimEnd() : ''
  const goals = subQueries.map((q) => (raw && q.startsWith(raw) ? q.slice(raw.length) : q).replace(/^[：:，,\s]+/, ''))
  return (
    <div>
      <Typography.Text strong>IR1 检索子查询（{subQueries.length} 条）</Typography.Text>
      {raw && (
        <Typography.Paragraph
          ellipsis={{ rows: 1, expandable: true, symbol: '展开' }}
          type="secondary" style={{ fontSize: 11, margin: '4px 0 0' }}
        >
          检索目标均基于 IR0 需求原文：{raw}
        </Typography.Paragraph>
      )}
      <ol style={{ fontSize: 12, color: '#57606a', paddingLeft: 18, margin: '6px 0 0' }}>
        {goals.map((g, i) => <li key={`${i}-${g.slice(0, 12)}`}>{g || subQueries[i]}</li>)}
      </ol>
    </div>
  )
}

/** PHASE-006 D6-3/D6-7：库选择与命中聚合纯函数（可单测）。 */
export interface KbHitGroup {
  kbKey: string
  kbName: string
  selected: boolean
  hits: number
  hitRefs: KnowledgeRef[]
  hitDocs: number
}
export function kbHitSummary(ir1: Ir1 | null, sessionKbNames?: string[] | null): {
  mode: 'manual' | 'auto' | 'unknown'
  fallbackAll: boolean
  groups: KbHitGroup[]
} {
  const sel: KbSelection | undefined = ir1?.kb_selection
  const refs = ir1?.retrieved_context ?? []
  if (!sel || !sel.bases?.length) {
    // 旧快照无留痕：用会话回填的库名兜底展示"选定库"，命中一律未知
    const names = sessionKbNames?.length
      ? sessionKbNames.map((n, i) => ({
          kbKey: `name-${i}`, kbName: n, selected: true, hits: -1, hitRefs: [], hitDocs: 0,
        }))
      : []
    const grouped = refs.length > 0 && !sessionKbNames?.length
      ? [{ kbKey: 'all', kbName: '全部知识库', selected: true, hits: refs.length,
           hitRefs: refs, hitDocs: new Set(refs.map((r) => r.doc_id)).size }]
      : names
    return { mode: 'unknown', fallbackAll: false, groups: grouped }
  }
  const byKb = new Map<string, KnowledgeRef[]>()
  for (const r of refs) {
    const key = r.kb_id ?? 'unknown'
    const arr = byKb.get(key) ?? []
    arr.push(r)
    byKb.set(key, arr)
  }
  const groups = sel.bases
    .filter((b) => b.selected)
    .map((b) => {
      const hitRefs = byKb.get(b.kb_id) ?? []
      return {
        kbKey: b.kb_id, kbName: b.kb_name, selected: b.selected, hits: b.hits,
        hitRefs, hitDocs: new Set(hitRefs.map((r) => r.doc_id)).size,
      }
    })
  // 引用没打上 kb_id（旧链路）：挂到"未标注"组
  const unknownRefs = byKb.get('unknown')
  if (unknownRefs?.length) {
    groups.push({ kbKey: 'unknown', kbName: '来源库未标注（早期实体无归属信息）', selected: true, hits: -1, hitRefs: unknownRefs, hitDocs: new Set(unknownRefs.map((r) => r.doc_id)).size })
  }
  return { mode: sel.mode, fallbackAll: !!sel.fallback_all, groups }
}

/** 图谱实体命中解析："Label:名称" → [Label] 名称；解析失败原样返回。 */
function parseGraphEntity(location: string): string {
  const i = location.indexOf(':')
  if (i <= 0) return location
  return `[${location.slice(0, i)}] ${location.slice(i + 1)}`
}

/** 知识检索卡片（PHASE-006）：检索模式 + 选定库依据 + 实际命中（库 → 图谱实体/文档片段分列）。 */
function KnowledgeRetrievalCard({ ir1, sessionKbNames }: { ir1: Ir1 | null; sessionKbNames?: string[] | null }) {
  const summary = kbHitSummary(ir1, sessionKbNames)
  if (!ir1 || summary.groups.length === 0) {
    return (
      <Card title="知识检索" size="small">
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {ir1 ? '知识库选择与命中信息尚未生成（A1 完成后显示）。' : 'A1 完成后展示本次使用的知识库与命中明细。'}
        </Typography.Text>
      </Card>
    )
  }
  return (
    <Card title="知识检索" size="small">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {summary.fallbackAll && (
          <div style={{ borderLeft: '3px solid #d97706', background: '#fdf3e3', padding: '6px 10px', fontSize: 12, borderRadius: 4 }}>
            Agent 在各知识库均未发现相关内容，已回退到全部知识库检索（如实标注）。
          </div>
        )}
        <div style={{ fontSize: 11.5, color: '#8b949e' }}>
          {summary.mode === 'auto' ? 'Agent 根据需求自动圈定' : summary.mode === 'manual' ? '创建会话时人工选定' : '选定知识库'}
          {' · '}命中 {ir1.retrieved_context.length} 条
        </div>
        {summary.groups.map((g) => {
          // 图谱实体与文档片段分开渲染（2026-09-24 用户反馈：来源不清晰）。
          // 旧快照无 kind 字段，用 doc_id==='kb-graph' 占位符识别（我们自己的约定值）
          const graphRefs = g.hitRefs.filter((r) => r.kind === 'graph' || r.doc_id === 'kb-graph')
          const docRefs = g.hitRefs.filter((r) => r.kind !== 'graph' && r.doc_id !== 'kb-graph')
          const entities = [...new Set(graphRefs.map((r) => parseGraphEntity(r.location)))]
          return (
            <div key={g.kbKey}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
                <Typography.Text strong style={{ fontSize: 12.5 }}>{g.kbName}</Typography.Text>
                <span style={{ fontSize: 11, color: '#8b949e' }}>
                  {g.hitRefs.length > 0
                    ? [
                        `引用 ${g.hitRefs.length} 条`,
                        entities.length ? `图谱实体 ${entities.length}` : null,
                        docRefs.length ? `涉及 ${g.hitDocs} 份文档` : null,
                      ].filter(Boolean).join(' · ')
                    : summary.mode === 'auto' && g.hits > 0
                      ? `探测相关（${g.hits} 条候选）但本次未命中`
                      : '本次无命中'}
                </span>
              </div>
              {g.hitRefs.length > 0 && (
                <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 11.5, color: '#57606a' }}>
                  {entities.length > 0 && (
                    <li style={{ lineHeight: 1.7 }}>
                      <span style={{ color: '#8b949e' }}>知识图谱 · </span>
                      {entities.slice(0, 4).join('、')}
                      {entities.length > 4 ? ` 等 ${entities.length} 个实体` : ''}
                    </li>
                  )}
                  {Object.entries(
                    docRefs.reduce<Record<string, string[]>>((acc, r) => {
                      ;(acc[r.doc_id] = acc[r.doc_id] ?? []).push(r.location)
                      return acc
                    }, {}),
                  ).map(([docId, locs]) => (
                    <li key={docId} style={{ lineHeight: 1.7 }}>
                      <span style={{ fontFamily: 'monospace', fontSize: 10.5, color: '#8b949e' }}>
                        {/^[\da-f]{16}$/.test(docId) ? `文档 ${docId.slice(0, 10)}` : docId.slice(0, 10)}
                      </span>{' '}
                      {locs.slice(0, 3).join(' / ')}{locs.length > 3 ? ` 等 ${locs.length} 处` : ''}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )
        })}
      </div>
    </Card>
  )
}

/** 原始需求卡片（IR0 raw_text，创建时固化不可变 · A-04；未填写如实留空）。 */
function RawRequirementCard({ rawText }: { rawText?: string | null }) {
  return (
    <Card title="原始需求" extra={<Typography.Text type="secondary" style={{ fontSize: 11 }}>IR0 · 创建时固化 · A-04</Typography.Text>} size="small">
      {rawText ? (
        <Typography.Paragraph
          ellipsis={{ rows: 4, expandable: true, symbol: '展开' }}
          style={{ fontSize: 12.5, margin: 0, whiteSpace: 'pre-wrap' }}
        >
          {rawText}
        </Typography.Paragraph>
      ) : (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          创建会话时未填写需求原文（留空创建的会话无 IR0 快照）。
        </Typography.Text>
      )}
    </Card>
  )
}

/** 总览页签：原始需求 + 实时日志 + IR 快照版本条 + 知识检索 + IR1 目标摘要 + 缺失项如实展示。 */
export function OverviewTab({
  sessionId,
  ir1,
  ir0RawText,
  versions,
  sessionKbNames,
}: {
  sessionId: string
  ir1: Ir1 | null
  ir0RawText?: string | null
  versions: { IR1?: number; IR2?: number; IR3?: number; IR4?: number; IR5?: number }
  sessionKbNames?: string[] | null
}) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,2fr) minmax(260px,1fr)', gap: 16 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <RawRequirementCard rawText={ir0RawText} />
        <Card title="执行记录" styles={{ body: { paddingTop: 8 } }}>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
            阶段转移与裁决历史见「审阅与参数」页签的裁决记录。
          </Typography.Paragraph>
          {ir1 && <SubQueriesCard subQueries={ir1.sub_queries} />}
          <Typography.Paragraph type="secondary" style={{ fontSize: 11, marginTop: 12 }}>
            会话 {sessionId}
          </Typography.Paragraph>
        </Card>
        <LiveLog sessionId={sessionId} />
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <KnowledgeRetrievalCard ir1={ir1} sessionKbNames={sessionKbNames} />
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

        {ir1Degraded(ir1) && (
          <Alert
            type="warning"
            showIcon
            message="IR1 由降级流程生成（LLM 规划不可用）"
            description="目标仅登记原始需求，各维度未结构化解析；建议在 LLM 恢复后新建会话重跑，或在审阅关驳回回退 A1。"
          />
        )}

        {ir1 && (
          <Card title="IR1 目标摘要" size="small">
            <div style={{ display: 'grid', gridTemplateColumns: '84px 1fr', rowGap: 6, fontSize: 12.5 }}>
              {ir1.objectives.map((o) => (
                <div key={o.dimension} style={{ display: 'contents' }}>
                  <span style={{ color: '#8b949e' }}>{o.dimension}</span>
                  <span style={{ minWidth: 0, overflowWrap: 'anywhere' }}>{String(o.value)} <Tag style={{ fontSize: 10, lineHeight: '14px', marginInlineEnd: 0 }}>{o.explicit ? '显式' : '推断'}</Tag></span>
                </div>
              ))}
            </div>
            {(() => {
              const { cause, dims } = normalizeMissingItems(ir1.missing_items)
              if (dims.length === 0 && !cause) return null
              return (
                <div style={{ marginTop: 10, borderLeft: '3px solid #d97706', background: '#fdf3e3', padding: '6px 10px', fontSize: 12, borderRadius: 4 }}>
                  <b>{cause ?? '缺失项（如实）'}：</b>
                  <ul style={{ margin: '4px 0 0', paddingLeft: 16 }}>
                    {dims.map((d) => <li key={d}>{d}</li>)}
                  </ul>
                </div>
              )
            })()}
          </Card>
        )}
      </div>
    </div>
  )
}
