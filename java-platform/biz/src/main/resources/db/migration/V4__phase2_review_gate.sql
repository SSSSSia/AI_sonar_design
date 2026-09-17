-- T2.2 审阅关支撑：IR 快照失效标记（宪法 A-12 细化）
-- 快照内容保持不可变（无 UPDATE content）；invalidated_at 是生命周期元数据，
-- 由驳回时的下游失效批量标记（重跑目标 T 的产物类型及其下游整批标记，见 PHASE-002 §2.3）。
-- review_records / traceability_chain 已在 V1 建立，本脚本不重复。
ALTER TABLE ir_snapshots ADD COLUMN IF NOT EXISTS invalidated_at timestamptz;
