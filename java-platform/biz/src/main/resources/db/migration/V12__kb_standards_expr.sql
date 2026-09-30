-- T9.3（PHASE-009）：军标条目判定表达式列——插拔式核对激活。
-- expr 为 SymPy 可求值布尔表达式（如 outer_diameter <= 325），符号取 IR2 参数 derivation
-- 的 "符号=数值" 记录（与公式复算同源）+ value（命中参数登记值）；NULL=仅登记提示性核对。
ALTER TABLE kb_standards ADD COLUMN IF NOT EXISTS expr TEXT;
