import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Button, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Typography } from 'antd'
import type { TableProps } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import {
  createMetadata, deleteMetadata, listMetadata, updateMetadata,
  type MetadataRow, type MetadataTable,
} from '../../api/metadata'
import { errText } from '../../api/client'

/**
 * 元数据管理（T13.7，§5.16 基础元数据 + 附录 D）：声呐类型（安装形式×功能体制）、
 * 频率段定义、性能参数规范、提示词模板（版本化，同键多版本互斥启用）。
 * LLM 模型注册表由「LLM 供应商」页承担——§5.16 五子项至此全覆盖。
 * 读写口径：全员可读，写仅 admin（T11.2 管理面收紧，后端硬约束）。
 */

const CATEGORIES: { key: MetadataTable; label: string; hint: string }[] = [
  { key: 'sonar_types', label: '声呐类型', hint: '安装形式 × 功能体制 双维度枚举' },
  { key: 'freq_bands', label: '频率段', hint: '低/中/高/甚高频：范围、应用场景、参考军标' },
  { key: 'perf_specs', label: '性能参数规范', hint: '单位、取值范围、业务定义' },
  { key: 'prompt_templates', label: '提示词模板', hint: '各环节提示词版本化管理与调优' },
]

const INSTALL_FORMS = ['艇艏阵', '舷侧阵', '拖曳阵']
const FUNCTIONAL_SYSTEMS = ['侧扫', '多波束', '前视', '合成孔径(SAS)', '被动']
const STAGES = ['a1_requirement', 'a2_scheme', 'a3_cad', 'a4_validation', 'a5_knowledge', 'retrieval', 'general']

interface FieldSpec {
  name: string
  label: string
  widget: 'input' | 'textarea' | 'number' | 'select' | 'switch'
  options?: string[]
  required?: boolean
}

const FIELDS: Record<MetadataTable, FieldSpec[]> = {
  sonar_types: [
    { name: 'install_form', label: '安装形式', widget: 'select', options: INSTALL_FORMS, required: true },
    { name: 'functional_system', label: '功能体制', widget: 'select', options: FUNCTIONAL_SYSTEMS, required: true },
    { name: 'description', label: '说明', widget: 'textarea' },
  ],
  freq_bands: [
    { name: 'name', label: '名称', widget: 'input', required: true },
    { name: 'freq_min_hz', label: '最小频率 (Hz)', widget: 'number' },
    { name: 'freq_max_hz', label: '最大频率 (Hz)', widget: 'number' },
    { name: 'application', label: '应用场景', widget: 'textarea' },
    { name: 'reference_standard', label: '参考军标', widget: 'input' },
  ],
  perf_specs: [
    { name: 'name', label: '参数名', widget: 'input', required: true },
    { name: 'unit', label: '单位', widget: 'input' },
    { name: 'value_min', label: '取值下限', widget: 'number' },
    { name: 'value_max', label: '取值上限', widget: 'number' },
    { name: 'definition', label: '业务定义', widget: 'textarea' },
  ],
  prompt_templates: [
    { name: 'stage', label: '环节', widget: 'select', options: STAGES, required: true },
    { name: 'name', label: '模板名', widget: 'input', required: true },
    { name: 'template_text', label: '模板文本', widget: 'textarea', required: true },
    { name: 'remark', label: '备注', widget: 'input' },
  ],
}

type MetaColumn = NonNullable<TableProps<MetadataRow>['columns']>[number]

const COLUMNS: Record<MetadataTable, MetaColumn[]> = {
  sonar_types: [
    { title: '安装形式', dataIndex: 'install_form', width: 120 },
    { title: '功能体制', dataIndex: 'functional_system', width: 140 },
    { title: '说明', dataIndex: 'description', ellipsis: true },
  ],
  freq_bands: [
    { title: '名称', dataIndex: 'name', width: 110 },
    { title: '范围 (Hz)', key: 'range', width: 180, render: (_: unknown, r: MetadataRow) => `${r.freq_min_hz ?? '?'} ~ ${r.freq_max_hz ?? '?'}` },
    { title: '应用场景', dataIndex: 'application', ellipsis: true },
    { title: '参考军标', dataIndex: 'reference_standard', width: 150, render: (v: string | null) => v || '—' },
  ],
  perf_specs: [
    { title: '参数名', dataIndex: 'name', width: 130 },
    { title: '单位', dataIndex: 'unit', width: 130, render: (v: string | null) => v || '—' },
    { title: '取值范围', key: 'range', width: 140, render: (_: unknown, r: MetadataRow) => `${r.value_min ?? '—'} ~ ${r.value_max ?? '—'}` },
    { title: '业务定义', dataIndex: 'definition', ellipsis: true },
  ],
  prompt_templates: [
    { title: '环节', dataIndex: 'stage', width: 140, render: (v: string) => <Typography.Text code style={{ fontSize: 11.5 }}>{v}</Typography.Text> },
    { title: '模板名', dataIndex: 'name', width: 160 },
    { title: '版本', dataIndex: 'version', width: 70, render: (v: number) => <Tag>v{v}</Tag> },
    { title: '模板文本', dataIndex: 'template_text', ellipsis: true },
    { title: '备注', dataIndex: 'remark', width: 140, ellipsis: true, render: (v: string | null) => v || '—' },
  ],
}

function MetaForm({ table, initial, form }: {
  table: MetadataTable
  initial: MetadataRow | null
  form: ReturnType<typeof Form.useForm>[0]
}) {
  return (
    <Form form={form} layout="vertical" preserve={false}>
      {FIELDS[table].map((f) => (
        <Form.Item
          key={f.name} name={f.name} label={f.label}
          rules={f.required ? [{ required: true, message: `${f.label}必填` }] : undefined}
          initialValue={initial ? initial[f.name] : undefined}
        >
          {f.widget === 'textarea' ? <Input.TextArea rows={f.name === 'template_text' ? 6 : 2} />
            : f.widget === 'select' ? <Select options={f.options!.map((o) => ({ value: o, label: o }))} allowClear={!f.required} />
              : f.widget === 'number' ? <Input type="number" />
                : <Input />}
        </Form.Item>
      ))}
    </Form>
  )
}

export default function MetadataTab() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [category, setCategory] = useState<MetadataTable>('sonar_types')
  const [editing, setEditing] = useState<MetadataRow | null>(null)
  const [creating, setCreating] = useState(false)
  const [form] = Form.useForm()

  const q = useQuery({
    queryKey: ['metadata', category],
    queryFn: () => listMetadata(category),
  })
  const rows: MetadataRow[] = q.data?.rows ?? []

  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['metadata', category] })

  const submit = async () => {
    const values = await form.validateFields()
    const fields: Record<string, unknown> = {}
    for (const f of FIELDS[category]) {
      const v = values[f.name]
      if (v !== undefined && v !== null && v !== '') {
        fields[f.name] = (f.widget === 'number' && v !== '') ? Number(v) : v
      }
    }
    try {
      if (editing) {
        await updateMetadata(category, editing.id, fields)
        message.success('已保存')
      } else {
        await createMetadata(category, fields)
        message.success('已创建')
      }
      setEditing(null)
      setCreating(false)
      refresh()
    } catch (e) {
      message.error(errText(e))
    }
  }

  const toggleEnable = async (row: MetadataRow) => {
    try {
      await updateMetadata(category, row.id, { enabled: !row.enabled })
      refresh()
    } catch (e) {
      message.error(errText(e))
    }
  }

  const remove = async (row: MetadataRow) => {
    try {
      await deleteMetadata(category, row.id)
      message.success('已删除')
      refresh()
    } catch (e) {
      message.error(errText(e))
    }
  }

  const active = CATEGORIES.find((c) => c.key === category)!
  const extraOps = category === 'prompt_templates'
    ? [
        { title: '启用', key: 'on', width: 90, render: (_: unknown, r: MetadataRow) => (
          <Switch size="small" checked={!!r.enabled} onChange={() => void toggleEnable(r)} />
        ) },
      ]
    : [
        { title: '启用', key: 'on', width: 90, render: (_: unknown, r: MetadataRow) => (
          <Switch size="small" checked={!!r.enabled} onChange={() => void toggleEnable(r)} />
        ) },
      ]

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
        <Space.Compact>
          {CATEGORIES.map((c) => (
            <Button key={c.key} type={c.key === category ? 'primary' : 'default'} onClick={() => setCategory(c.key)}>
              {c.label}
            </Button>
          ))}
        </Space.Compact>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>{active.hint} · 种子数据来自附录 D（示例口径）</Typography.Text>
        <span style={{ flex: 1 }} />
        <Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => setCreating(true)}>新建</Button>
      </div>
      <Table
        rowKey="id" size="small" loading={q.isLoading} dataSource={rows}
        pagination={rows.length > 20 ? { pageSize: 20 } : false}
        columns={[
          ...COLUMNS[category],
          ...extraOps,
          {
            title: '操作', key: 'ops', width: 110,
            render: (_: unknown, r: MetadataRow) => (
              <Space size={4}>
                <Button type="link" size="small" onClick={() => setEditing(r)}>编辑</Button>
                <Popconfirm title="确认删除该条元数据？" okButtonProps={{ danger: true }}
                  onConfirm={() => void remove(r)}>
                  <Button type="link" size="small" danger>删除</Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title={editing ? `编辑：${active.label} #${editing.id}` : `新建：${active.label}`}
        open={creating || !!editing}
        onCancel={() => { setEditing(null); setCreating(false) }}
        okText={editing ? '保存' : '创建'}
        onOk={() => void submit()}
        destroyOnClose
      >
        <MetaForm table={category} initial={editing} form={form} />
        {category === 'prompt_templates' && !editing && (
          <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>
            同一（环节+模板名）重复创建时版本号自动递增；启用某版本会自动停用同键其他版本。
          </Typography.Text>
        )}
      </Modal>
    </div>
  )
}
