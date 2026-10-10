import { useQuery } from '@tanstack/react-query'
import { Alert, Descriptions, Drawer, Skeleton, Typography } from 'antd'
import { getKbRef } from '../api/kb'
import { errText } from '../api/client'

/**
 * 知识原文抽屉（PHASE-006 T6.6）：审阅页 retrieved 参数一键查看知识库原文段落，
 * 出处三件套（库 / 文档 / 位置）齐显，供设计人员核查方案依据（A-02 溯源闭环）。
 */
export function KbRefDrawer({ ref: kbRef, onClose }: {
  ref: { docId: string; location: string; paramName?: string } | null
  onClose: () => void
}) {
  const q = useQuery({
    queryKey: ['kb-ref', kbRef?.docId, kbRef?.location],
    queryFn: () => getKbRef(kbRef!.docId, kbRef!.location),
    enabled: !!kbRef,
    retry: false,
  })

  return (
    <Drawer
      title={kbRef?.paramName ? `知识原文 · ${kbRef.paramName}` : '知识原文'}
      open={!!kbRef}
      onClose={onClose}
      width={520}
      destroyOnClose
    >
      {q.isLoading && <Skeleton active paragraph={{ rows: 5 }} />}
      {q.isError && (
        <Alert
          type="warning" showIcon
          message={q.error instanceof Error && q.error.message.includes('404') ? '出处文档已删除' : '原文获取失败'}
          description={`${errText(q.error)}。该出处可能已随文档删除退出知识库（A-16 如实提示）。`}
        />
      )}
      {q.data && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <Descriptions
            size="small"
            column={1}
            bordered
            items={[
              { key: 'file', label: '文档', children: q.data.filename },
              { key: 'loc', label: '位置', children: <span className="mono" style={{ fontSize: 11.5 }}>{q.data.location || '—'}</span> },
              { key: 'bases', label: '所属知识库', children: q.data.bases?.length ? q.data.bases.join('、') : '未归库（暂不参与检索）' },
            ]}
          />
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>知识库原文段落</Typography.Text>
            <div
              style={{
                marginTop: 6, padding: '12px 14px', background: '#fafbfc', border: '1px solid #e5e7eb',
                borderRadius: 6, fontSize: 13, lineHeight: 1.9, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere',
              }}
            >
              {q.data.text ?? '（该位置无原文段落——出处为图谱层引用）'}
            </div>
          </div>
        </div>
      )}
    </Drawer>
  )
}
