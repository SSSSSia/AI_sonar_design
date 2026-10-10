import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Button, Card, Modal, Popconfirm, Space, Tabs, Tag, Tooltip, Typography } from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons'
import { deleteBase, listBases, type KbBase } from '../../api/kb'
import { errText } from '../../api/client'
import { DocumentsTab, ExtractionsTab, NewBaseWizard } from './DocumentsTab'
import { RegistryTab } from './RegistryTab'
import { ProvidersTab } from './ProvidersTab'
import { GraphTab } from './GraphTab'
import MetadataTab from './MetadataTab'

/**
 * 知识库管理（PHASE-006 多库重构）：左侧库列表（含「文档池」）+ 右侧库内管线。
 * 全局资产（三库/供应商/图谱）保持顶部二级 Tab。宪法 §7.3：用户明确免 mockup 直接实现。
 */

function BaseListPane({ bases, activeKey, onPick, loading, onCreate }: {
  bases: KbBase[]
  activeKey: string
  onPick: (key: string) => void
  loading: boolean
  onCreate: () => void
}) {
  return (
    <Card
      title="知识库"
      size="small"
      styles={{ body: { padding: 4 } }}
      extra={
        <Button type="link" size="small" icon={<PlusOutlined />} style={{ padding: 0 }} onClick={onCreate}>
          新建
        </Button>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <div
          onClick={() => onPick('pool')}
          style={{
            padding: '8px 10px', borderRadius: 6, cursor: 'pointer', fontSize: 13,
            background: activeKey === 'pool' ? '#eef1f6' : 'transparent',
          }}
        >
          <div style={{ fontWeight: activeKey === 'pool' ? 600 : 400 }}>文档池</div>
          <div style={{ fontSize: 11, color: 'var(--text-3)' }}>全部文档与未归库</div>
        </div>
        {loading && <div style={{ padding: 10, fontSize: 12, color: 'var(--text-3)' }}>加载中…</div>}
        {bases.map((b) => (
          <div
            key={b.id}
            onClick={() => onPick(b.id)}
            style={{
              padding: '8px 10px', borderRadius: 6, cursor: 'pointer',
              background: activeKey === b.id ? '#eef1f6' : 'transparent',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontSize: 13, fontWeight: activeKey === b.id ? 600 : 400, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {b.name}
              </span>
              {b.is_default && <Tag style={{ fontSize: 10, lineHeight: '14px', marginInlineEnd: 0 }}>默认</Tag>}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
              {b.documents ?? 0} 份文档 · {(b.indexed ?? 0) === (b.documents ?? 0) && (b.documents ?? 0) > 0 ? '全部入索引' : `已入索引 ${b.indexed ?? 0}`}
            </div>
          </div>
        ))}
      </div>
    </Card>
  )
}

function BaseDetail({ base, onEdit }: { base: KbBase | null; onEdit: (b: KbBase) => void }) {
  if (!base) {
    return (
      <Card size="small">
        <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-3)', fontSize: 13 }}>
          未归库文档不参与检索——左侧选择知识库查看其内容，或把文档归入知识库以恢复检索。
        </div>
      </Card>
    )
  }
  return (
    <Card
      size="small"
      title={
        <Space size={8}>
          <span style={{ fontSize: 14.5 }}>{base.name}</span>
          {base.is_default && <Tag color="blue" style={{ marginInlineEnd: 0 }}>默认库</Tag>}
        </Space>
      }
      extra={
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => onEdit(base)}>重命名</Button>
          <Tooltip title={base.is_default ? '默认库不可删除（系统保底库）' : '删除后文档转未归库（内容保留，不参与检索）'}>
          <Popconfirm
            title={`删除知识库「${base.name}」？`}
            description={(base.documents ?? 0) > 0
              ? `将解除 ${base.documents} 份文档的本库归属，文档与索引保留；无其他归属的文档转入未归库，可随时重新归库。`
              : '该库暂无文档。'}
            disabled={base.is_default}
            onConfirm={() => window.dispatchEvent(new CustomEvent('kb-delete-base', { detail: base.id }))}
            okText="删除"
            okButtonProps={{ danger: true }}
          >
            <Button size="small" danger disabled={base.is_default} icon={<DeleteOutlined />}>删除</Button>
            </Popconfirm>
          </Tooltip>
        </Space>
      }
    >
      {base.description && (
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 12 }}>
          {base.description}
        </Typography.Paragraph>
      )}
      <Tabs
        size="small"
        items={[
          { key: 'docs', label: '文档', children: <DocumentsTab baseId={base.id} /> },
          { key: 'ext', label: '抽取确认', children: <ExtractionsTab baseId={base.id} /> },
        ]}
      />
    </Card>
  )
}

function PoolDetail() {
  return (
    <Card size="small" title="文档池">
      <div style={{ marginBottom: 8 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          上传文档先进文档池（未归库不参与检索），再归入知识库；也可在库视图内直接上传。
        </Typography.Text>
      </div>
      <DocumentsTab pool />
    </Card>
  )
}

export default function KnowledgePage() {
  const { modal, message } = App.useApp()
  const queryClient = useQueryClient()
  const [activeKey, setActiveKey] = useState('pool')
  const [wizard, setWizard] = useState(false)
  const [editing, setEditing] = useState<KbBase | null>(null)
  const [editName, setEditName] = useState('')

  const basesQuery = useQuery({ queryKey: ['kb-bases'], queryFn: listBases })
  const bases: KbBase[] = basesQuery.data?.bases ?? []
  const activeBase = bases.find((b) => b.id === activeKey) ?? null

  async function doDelete(id: string) {
    try {
      const r = await deleteBase(id)
      message.success(`已删除「${r.name}」，${r.unassigned_docs} 份文档转为未归库（内容保留）`)
      if (activeKey === id) setActiveKey('pool')
      void queryClient.invalidateQueries({ queryKey: ['kb-bases'] })
      void queryClient.invalidateQueries({ queryKey: ['kb-docs'] })
    } catch (e) {
      message.error('删除失败：' + errText(e, 120))
    }
  }

  // BaseDetail 内嵌 Popconfirm 经事件委托回调（避免在 Card extra 里层层传 modal）
  if (typeof window !== 'undefined' && !(window as unknown as { __kbDelBound?: boolean }).__kbDelBound) {
    ;(window as unknown as { __kbDelBound?: boolean }).__kbDelBound = true
    window.addEventListener('kb-delete-base', (e) => {
      const id = (e as CustomEvent<string>).detail
      modal.confirm({
        title: '确认删除该知识库？',
        content: '文档与索引数据保留，仅解除归属关系；无其他归属的文档将转入未归库。',
        okText: '删除',
        okButtonProps: { danger: true },
        onOk: () => doDeleteRef.current?.(id),
      })
    })
  }
  const doDeleteRef = useRefOf(doDelete)

  return (
    <div style={{ maxWidth: 1320, margin: '0 auto' }}>
      <Tabs
        items={[
          {
            key: 'kb',
            label: '知识库',
            children: (
              <div style={{ display: 'grid', gridTemplateColumns: '240px minmax(0,1fr)', gap: 16, alignItems: 'start' }}>
                <BaseListPane
                  bases={bases}
                  activeKey={activeKey}
                  onPick={setActiveKey}
                  loading={basesQuery.isLoading}
                  onCreate={() => setWizard(true)}
                />
                {activeKey === 'pool'
                  ? <PoolDetail />
                  : (
                    <BaseDetail
                      base={activeBase}
                      onEdit={(b) => { setEditing(b); setEditName(b.name) }}
                    />
                  )}
              </div>
            ),
          },
          { key: 'registry', label: '三库管理', children: <RegistryTab /> },
          { key: 'providers', label: 'LLM 供应商', children: <ProvidersTab /> },
          { key: 'graph', label: '图谱', children: <GraphTab /> },
          { key: 'metadata', label: '元数据管理', children: <MetadataTab /> },
        ]}
      />
      <NewBaseWizard open={wizard} onClose={() => setWizard(false)} onCreated={(b) => setActiveKey(b.id)} />
      <Modal
        title="重命名知识库"
        open={!!editing}
        onOk={async () => {
          if (!editing) return
          try {
            const { updateBase } = await import('../../api/kb')
            await updateBase(editing.id, { name: editName.trim() })
            message.success('已重命名')
            setEditing(null)
            void queryClient.invalidateQueries({ queryKey: ['kb-bases'] })
          } catch (e) {
            message.error(errText(e, 120))
          }
        }}
        onCancel={() => setEditing(null)}
        destroyOnClose
      >
        <input
          value={editName}
          onChange={(e) => setEditName(e.target.value)}
          maxLength={128}
          style={{ width: '100%', border: '1px solid #d0d7de', borderRadius: 6, padding: '6px 10px', fontSize: 13 }}
        />
      </Modal>
    </div>
  )
}

/** 简易 useLatestRef：事件委托回调始终拿到最新 doDelete（组件外不可用 hooks，此处手动）。 */
function useRefOf<T>(value: T) {
  const ref = { current: value }
  ref.current = value
  return ref
}
