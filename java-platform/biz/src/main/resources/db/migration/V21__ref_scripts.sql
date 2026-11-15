-- V21: 参考代码范例库（T16.1 B1 最小管理，契约=ADR-015）
-- 一行 = 一个范例的一个内容版本；内容实体在 MinIO 桶 ref-scripts（{ref_id}/v{version}/{file_name}），
-- 此处只存登记元数据（对齐 V1 交付物登记惯例）。
-- 状态机 pending|active|disabled：检索只读 active；同一 ref_id 至多一个 active（应用侧约束，
-- 对齐 meta_prompt_templates「多版本仅一个启用」先例）；不提供删除（回退不删历史版本，撤回走状态）。
CREATE TABLE IF NOT EXISTS ref_scripts (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ref_id          varchar(128) NOT NULL,                    -- 稳定业务标识（catalog id 风格 slug）
    version         int          NOT NULL,                    -- 同 ref_id 内容版本递增，内容变更产生新版本
    file_name       varchar(256) NOT NULL,                    -- 展示/下载文件名（原 catalog file）
    title           varchar(256),
    description     text,
    domain          varchar(64),                              -- 适用范围：领域标识（catalog 同构）
    domain_rules    varchar(64),                              -- 适用范围：领域规则集标识
    sonar_types     jsonb        NOT NULL DEFAULT '[]'::jsonb, -- 类型标签（标题命中权威信号）
    keywords        jsonb        NOT NULL DEFAULT '[]'::jsonb,
    bucket          varchar(64)  NOT NULL,                    -- ref-scripts
    object_key      varchar(512) NOT NULL,
    content_sha256  varchar(64)  NOT NULL,                    -- sha256(脚本文本 utf-8)，与 cad_engine/reference.py 文件缓存同源
    status          varchar(16)  NOT NULL DEFAULT 'pending',  -- pending|active|disabled
    source_type     varchar(32)  NOT NULL,                    -- catalog_import|manual_upload（B3 沉淀预留 deposit）
    source_note     text,                                     -- 来源证据：目录路径/上传者/说明
    created_by      varchar(64),
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (ref_id, version)
);
CREATE INDEX IF NOT EXISTS idx_ref_scripts_status ON ref_scripts (status, ref_id);
COMMENT ON TABLE ref_scripts IS '参考代码范例库登记（T16.1 B1，ADR-015）：检索双源（REF_SOURCE=file|db）的 DB 侧；内容实体在 MinIO 桶 ref-scripts，表存元数据与来源证据';
