-- T12.2（PHASE-012）E1 正向联动：IR2 参数正向更新（文档→CAD）记录表。
-- 变更抑制三件套之一：origin/depth 记录传播链（forward=1，其引发的 A3 重算所产
-- 反向提案 depth=2，>3 拒绝——系统实施方案「变更发起方原子锁+最大传播深度」的
-- 务实落地；「回写拦截器」= v0.4 反向提案确认制；单飞行窗口 = 仅 paused@gate2 可发起）。
CREATE TABLE IF NOT EXISTS cad_param_updates (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  uuid NOT NULL REFERENCES design_sessions(id) ON DELETE CASCADE,
    param_id    varchar(32) NOT NULL,
    old_value   double precision,
    new_value   double precision NOT NULL,
    unit        varchar(16) NOT NULL DEFAULT 'mm',
    reason      text,
    origin      varchar(24) NOT NULL DEFAULT 'user_forward',  -- user_forward（本期唯一发起方）
    depth       integer NOT NULL DEFAULT 1,
    ir2_version integer NOT NULL,                             -- 本次更新落地的 IR2 版本
    created_by  varchar(64),
    created_at  timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE cad_param_updates IS 'E1 正向参数更新（T12.2）：文档→CAD 联动留痕与传播深度计量';
CREATE INDEX IF NOT EXISTS idx_param_updates_session ON cad_param_updates (session_id, created_at DESC);
