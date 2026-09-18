-- T3.2 修复：kb_extractions.graph_key 容纳关系组合键（src16->dst16 = 34 字符）
ALTER TABLE kb_extractions ALTER COLUMN graph_key TYPE varchar(64);
