-- T2.3 修复：review_records.gate 容纳 "knowledge"（9 字符）
-- V1 建表时 varchar(8) 只够 gate1/gate2，注释中声明的 knowledge 枚举值实际放不进去。
ALTER TABLE review_records ALTER COLUMN gate TYPE varchar(16);
