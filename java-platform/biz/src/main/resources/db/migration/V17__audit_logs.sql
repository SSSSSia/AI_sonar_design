-- T13.1（总体设计 §9.4 审计留痕）：独立审计日志表——不混入应用日志（§9.4：审计日志独立留存）。
-- 写入方：auth/workflow/biz 各服务 AuditRecorder 直插（notifications V14 同模式，biz 持有 Flyway）+
--         py-engine 审计中间件（storage/audit_store.py）。查询端点：auth GET /api/v1/audit/logs（admin）。
-- 范围口径（PHASE-013 §2.1）：Agent 调用/参数变更/审阅决定/知识回写已由 traceability_chain、
-- cad_param_updates、review_records、IR5 回写承担（A-12 溯源体系），本表补齐不覆盖的操作域：
-- 登录、用户管理、项目写、会话创建/删除、gate 裁决、KB 写、导出。
-- user_id 用 VARCHAR（gateway 透传的 X-User-Id 语义，兼容非 UUID 调试值；关联 users 查询时显式 cast）。
CREATE TABLE IF NOT EXISTS audit_logs (
    id             BIGSERIAL PRIMARY KEY,
    user_id        VARCHAR(64),
    username       VARCHAR(64),
    action         VARCHAR(64)  NOT NULL,
    resource_type  VARCHAR(32),
    resource_id    VARCHAR(64),
    detail         JSONB,
    ip             VARCHAR(64),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id    ON audit_logs (user_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_resource   ON audit_logs (resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action     ON audit_logs (action);
