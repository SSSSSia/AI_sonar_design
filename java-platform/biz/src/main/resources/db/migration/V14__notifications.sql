-- T11.1（PHASE-011）通知持久化（P2-4）：notify 广播的关键信封落库，登录后可拉取，刷新/换端不丢。
-- 落库类型口径（PHASE-011 §6 决策）：review_request（待裁决，需行动）与 alert（系统告警）；
-- task_progress / log 为高频过程类、ir_updated 每会话 5 次更新——均仍仅 WS 广播（写放大与噪声控制）。
-- 扇出模型：广播信封按 users 表逐用户落行（user_id 非空）——已读/删除/清理天然按用户隔离，
-- 避免「A 已读把 B 的未读也清了」；角色过滤在 INSERT...SELECT 内完成。
-- client_key = 会话|类型|信封时间戳，(user_id, client_key) 唯一 → 重投/重放幂等。
CREATE TABLE IF NOT EXISTS notifications (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id  varchar(64) NOT NULL DEFAULT '',
    client_key  varchar(160) NOT NULL,
    type        varchar(32) NOT NULL,
    title       varchar(200),
    payload     jsonb NOT NULL,
    read_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE notifications IS '通知（T11.1，notify 扇出落库；已读/清理按用户隔离）';
CREATE UNIQUE INDEX IF NOT EXISTS uk_notifications_user_key ON notifications (user_id, client_key);
CREATE INDEX IF NOT EXISTS idx_notifications_user_created ON notifications (user_id, created_at DESC);
