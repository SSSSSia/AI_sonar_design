-- T11.3（PHASE-011）E1 CAD→文档反向回写（P2-1）：微调定稿 diff 基线 IR3 产生的参数变更提案。
-- 人工确认后追加 IR2 新版本（value + derivation 同步）并定向失效 IR4/IR5、重跑 A4、回 gate2 复审。
-- 仅长度类联动项可自动映射（IR3 feature.linked_param_id ↔ IR2 parameter.id，经 linked_dim 对应 bbox 维）；
-- 分段数变化/基线无联动（链式微调）时如实不产提案（A-16）。
CREATE TABLE IF NOT EXISTS cad_tune_param_proposals (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          uuid NOT NULL REFERENCES design_sessions(id) ON DELETE CASCADE,
    tune_id             uuid NOT NULL REFERENCES cad_tune_sessions(id) ON DELETE CASCADE,
    round_no            integer NOT NULL,
    ir3_version         integer NOT NULL,              -- 触发提案的微调版 IR3
    baseline_ir_version integer NOT NULL,              -- diff 基线（tune.baseline_ir_version）
    param_id            varchar(32) NOT NULL,          -- IR2 parameter.id（如 P-014）
    param_name          varchar(128),
    unit                varchar(16) NOT NULL DEFAULT 'mm',
    old_value           double precision NOT NULL,     -- 原登记值（原生单位）
    new_value           double precision NOT NULL,     -- geometry_inspect 实测（原生单位，由 mm 换算回）
    delta_pct           double precision NOT NULL,
    dimension           integer NOT NULL,              -- linked_dim（bbox 维下标 0/1/2）
    status              varchar(16) NOT NULL DEFAULT 'proposed', -- proposed|applied|ignored
    applied_ir2_version integer,                       -- 确认回写落地的 IR2 版本
    note                text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    decided_at          timestamptz,
    decided_by          varchar(64)
);
COMMENT ON TABLE cad_tune_param_proposals IS 'E1 参数变更提案（T11.3）：几何实测 diff → 人工确认 → IR2 回写';
CREATE UNIQUE INDEX IF NOT EXISTS uk_e1_proposal_round_param
    ON cad_tune_param_proposals (tune_id, round_no, param_id);
CREATE INDEX IF NOT EXISTS idx_e1_proposals_session ON cad_tune_param_proposals (session_id, status);

-- P2-2 定案（phase-11 §6）：parameter_schemes 表（V1 建成但全库零引用）——
-- 声明「预留，当前以 IR2 快照为权威」；E1 提案以 cad_tune_param_proposals 承载
-- （本表列结构与提案语义不匹配），消除表-实现歧义。
COMMENT ON TABLE parameter_schemes IS '预留（E1 工作副本候选，当前零引用）；参数权威载体 = ir_snapshots 的 IR2 快照，E1 提案见 cad_tune_param_proposals（T11.3 定案，phase-11 §6 P2-2）';
