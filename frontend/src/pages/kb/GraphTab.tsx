import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Card, Input, Select, Space, Tag, Typography } from 'antd'
import * as d3 from 'd3'
import { getGraph, getNeighborhood, type GraphData } from '../../api/kb'

// 实体类型键 = Neo4j label（与后端图谱写入一致，2026-09-18 实测采样对齐）；图表数据编码色，非交互蓝
const TYPE_COLOR: Record<string, string> = {
  SonarSystem: '#1f2329', DesignCase: '#b7791f', DesignConstraint: '#c92a2a',
  DesignParameter: '#d97706', PerformanceIndicator: '#0e8088', Algorithm: '#2b8a3e',
  Component: '#47698c', StructuralModule: '#6e56cf', CADModelingStandard: '#57606a',
}

function nodeType(n: { label?: string; type?: string }): string {
  return n.label ?? n.type ?? 'Entity'
}

interface Node extends d3.SimulationNodeDatum {
  key: string; label: string; name?: string; type?: string; deprecated?: boolean
}
interface Link { source: string | Node; target: string | Node; relation?: string }

/** D3 力导向图谱（全图采样 + 邻域查询）；节点带来源可反跳，deprecated 灰显。 */
export function GraphTab() {
  const [query, setQuery] = useState('')
  const [depth, setDepth] = useState(2)
  const [data, setData] = useState<GraphData | null>(null)
  const [selected, setSelected] = useState<GraphData['nodes'][number] | null>(null)
  const [loading, setLoading] = useState(false)
  const svgRef = useRef<SVGSVGElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)

  const overview = useQuery({ queryKey: ['kb-graph'], queryFn: () => getGraph(300), refetchInterval: 60_000 })
  const refetchGraph = () => { void overview.refetch() }
  const graph = data ?? overview.data

  async function load(g: GraphData) {
    setData(g)
    setSelected(null)
  }

  async function search() {
    if (!query.trim()) return
    setLoading(true)
    try {
      const sub = await getNeighborhood(query.trim(), depth)
      await load(sub)
    } finally {
      setLoading(false)
    }
  }

  // D3 force 布局 + 渲染
  useEffect(() => {
    if (!graph || !svgRef.current || !wrapRef.current) return
    const width = wrapRef.current.clientWidth
    const height = 460
    const nodes: Node[] = graph.nodes.map((n) => ({ ...n }))
    const links: Link[] = graph.edges
      .filter((e) => nodes.some((n) => n.key === e.source) && nodes.some((n) => n.key === e.target))
      .map((e) => ({ source: e.source, target: e.target, relation: e.relation }))

    const svg = d3.select(svgRef.current)
    svg.selectAll('*').remove()
    svg.attr('viewBox', `0 0 ${width} ${height}`)

    const sim = d3.forceSimulation<Node>(nodes)
      .force('link', d3.forceLink<Node, Link>(links).id((d) => d.key).distance(70).strength(0.4))
      .force('charge', d3.forceManyBody().strength(-160))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collide', d3.forceCollide<Node>(16))

    const g = svg.append('g')
    svg.call(d3.zoom<SVGSVGElement, unknown>().on('zoom', (ev) => g.attr('transform', ev.transform))).on('dblclick.zoom', null)

    const link = g.append('g')
      .selectAll<SVGLineElement, Link>('line').data(links).join('line')
      .attr('stroke', '#d1d5db').attr('stroke-width', 1)

    const node = g.append('g')
      .selectAll<SVGGElement, Node>('g').data(nodes).join('g')
      .attr('cursor', 'pointer')
      .on('click', (_, d) => setSelected(d))

    node.append('circle')
      .attr('r', 7)
      .attr('fill', (d) => (d.deprecated ? '#c6cbd1' : TYPE_COLOR[nodeType(d)] ?? '#8b949e'))
      .attr('stroke', '#fff').attr('stroke-width', 1.5)
    node.append('text')
      .text((d) => (d.name || d.label).slice(0, 10))
      .attr('x', 0).attr('y', 18)
      .attr('text-anchor', 'middle')
      .attr('font-size', 9.5)
      .attr('fill', '#57606a')

    node.call(d3.drag<SVGGElement, Node>()
      .on('start', (ev, d) => { if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y })
      .on('drag', (ev, d) => { d.fx = ev.x; d.fy = ev.y })
      .on('end', (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null }))

    sim.on('tick', () => {
      link.attr('x1', (d) => (d.source as Node).x ?? 0).attr('y1', (d) => (d.source as Node).y ?? 0)
        .attr('x2', (d) => (d.target as Node).x ?? 0).attr('y2', (d) => (d.target as Node).y ?? 0)
      node.attr('transform', (d) => `translate(${d.x ?? 0},${d.y ?? 0})`)
    })

    return () => { sim.stop() }
  }, [graph])

  const legend = useMemo(() => Object.entries(TYPE_COLOR), [])

  return (
    <Card
      title="知识图谱"
      extra={
        <Space>
          <Input size="small" style={{ width: 200 }} placeholder="实体名 / 图键" value={query} onChange={(e) => setQuery(e.target.value)} onPressEnter={search} />
          <Select size="small" style={{ width: 80 }} value={depth} onChange={setDepth} options={[1, 2, 3].map((d) => ({ value: d, label: `${d} 跳` }))} />
          <Button size="small" type="primary" loading={loading} onClick={search}>查询子图</Button>
          <Button size="small" onClick={() => { setData(null); refetchGraph() }}>全图</Button>
        </Space>
      }
      styles={{ body: { padding: 0 } }}
    >
      <div ref={wrapRef} style={{ position: 'relative' }}>
        <svg ref={svgRef} style={{ width: '100%', display: 'block', background: '#fcfcfd', borderRadius: 8, minHeight: 460 }} />
        <div style={{ position: 'absolute', left: 10, bottom: 8, display: 'flex', gap: 8, flexWrap: 'wrap', fontSize: 10.5, color: '#57606a', background: 'rgba(255,255,255,.9)', padding: '3px 8px', borderRadius: 6 }}>
          {legend.map(([t, c]) => <span key={t}><span style={{ display: 'inline-block', width: 9, height: 9, borderRadius: 5, background: c, marginRight: 3 }} />{t}</span>)}
        </div>
        {overview.data?.truncated && <div style={{ position: 'absolute', right: 10, top: 8 }}><Tag>已采样 {graph?.nodes.length}/全量</Tag></div>}
      </div>
      <div style={{ padding: '10px 16px', borderTop: '1px solid #f0f1f3', minHeight: 64 }}>
        {selected ? (
          <div style={{ fontSize: 12.5 }}>
            <b>{selected.name || selected.label}</b>{' '}
            <Tag style={{ marginInlineEnd: 6 }}>{nodeType(selected)}</Tag>
            {selected.deprecated && <Tag color="default">deprecated</Tag>}
            <div style={{ color: '#57606a', marginTop: 4 }}>
              来源：{selected.source_doc ? <a>{selected.source_doc}</a> : '—'}
              {selected.source_session ? <> · 会话 <a>{selected.source_session.slice(0, 12)}…</a></> : null}
            </div>
          </div>
        ) : (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            点击节点查看来源（source_doc / source_session 可反跳）；拖拽节点、滚轮缩放。Neo4j 不可达时此页如实报错。
          </Typography.Text>
        )}
      </div>
    </Card>
  )
}
