-- V20（T15.6 / C-0，ADR-014 §5）：review_records 补 metadata jsonb 列——批准版本组合凭证载体。
-- 立约时"无迁移"系误记（V1 表中本无独立 metadata 列），实现期修正：一处 ALTER 补齐，
-- 组合契约自身后续演进仍免迁移（自由 jsonb）。annotations（人工批注+裁决自动字段）与
-- metadata（机器组合凭证 snapshot_combo/combo_policy）分列分职，审计可直查 metadata->'snapshot_combo'。
ALTER TABLE review_records ADD COLUMN IF NOT EXISTS metadata jsonb;

COMMENT ON COLUMN review_records.metadata IS
    '批准版本组合凭证（ADR-014 §5）：gate2 批准固化 snapshot_combo{IR2/IR3/IR4: snapshot_id,version,content_sha256,valid_at_decision} + combo_policy{ir4_overall,exception,unresolved}；knowledge 关记 ir5_version/kb_id。历史行 NULL=组合契约前记录，诚实可辨';
