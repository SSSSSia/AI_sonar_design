-- T3.1 知识文档管线：文档档案 / 分块 / 抽取预览（宪法 §6 ①②③④）
CREATE TABLE IF NOT EXISTS kb_documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_id varchar(64) UNIQUE NOT NULL,
    filename varchar(256) NOT NULL,
    format varchar(8) NOT NULL,
    size_bytes bigint NOT NULL DEFAULT 0,
    bucket varchar(32) NOT NULL,
    object_key varchar(512) NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'uploaded',   -- uploaded|parsed|chunked|indexed|failed
    page_count integer,
    section_count integer,
    ocr_pending integer NOT NULL DEFAULT 0,           -- 扫描页计数（OCR 插件待接，D-14）
    parsed_units jsonb,
    build_detail jsonb,                               -- 各层构建结果如实明细（向量/图谱/语料）
    error text,
    uploaded_by varchar(64),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kb_documents_status ON kb_documents(status);

CREATE TABLE IF NOT EXISTS kb_chunks (
    id bigserial PRIMARY KEY,
    doc_id varchar(64) NOT NULL REFERENCES kb_documents(doc_id) ON DELETE CASCADE,
    chunk_index integer NOT NULL,
    location varchar(256) NOT NULL,                   -- 页码/章节位置（§6 ②，溯源引用单元）
    text text NOT NULL,
    embedded boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (doc_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_doc ON kb_chunks(doc_id);

CREATE TABLE IF NOT EXISTS kb_extractions (
    id bigserial PRIMARY KEY,
    doc_id varchar(64) NOT NULL REFERENCES kb_documents(doc_id) ON DELETE CASCADE,
    chunk_index integer,
    kind varchar(8) NOT NULL,                         -- entity | relation
    etype varchar(32) NOT NULL,                       -- 实体本体类型 / 关系类型
    name varchar(128) NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    status varchar(16) NOT NULL DEFAULT 'pending',    -- pending|confirmed|rejected（§6 ④ 人工审）
    graph_key varchar(32),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kb_extractions_doc ON kb_extractions(doc_id);
CREATE INDEX IF NOT EXISTS idx_kb_extractions_status ON kb_extractions(status);
