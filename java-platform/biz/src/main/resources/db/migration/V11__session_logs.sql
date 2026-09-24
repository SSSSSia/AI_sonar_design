-- 会话运行日志留存（总览页「全部日志」展开栏）：worker 关键步在 WS 实时推送之外
-- 顺带落库，刷新/换机后仍可回看完整运行过程。
-- 定位不变（A-12/A-16）：日志是运行遥测，权威记录仍是 IR 快照与溯源链；本表只服务展示层。

CREATE TABLE IF NOT EXISTS session_logs (
    id          bigserial PRIMARY KEY,
    session_id  uuid        NOT NULL REFERENCES design_sessions(id) ON DELETE CASCADE,
    level       varchar(16) NOT NULL DEFAULT 'info',            -- debug|info|ok|warn|error
    text        text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_session_logs_session ON session_logs (session_id, id);
