-- PHASE-006 知识库多库：库表 + 多对多归属 + 会话选库（v0.3 决策版）
-- 归属唯一事实源 = kb_base_documents 关联表；检索按 doc 集合过滤，Milvus/Neo4j 结构不动。

CREATE TABLE IF NOT EXISTS kb_bases (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        varchar(128) NOT NULL UNIQUE,
    description varchar(512),
    is_default  boolean NOT NULL DEFAULT false,   -- 种子"默认知识库"，不可删（回写默认值等系统保底）
    created_by  varchar(64),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- 文档 ↔ 库 多对多：文档可同时作为多个库的构建材料；删库/删文档双向级联解除
CREATE TABLE IF NOT EXISTS kb_base_documents (
    base_id  uuid NOT NULL REFERENCES kb_bases(id) ON DELETE CASCADE,
    doc_id   varchar(64) NOT NULL REFERENCES kb_documents(doc_id) ON DELETE CASCADE,
    added_by varchar(64),
    added_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (base_id, doc_id)
);
CREATE INDEX IF NOT EXISTS idx_kb_base_documents_doc ON kb_base_documents(doc_id);

-- 存量文档全部归入默认库（保持现检索行为不回退：升级前检索全库）
INSERT INTO kb_base_documents (base_id, doc_id)
SELECT b.id, d.doc_id FROM kb_documents d CROSS JOIN (SELECT id FROM kb_bases WHERE is_default) b
WHERE NOT EXISTS (SELECT 1 FROM kb_base_documents x WHERE x.base_id = b.id AND x.doc_id = d.doc_id);

-- 会话选定的知识库；null = 不选（Agent 自主决策模式，PHASE-006 D6-3）
ALTER TABLE design_sessions ADD COLUMN IF NOT EXISTS kb_ids jsonb;
