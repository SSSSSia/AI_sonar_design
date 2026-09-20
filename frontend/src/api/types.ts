/** 会话/项目领域类型（与后端实体一致；IR 快照内容为 JSON 字符串或对象，按 type 区分） */

export interface Project {
  id: string
  name: string
  code?: string | null
  description?: string | null
  status: string
  ownerId?: string | null
  createdAt: string
  /** 列表接口附带（项目卡片用；单查接口无此字段） */
  session_count?: number
  session_stats?: { pending?: number; running?: number; paused?: number; completed?: number; failed?: number }
  /** 项目下最近一次会话活动（无会话时为空，卡片回退显示创建时间） */
  last_active_at?: string | null
}

export type PipelineStatusValue = 'pending' | 'running' | 'paused' | 'completed' | 'failed'

export interface DesignSession {
  id: string
  projectId: string
  /** 后端列表/详情回填（projects 同库只读 join）；项目被删悬空时为空 */
  projectName?: string | null
  title: string
  pipelineStatus: PipelineStatusValue
  currentStage?: string | null
  createdBy?: string | null
  /** 创建人用户名（后端 join users 带出；缺失时回退 createdBy 短显） */
  createdByName?: string | null
  createdAt: string
  updatedAt: string
}

/** MyBatis-Plus 分页包 */
export interface PageResult<T> {
  records: T[]
  total: number
  size: number
  current: number
  pages: number
}

export interface LoginResult {
  token: string
  username: string
  displayName: string
  roles: string[]
}

export interface MeInfo {
  userId: string
  username: string
  roles: string[]
}
