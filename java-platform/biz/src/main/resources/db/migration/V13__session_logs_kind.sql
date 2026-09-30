-- T10.3（PHASE-010）：session_logs 增加行类型列，承载「阶段耗时」结构化行。
-- kind='log' 为既有普通运行日志（缺省回填，兼容 V11 既有行）；
-- kind='stage_duration' 由 worker _record_stage 写入（A1–A5 阶段耗时，性能基线数据源）。
ALTER TABLE session_logs ADD COLUMN IF NOT EXISTS kind VARCHAR(24) NOT NULL DEFAULT 'log';
CREATE INDEX IF NOT EXISTS idx_session_logs_kind ON session_logs (kind, id);
