-- =====================================================================
-- Sonar-CAD v2 · V1 初始表结构（T0.4）
-- 依据：P3《智能辅助声呐装备设计系统_总体设计方案》§7.7 数据存储设计 + 附录 C 权限矩阵
-- 约束关联：
--   ir_snapshots = 不可变快照（宪法 A-12），无 UPDATE 语义
--   design_sessions.pipeline_status 对应编排状态机（T1.1：pending→running→paused→completed/failed）
--   traceability_chain = E2 全链路溯源（P2 §6.1），任一参数可反查全过程
-- =====================================================================

-- ---------- 用户与权限（D-04：JWT + RBAC，角色权限矩阵按 P3 附录 C） ----------
CREATE TABLE users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username      varchar(64)  NOT NULL UNIQUE,
    password_hash varchar(256) NOT NULL,
    display_name  varchar(64),
    email         varchar(128),
    status        varchar(16)  NOT NULL DEFAULT 'active',   -- active | disabled
    created_at    timestamptz  NOT NULL DEFAULT now(),
    updated_at    timestamptz  NOT NULL DEFAULT now()
);
COMMENT ON TABLE users IS '用户（P3 §5.11）';

CREATE TABLE roles (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code        varchar(32)  NOT NULL UNIQUE,               -- 如 admin / designer / reviewer / viewer
    name        varchar(64)  NOT NULL,
    description text,
    created_at  timestamptz  NOT NULL DEFAULT now()
);
COMMENT ON TABLE roles IS '角色（P3 附录 C 权限矩阵）';

CREATE TABLE permissions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code        varchar(64)  NOT NULL UNIQUE,               -- 如 project:write / gate:decide / kb:read
    name        varchar(64)  NOT NULL,
    description text,
    created_at  timestamptz  NOT NULL DEFAULT now()
);
COMMENT ON TABLE permissions IS '权限点（含项目级/会话级/知识库读写级/审阅关裁决权，P3 §9.4）';

CREATE TABLE user_roles (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id uuid NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE role_permissions (
    role_id       uuid NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id uuid NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

-- ---------- 项目（P3 §5.7） ----------
CREATE TABLE projects (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        varchar(128) NOT NULL,
    code        varchar(64)  UNIQUE,
    description text,
    owner_id    uuid REFERENCES users(id),
    status      varchar(16)  NOT NULL DEFAULT 'active',     -- active | archived（归档转只读，P3 §9.2）
    created_at  timestamptz  NOT NULL DEFAULT now(),
    updated_at  timestamptz  NOT NULL DEFAULT now()
);
COMMENT ON TABLE projects IS '项目（P3 §5.7）';

-- ---------- 设计会话（P3 §5.8，编排状态机载体） ----------
CREATE TABLE design_sessions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      uuid NOT NULL REFERENCES projects(id),
    title           varchar(256),
    pipeline_status varchar(16)  NOT NULL DEFAULT 'pending', -- pending | running | paused | completed | failed
    current_stage   varchar(16),                             -- A1..A5 / gate1 / gate2
    created_by      uuid REFERENCES users(id),
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now()
);
COMMENT ON TABLE design_sessions IS '设计会话：五步主线实例，状态由 Java 编排状态机维护（D-05，审阅关暂停/断点恢复）';
CREATE INDEX idx_sessions_project ON design_sessions(project_id);
CREATE INDEX idx_sessions_status ON design_sessions(pipeline_status);

-- ---------- IR 不可变快照（A-12：只追加，回退只重跑受影响 Agent） ----------
CREATE TABLE ir_snapshots (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id     uuid NOT NULL REFERENCES design_sessions(id),
    ir_type        varchar(8)  NOT NULL,                    -- IR0..IR5
    version        integer     NOT NULL,                    -- 同 (session, type) 内单调递增
    schema_version varchar(16) NOT NULL,                    -- 对应 contracts/ir 的契约版本
    content        jsonb       NOT NULL,                    -- IR 全文（校验对齐 contracts/ir/*.schema.json）
    created_by     varchar(24),                             -- 产出方：A1..A5 / user
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (session_id, ir_type, version)
);
COMMENT ON TABLE ir_snapshots IS 'IR 不可变快照（P3 §7.7 / 宪法 A-12）：局部重算与断点恢复的版本依据';
CREATE INDEX idx_ir_session_type ON ir_snapshots(session_id, ir_type);

-- ---------- 参数方案可编辑工作副本（P3 §7.7；审阅关①裁决后允许修订，原始值以 IR2 快照为准） ----------
CREATE TABLE parameter_schemes (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id     uuid NOT NULL REFERENCES design_sessions(id),
    ir_snapshot_id uuid REFERENCES ir_snapshots(id),        -- 来源 IR2 快照版本
    param_id       varchar(32) NOT NULL,                    -- 对应 IR2 parameter.id，如 P-014
    name           varchar(128),
    value          jsonb,
    unit           varchar(32),
    source_type    varchar(16) NOT NULL,                    -- formula | case | standard | estimated（A-06）
    source_ref     varchar(128),
    derivation     text,
    confidence     varchar(16),                             -- high | medium
    trace_id       varchar(32),                             -- E2 溯源链 ID
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (session_id, param_id)
);
COMMENT ON TABLE parameter_schemes IS '参数方案可编辑工作副本（P3 §7.7）：与 IR2 快照互为对照，修改走 E1 双向联动';
CREATE INDEX idx_params_session ON parameter_schemes(session_id);
CREATE INDEX idx_params_trace ON parameter_schemes(trace_id);

-- ---------- 审阅记录（P3 §5.8 审阅关①② + 知识审阅关） ----------
CREATE TABLE review_records (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id     uuid NOT NULL REFERENCES design_sessions(id),
    gate           varchar(8)  NOT NULL,                    -- gate1（IR2 后）| gate2（IR3 后）| knowledge（知识审阅关）
    ir_snapshot_id uuid REFERENCES ir_snapshots(id),        -- 被审阅的快照版本
    decision       varchar(16) NOT NULL,                    -- approved | rejected
    annotations    jsonb,                                   -- 批注列表（前端审阅界面录入）
    reviewer_id    uuid REFERENCES users(id),
    decided_at     timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE review_records IS '人工审阅记录（宪法 A-11）：不通过触发局部回环，禁止整链路重跑';
CREATE INDEX idx_review_session ON review_records(session_id, gate);

-- ---------- 交付物（P3 §5.12；实体存 MinIO 分桶，D-10） ----------
CREATE TABLE artifacts (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  uuid NOT NULL REFERENCES design_sessions(id),
    type        varchar(32) NOT NULL,                        -- step_model | cad_script | design_doc | validation_report | knowledge_dump
    bucket      varchar(64) NOT NULL,                        -- step-models / cad-scripts / design-docs / validation-reports / knowledge-dumps
    object_key  varchar(512) NOT NULL,
    size_bytes  bigint,
    created_by  varchar(24),
    created_at  timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE artifacts IS '交付物登记（实体在 MinIO，此处只存引用，P3 §8.3）';
CREATE INDEX idx_artifacts_session ON artifacts(session_id);

-- ---------- 溯源链（E2 全链路溯源，P2 §6.1；支撑 GET /api/v1/trace/{trace_id}，宪法 §5.4） ----------
CREATE TABLE traceability_chain (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id       varchar(32) NOT NULL,
    step_no        integer     NOT NULL,                    -- 链内顺序
    session_id     uuid REFERENCES design_sessions(id),
    actor          varchar(24) NOT NULL,                    -- A1..A5 / review / system
    action         varchar(64) NOT NULL,                    -- 如 derive / assign / transform / validate / confirm
    ir_snapshot_id uuid REFERENCES ir_snapshots(id),
    payload        jsonb,                                   -- 追加的溯源细节（依据、输入输出摘要）
    created_at     timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE traceability_chain IS '溯源链（P2 §6.1）：A2 起头、各 Agent 追加、A5 归档，任一参数可还原"由谁、何时、依据什么"';
CREATE INDEX idx_trace_trace_id ON traceability_chain(trace_id, step_no);
