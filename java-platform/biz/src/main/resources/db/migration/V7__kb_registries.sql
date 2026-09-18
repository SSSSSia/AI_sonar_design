-- T3.3 公式/约束/军标三类知识资产库（§6 + 5.14）：录入/启停/可信度分层
CREATE TABLE IF NOT EXISTS kb_formulas (
    id bigserial PRIMARY KEY,
    key varchar(64) UNIQUE NOT NULL,
    name varchar(128) NOT NULL,
    expr varchar(512) NOT NULL,                       -- SymPy 可解析表达式（A4 复算用）
    symbols jsonb NOT NULL DEFAULT '[]',              -- 输入符号列表
    unit varchar(32) NOT NULL DEFAULT '-',
    source_ref varchar(512) NOT NULL DEFAULT '',
    enabled boolean NOT NULL DEFAULT true,
    confidence varchar(8) NOT NULL DEFAULT 'medium',  -- high|medium|low（可信度分层）
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kb_constraints (
    id bigserial PRIMARY KEY,
    name varchar(128) UNIQUE NOT NULL,
    expression varchar(512) NOT NULL,
    applies_to varchar(256) NOT NULL DEFAULT '',
    severity_hint varchar(8) NOT NULL DEFAULT 'warning',
    source_ref varchar(512) NOT NULL DEFAULT '',
    enabled boolean NOT NULL DEFAULT true,
    confidence varchar(8) NOT NULL DEFAULT 'medium',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kb_standards (
    id bigserial PRIMARY KEY,
    code varchar(64) UNIQUE NOT NULL,                 -- 军标编号（如 GJB 585）
    name varchar(256) NOT NULL,
    applies_to varchar(256) NOT NULL,                 -- 参数名包含匹配
    requirement text NOT NULL DEFAULT '',             -- 条款要求描述（自动核对逻辑待实现时如实提示）
    enabled boolean NOT NULL DEFAULT true,
    confidence varchar(8) NOT NULL DEFAULT 'medium',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
