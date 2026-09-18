import { Tabs } from 'antd'
import { DocumentsTab, ExtractionsTab } from './DocumentsTab'
import { RegistryTab } from './RegistryTab'
import { ProvidersTab } from './ProvidersTab'
import { GraphTab } from './GraphTab'

/** 知识库管理（T4.4）：文档管线 / 抽取确认 / 三库 / 供应商 / 图谱。 */
export default function KnowledgePage() {
  return (
    <div style={{ maxWidth: 1320, margin: '0 auto' }}>
      <Tabs
        items={[
          { key: 'docs', label: '文档库', children: <DocumentsTab /> },
          { key: 'ext', label: '抽取确认', children: <ExtractionsTab /> },
          { key: 'registry', label: '三库管理', children: <RegistryTab /> },
          { key: 'providers', label: 'LLM 供应商', children: <ProvidersTab /> },
          { key: 'graph', label: '图谱', children: <GraphTab /> },
        ]}
      />
    </div>
  )
}
