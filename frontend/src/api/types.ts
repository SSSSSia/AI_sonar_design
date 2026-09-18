/** 会话/项目领域类型（与后端实体一致；IR 快照内容为 JSON 字符串或对象，按 type 区分） */

export interface Project {
  id: string
  name: string
  code?: string | null
  description?: string | null
  status: string
  ownerId?: string | null
  createdAt: string
}

export type PipelineStatusValue = 'pending' | 'running' | 'paused' | 'completed' | 'failed'

export interface DesignSession {
  id: string
  projectId: string
  title: string
  pipelineStatus: PipelineStatusValue
  currentStage?: string | null
  createdBy?: string | null
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
