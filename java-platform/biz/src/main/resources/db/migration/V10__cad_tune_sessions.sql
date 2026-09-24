-- PHASE-008 CAD 交互微调：微调会话 / 轮次 / 工具调用（ADR-004，D8-8）
-- 大脑在 py-engine（AgentLoop），工具执行外包 FreeCAD 插件（长轮询）；
-- 每轮收敛产物上传后由服务端实测几何并追加 IR3 版本（ir_snapshots，本文件不动它）。

-- 微调会话：挂在设计会话下；审阅关②前的人工微调现场（D8-3）
CREATE TABLE IF NOT EXISTS cad_tune_sessions (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    design_session_id  uuid NOT NULL REFERENCES design_sessions(id) ON DELETE CASCADE,
    status             varchar(16) NOT NULL DEFAULT 'active',   -- active|finalized|abandoned
    baseline_ir_version integer NOT NULL,                       -- 建会话时的最新 IR3 版本
    note               text,
    created_by         varchar(64),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_cad_tune_sessions_design ON cad_tune_sessions(design_session_id);

-- 微调轮次：一次自然语言交互 = 一轮；llm_trace 留工具调用序列（A-02 溯源）
CREATE TABLE IF NOT EXISTS cad_tune_rounds (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tune_session_id    uuid NOT NULL REFERENCES cad_tune_sessions(id) ON DELETE CASCADE,
    round_no           integer NOT NULL,
    baseline_ir_version integer NOT NULL,                       -- 该轮微调基线（回退后可非递增）
    prompt             text NOT NULL,
    selection          jsonb,                                   -- 点选上下文（元素类型/bbox/名称）
    llm_trace          jsonb,
    script             text,                                    -- 最终采纳的编辑脚本
    result_ir3_version integer,                                 -- 定稿上传后回填
    status             varchar(16) NOT NULL DEFAULT 'running',  -- running|awaiting_upload|done|failed
    error              text,
    created_by         varchar(64),
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tune_session_id, round_no)
);
CREATE INDEX IF NOT EXISTS idx_cad_tune_rounds_session ON cad_tune_rounds(tune_session_id);

-- 工具调用：服务端 AgentLoop 下发 → 插件长轮询取走 → 回传结果（D8-5）
CREATE TABLE IF NOT EXISTS cad_tune_tool_calls (
    id            bigserial PRIMARY KEY,
    round_id      uuid NOT NULL REFERENCES cad_tune_rounds(id) ON DELETE CASCADE,
    name          varchar(32) NOT NULL,                         -- 一期仅 execute_edit_code
    args          jsonb NOT NULL,
    result        jsonb,                                        -- {ok, stdout, quality, doc_summary, error}
    status        varchar(16) NOT NULL DEFAULT 'pending',       -- pending|done|timeout
    created_at    timestamptz NOT NULL DEFAULT now(),
    completed_at  timestamptz
);
CREATE INDEX IF NOT EXISTS idx_cad_tune_tool_calls_round ON cad_tune_tool_calls(round_id);
