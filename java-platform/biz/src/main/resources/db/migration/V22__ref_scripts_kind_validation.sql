-- V22: 范例条目类型与验证报告列（T19.1，契约=ADR-019 决策 1/4）
-- kind：script=成品脚本（prompt 注入，既有行为）| recipe=参数化配方（build(params) 确定性
-- 执行，拼接式单文件走沙箱——零 LLM 生成，连通 by construction）；DEFAULT 'script' 旧行
-- 不受影响（加法演进）。
-- validation：最近一次自动验证报告 jsonb（T19.5 上传工装起草时写入；结构由验证器定义，
-- 入库前无值=未验证如实，A-16）。
ALTER TABLE ref_scripts ADD COLUMN IF NOT EXISTS kind varchar(16) NOT NULL DEFAULT 'script';
ALTER TABLE ref_scripts ADD COLUMN IF NOT EXISTS validation jsonb;
CREATE INDEX IF NOT EXISTS idx_ref_scripts_kind ON ref_scripts (kind, status);
COMMENT ON COLUMN ref_scripts.kind IS '条目类型（T19.1，ADR-019 决策 1）：script|recipe';
COMMENT ON COLUMN ref_scripts.validation IS '最近一次自动验证报告（T19.5 起草；结构由验证器定义）';
