import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Card, Collapse, Space, Table, Tag, Timeline, Typography } from 'antd'
import { Canvas } from '@react-three/fiber'
import { Bounds, OrbitControls, useGLTF } from '@react-three/drei'
import { MOUSE } from 'three'
import { http } from '../../api/client'
import { getIr, type Ir3 } from '../../api/workflow'
import { listTunes } from '../../api/cadAgent'
import { roundStatusLabel, summarizeTunes } from './tuneSummary'

type GltfState = 'idle' | 'converting' | 'ready' | 'failed' | 'no-ir3'

/** STEP→GLB 异步转换状态轮询；ready 后建 blob URL 交给 R3F。
 *  网络抖动容错：连续 3 次查询失败才判失败（此前任一次超时都会把 axios 原码甩给用户）；
 *  retry() 重新触发转换并恢复轮询。 */
function useGltf(sid: string) {
  const [state, setState] = useState<GltfState>('idle')
  const [detail, setDetail] = useState('')
  const [url, setUrl] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  const urlRef = useRef<string | null>(null)
  const failCount = useRef(0)

  useEffect(() => {
    let stopped = false
    let timer: number | undefined

    async function poll() {
      try {
        const resp = await http.get(`/cad/sessions/${sid}/gltf`, { responseType: 'arraybuffer', timeout: 20_000 })
        failCount.current = 0
        const ct = String(resp.headers['content-type'] ?? '')
        if (ct.includes('model/gltf-binary')) {
          const blob = new Blob([resp.data], { type: 'model/gltf-binary' })
          const u = URL.createObjectURL(blob)
          urlRef.current = u
          setUrl(u)
          setState('ready')
          return
        }
        const body = JSON.parse(new TextDecoder().decode(resp.data as ArrayBuffer))
        setState(body.state === 'ready' ? 'ready' : 'converting')
      } catch (e) {
        const status = (e as { response?: { status?: number; data?: { detail?: string } } }).response?.status
        const respDetail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail
        if (status === 404) {
          setState('no-ir3')
          return
        }
        if (status === 503) {
          setState('failed')
          setDetail(respDetail ?? '')
          return
        }
        failCount.current += 1
        if (failCount.current >= 3) {
          setState('failed')
          setDetail('三维服务暂时无响应——模型较大时转换可能仍在后台进行，请稍后点击重试或刷新页面。')
          return
        }
        setState('converting') // 单次查询失败先容忍，继续轮询
      }
      if (!stopped) timer = window.setTimeout(poll, 5000)
    }

    // 首次先触发转换（幂等：已缓存直接 ready）
    void http.post(`/cad/sessions/${sid}/gltf/convert`).catch(() => {})
    void poll()

    return () => {
      stopped = true
      if (timer) window.clearTimeout(timer)
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
    }
  }, [sid, nonce])

  return {
    state, detail, url,
    retry() {
      failCount.current = 0
      setState('idle')
      setDetail('')
      setNonce((n) => n + 1)
    },
  }
}

function GltfModel({ url, wireframe }: { url: string; wireframe: boolean }) {
  const { scene } = useGLTF(url)
  useEffect(() => {
    scene.traverse((o) => {
      const mesh = o as { isMesh?: boolean; material?: { wireframe?: boolean } }
      if (mesh.isMesh && mesh.material) mesh.material.wireframe = wireframe
    })
  }, [scene, wireframe])
  return <primitive object={scene} />
}

/** FreeCAD 交互微调卡片（T8.5）：活跃会话状态 + 轮次/版本时间线 + 插件使用提示。 */
function TuneCard({ sessionId, ir3Version }: { sessionId: string; ir3Version?: number }) {
  const tunesQuery = useQuery({
    queryKey: ['cad-tune', sessionId],
    queryFn: () => listTunes(sessionId),
    enabled: !!sessionId,
  })
  const sum = summarizeTunes(tunesQuery.data?.sessions)
  const statusTag = sum.active ? (
    <Tag color="processing">微调进行中{sum.activeRoundCount > 0 ? ` · ${sum.activeRoundCount} 轮未收敛` : ''}</Tag>
  ) : sum.latest?.status === 'finalized' ? (
    <Tag color="success">已定稿（待审阅关②）</Tag>
  ) : sum.latest?.status === 'abandoned' ? (
    <Tag>已放弃</Tag>
  ) : (
    <Tag>未开始</Tag>
  )

  return (
    <Card
      title="FreeCAD 交互微调"
      size="small"
      extra={statusTag}
      styles={{ body: { paddingTop: 8 } }}
    >
      {sum.rows.length === 0 ? (
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
          在 FreeCAD 中安装 SonarCADLink 插件后，可对当前模型（IR3 v{ir3Version ?? '?'}）进行
          点选 + 自然语言微调；每轮微调产生新版本并全部留痕。
        </Typography.Paragraph>
      ) : (
        <Timeline
          style={{ marginBottom: 0 }}
          items={sum.rows.map((r) => ({
            color: r.status === 'failed' ? 'red' : r.irVersion ? 'green' : r.status === 'running' ? 'blue' : 'gray',
            children: (
              <div style={{ fontSize: 12, lineHeight: 1.6 }}>
                <div>
                  <b>r{r.roundNo}</b> · {roundStatusLabel(r.status)}
                  {r.irVersion ? <Tag color="green" style={{ marginLeft: 6 }}>IR3 v{r.irVersion}</Tag> : null}
                  {r.isQuestion ? <Tag style={{ marginLeft: 6 }}>问答</Tag> : null}
                  {r.toolCount > 0 ? <span style={{ color: '#8b949e' }}>（{r.toolCount} 次编辑）</span> : null}
                </div>
                <div style={{ color: '#57606a' }}>{r.prompt}</div>
                {r.failedError ? <div style={{ color: '#cf222e' }}>{r.failedError}</div> : null}
              </div>
            ),
          }))}
        />
      )}
      <Collapse
        ghost
        size="small"
        items={[{
          key: 'how',
          label: <span style={{ fontSize: 12 }}>如何在 FreeCAD 中微调？</span>,
          children: (
            <Typography.Paragraph style={{ fontSize: 12, marginBottom: 0 }} type="secondary">
              把仓库 <code>clients/freecad-plugin/SonarCADLink</code> 复制进 FreeCAD 的 Mod
              目录，重启后切到 SonarCADLink 工作台：登录平台 → 粘贴本会话 ID →
              「打开/新建微调」→「拉取模型」→ 对话微调 → 定稿（详见插件 README 的人工验收清单）。
            </Typography.Paragraph>
          ),
        }]}
      />
    </Card>
  )
}

/** 三维预览 + 交付物下载（T4.3）。 */
export function ModelTab({ sessionId, ir3, ir3Version, ir2Version, ir4Version }: {
  sessionId: string
  ir3: Ir3 | null
  ir3Version?: number
  ir2Version?: number
  ir4Version?: number
}) {
  const [wireframe, setWireframe] = useState(false)
  const [showAllScript, setShowAllScript] = useState(false)
  const [resetKey, setResetKey] = useState(0)
  const { state, detail, url, retry } = useGltf(sessionId)

  async function download(kind: 'step' | 'script' | 'ir2' | 'ir4') {
    if (kind === 'ir2' || kind === 'ir4') {
      // IR JSON 快照下载（审查 P2-6）：走既有快照 API，客户端落文件
      const irType = kind === 'ir2' ? 'IR2' : 'IR4'
      const r = await getIr(sessionId, irType)
      const content = typeof r.content === 'string' ? r.content : JSON.stringify(r.content, null, 2)
      const blob = new Blob([content], { type: 'application/json' })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `${irType.toLowerCase()}_v${r.version}.json`
      a.click()
      URL.revokeObjectURL(a.href)
      return
    }
    const resp = await http.get<{ url: string }>(`/cad/sessions/${sessionId}/download`, { params: { kind } })
    window.open(resp.data.url, '_blank')
  }

  if (!ir3) {
    return <Alert type="info" showIcon message="IR3 尚未生成，暂无三维模型与交付物。" />
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,3fr) minmax(280px,1fr)', gap: 16 }}>
      <Card
        title="三维预览"
        extra={
          <Space>
            <Button size="small" autoInsertSpace={false} onClick={() => setResetKey((k) => k + 1)}>复位视角</Button>
            <Button size="small" autoInsertSpace={false} onClick={() => setWireframe((w) => !w)}>{wireframe ? '实体' : '线框'}</Button>
          </Space>
        }
      >
        {state === 'ready' && url && (
          <div
            key={resetKey}
            style={{ position: 'relative', border: '1px solid #e5e7eb', borderRadius: 8, overflow: 'hidden' }}
            // 中键留给平移：拦掉浏览器的自动滚动/小圆盘；右键拖动平移时不弹菜单
            onMouseDown={(e) => { if (e.button === 1) e.preventDefault() }}
            onAuxClick={(e) => e.preventDefault()}
            onContextMenu={(e) => e.preventDefault()}
          >
            <Canvas style={{ height: 460, background: '#f2f3f5' }} camera={{ fov: 45 }}>
              <hemisphereLight intensity={0.9} />
              <directionalLight position={[1, 1.5, 1]} intensity={1.2} />
              <Bounds fit clip observe margin={1.15}>
                <GltfModel url={url} wireframe={wireframe} />
              </Bounds>
              <OrbitControls
                makeDefault
                mouseButtons={{ LEFT: MOUSE.ROTATE, MIDDLE: MOUSE.PAN, RIGHT: MOUSE.PAN }}
              />
              <gridHelper args={[100, 20, '#d1d5db', '#e5e7eb']} />
            </Canvas>
            <div style={{
              position: 'absolute', left: 8, bottom: 8, pointerEvents: 'none',
              background: 'rgba(255,255,255,0.82)', borderRadius: 6, padding: '2px 8px',
              fontSize: 11, color: '#57606a',
            }}>
              左键旋转 · 右键/中键拖动平移 · 滚轮缩放
            </div>
          </div>
        )}
        {state === 'converting' && (
          <Alert type="warning" showIcon message="正在生成三维预览…" description="首次打开需要把设计模型转换为预览格式，结构复杂时可能需要几分钟；完成后自动显示，下次打开同一版本即刻呈现。" />
        )}
        {state === 'failed' && (
          <Alert
            type="error" showIcon message="三维预览生成失败"
            description={detail || '可先下载 STEP 模型查看，或点击重试再次转换。'}
            action={<Button size="small" onClick={retry}>重试</Button>}
          />
        )}
        {state === 'idle' && <Alert type="info" showIcon message="准备转换…" />}
      </Card>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <TuneCard sessionId={sessionId} ir3Version={ir3Version} />
        <Card title="交付物下载" size="small" extra={<Typography.Text type="secondary" style={{ fontSize: 11 }}>下载链接 15 分钟内有效</Typography.Text>} styles={{ body: { padding: 0 } }}>
          <Table
            rowKey="k" size="small" pagination={false}
            dataSource={[
              { k: 'step', name: 'STEP 模型', meta: ir3.step_file.object_key.split('/').pop() || `ir3_v${ir3Version ?? '?'}.step` },
              { k: 'script', name: 'CadQuery 脚本', meta: `${ir3.cadquery_script.split('\n').length} 行` },
              { k: 'ir2', name: 'IR2 参数方案', meta: ir2Version ? `JSON · v${ir2Version}` : 'JSON' },
              { k: 'ir4', name: 'IR4 校验报告', meta: ir4Version ? `JSON · v${ir4Version}` : 'JSON' },
            ]}
            columns={[
              { title: '文件', key: 'n', render: (_, r) => <div><div style={{ fontWeight: 500 }}>{r.name}</div><div style={{ fontSize: 11, color: '#8b949e', fontFamily: 'monospace' }}>{r.meta}</div></div> },
              { title: '', key: 'a', width: 80, render: (_, r) => <Button size="small" type="primary" autoInsertSpace={false} onClick={() => download(r.k as 'step' | 'script' | 'ir2' | 'ir4')}>下载</Button> },
            ]}
          />
        </Card>

        <Card
          title="CadQuery 脚本（只读）"
          size="small"
          extra={
            ir3.cadquery_script.split('\n').length > 60 ? (
              <Button type="link" size="small" style={{ padding: 0 }} onClick={() => setShowAllScript((s) => !s)}>
                {showAllScript ? '收起' : `展开全部 ${ir3.cadquery_script.split('\n').length} 行`}
              </Button>
            ) : undefined
          }
          styles={{ body: { padding: 0 } }}
        >
          <pre style={{ maxHeight: showAllScript ? 480 : 220, overflow: 'auto', margin: 0, padding: '10px 14px', fontFamily: 'monospace', fontSize: 11.5, lineHeight: 1.8, color: '#57606a', background: '#fafbfc' }}>
            {(showAllScript ? ir3.cadquery_script : ir3.cadquery_script.split('\n').slice(0, 60).join('\n')).trimEnd()}
          </pre>
        </Card>
      </div>
    </div>
  )
}
