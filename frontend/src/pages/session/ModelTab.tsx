import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Space, Table, Typography } from 'antd'
import { Canvas } from '@react-three/fiber'
import { Bounds, OrbitControls, useGLTF } from '@react-three/drei'
import { http } from '../../api/client'
import { getIr, type Ir3 } from '../../api/workflow'

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
            <Button size="small" autoInsertSpace={false} onClick={() => setWireframe((w) => !w)}>{wireframe ? '实体' : '线框'}</Button>
          </Space>
        }
      >
        {state === 'ready' && url && (
          <div style={{ border: '1px solid #e5e7eb', borderRadius: 8, overflow: 'hidden' }}>
            <Canvas style={{ height: 460, background: '#f2f3f5' }} camera={{ fov: 45 }}>
              <hemisphereLight intensity={0.9} />
              <directionalLight position={[1, 1.5, 1]} intensity={1.2} />
              <Bounds fit clip observe margin={1.15}>
                <GltfModel url={url} wireframe={wireframe} />
              </Bounds>
              <OrbitControls makeDefault autoRotate autoRotateSpeed={0.6} />
              <gridHelper args={[100, 20, '#d1d5db', '#e5e7eb']} />
            </Canvas>
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
