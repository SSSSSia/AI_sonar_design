-- =====================================================================
-- Sonar-CAD v2 · V3 统一 LLM 适配器多供应商配置表（T1.2，D-12）
-- 约束：api_key 任何序列化输出必须脱敏（宪法 §10.2）；供应商可配置热切换、
-- 禁止硬编码单一供应商。管理界面/API 随 T3.3（5.16 提示词与配置管理）补全，
-- 本期由 SQL 维护 + py-engine 侧带 TTL 的热加载。
-- =====================================================================

CREATE TABLE llm_providers (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name       varchar(64)  NOT NULL UNIQUE,
    base_url   varchar(256) NOT NULL,
    api_key    varchar(256) NOT NULL,
    model      varchar(128) NOT NULL,
    enabled    boolean      NOT NULL DEFAULT false,
    priority   integer      NOT NULL DEFAULT 0,             -- 同 enabled 中取最大者
    timeout_s  integer      NOT NULL DEFAULT 60,
    created_at timestamptz  NOT NULL DEFAULT now(),
    updated_at timestamptz  NOT NULL DEFAULT now()
);
COMMENT ON TABLE llm_providers IS '统一 LLM 适配器多供应商配置（D-12）：py-engine 按 priority 热切换；api_key 输出必须脱敏';
