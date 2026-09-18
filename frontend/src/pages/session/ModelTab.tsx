import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Space, Table, Typography } from 'antd'
import { Canvas } from '@react-three/fiber'
import { Bounds, OrbitControls, useGLTF } from '@react-three/drei'
import { http } from '../../api/client'
import type { Ir3 } from '../../api/workflow'

type GltfState = 'idle' | 'converting' | 'ready' | 'failed' | 'no-ir3'

/** STEP→GLB 异步转换状态轮询；ready 后建 blob URL 交给 R3F。 */
function useGltf(sid: string) {
  const [state, setState] = useState<GltfState>('idle')
  const [detail, setDetail] = useState('')
  const [url, setUrl] = useState<string | null>(null)
  const urlRef = useRef<string | null>(null)

  useEffect(() => {
    let stopped = false
    let timer: number | undefined

    async function poll() {
      try {
        const resp = await http.get(`/cad/sessions/${sid}/gltf`, { responseType: 'arraybuffer' })
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
        setState('failed')
        setDetail(String(e))
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
  }, [sid])

  return { state, detail, url }
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
export function ModelTab({ sessionId, ir3, ir3Version }: { sessionId: string; ir3: Ir3 | null; ir3Version?: number }) {
  const [wireframe, setWireframe] = useState(false)
  const { state, detail, url } = useGltf(sessionId)

  async function download(kind: 'step' | 'script') {
    const resp = await http.get<{ url: string }>(`/cad/sessions/${sessionId}/download`, { params: { kind } })
    window.open(resp.data.url, '_blank')
  }

  if (!ir3) {
    return <Alert type="info" showIcon message="IR3 尚未生成，暂无三维模型与交付物。" />
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,3fr) minmax(280px,1fr)', gap: 16 }}>
      <Card
        title={<>三维预览 <Typography.Text type="secondary" style={{ fontSize: 12 }}>STEP → glTF（按 IR 版本缓存）</Typography.Text></>}
        extra={
          <Space>
            <Button size="small" onClick={() => setWireframe((w) => !w)}>{wireframe ? '实体' : '线框'}</Button>
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
          <Alert type="warning" showIcon message="三维预览转换中…" description="大装配首次镶嵌可能需要数分钟，转换结果按 IR 版本缓存，之后秒开。此页每 5 秒自动查询。" />
        )}
        {state === 'failed' && (
          <Alert type="error" showIcon message="三维预览转换失败" description={detail || '可先下载 STEP 查看，转换失败原因如实展示。'} />
        )}
        {state === 'idle' && <Alert type="info" showIcon message="准备转换…" />}
        <Typography.Paragraph type="secondary" style={{ fontSize: 11, marginTop: 10, marginBottom: 0 }}>
          转换链路：IR3 快照 → MinIO STEP → cadquery 镶嵌 → GLB 缓存（cad-previews/{'{session}'}/{'{version}'}.glb），版本变更自动重转。
        </Typography.Paragraph>
      </Card>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <Card title="交付物下载" size="small" extra={<Typography.Text type="secondary" style={{ fontSize: 11 }}>预签名 15 分钟</Typography.Text>} styles={{ body: { padding: 0 } }}>
          <Table
            rowKey="k" size="small" pagination={false}
            dataSource={[
              { k: 'step', name: 'STEP 模型', meta: `ir3_v${ir3Version ?? '?'} · ${ir3.step_file.bucket}` },
              { k: 'script', name: 'CadQuery 脚本', meta: `${ir3.cadquery_script.split('\n').length} 行` },
            ]}
            columns={[
              { title: '文件', key: 'n', render: (_, r) => <div><div style={{ fontWeight: 500 }}>{r.name}</div><div style={{ fontSize: 11, color: '#8b949e', fontFamily: 'monospace' }}>{r.meta}</div></div> },
              { title: '', key: 'a', width: 80, render: (_, r) => <Button size="small" type="primary" onClick={() => download(r.k as 'step' | 'script')}>下载</Button> },
            ]}
          />
        </Card>

        <Card title="CadQuery 脚本（只读）" size="small" styles={{ body: { padding: 0 } }}>
          <pre style={{ maxHeight: 220, overflow: 'auto', margin: 0, padding: '10px 14px', fontFamily: 'monospace', fontSize: 11.5, lineHeight: 1.8, color: '#57606a', background: '#fafbfc' }}>
            {ir3.cadquery_script.split('\n').slice(0, 60).join('\n')}
          </pre>
        </Card>
      </div>
    </div>
  )
}
