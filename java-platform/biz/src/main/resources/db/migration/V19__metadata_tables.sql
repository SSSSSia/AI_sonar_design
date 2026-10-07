-- T13.7（总体设计 §5.16 基础元数据管理 + 附录 D 元数据字典）：四子项落地——
-- 声呐类型（安装形式×功能体制双维度枚举）、频率段定义、性能参数规范、提示词模板。
-- LLM 模型注册表（llm_providers）v0.1 已落地——四表补齐后 §5.16 全覆盖。
-- 种子数据取自附录 D（示例口径，如实标注 example）。

-- ---------- 声呐类型（双维度：install_form × functional_system） ----------
CREATE TABLE IF NOT EXISTS meta_sonar_types (
    id                 bigserial PRIMARY KEY,
    install_form       varchar(32)  NOT NULL,              -- 艇艏阵 | 舷侧阵 | 拖曳阵
    functional_system  varchar(32)  NOT NULL,              -- 侧扫 | 多波束 | 前视 | 合成孔径(SAS) | 被动
    description        text,
    enabled            boolean      NOT NULL DEFAULT true,
    created_at         timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (install_form, functional_system)
);

-- ---------- 频率段定义 ----------
CREATE TABLE IF NOT EXISTS meta_freq_bands (
    id                 bigserial PRIMARY KEY,
    name               varchar(32)  NOT NULL UNIQUE,       -- 低频段 | 中频段 | 高频段 | 甚高频段
    freq_min_hz        numeric,
    freq_max_hz        numeric,
    application        text,                                -- 应用场景
    reference_standard varchar(128),                        -- 参考军标
    enabled            boolean      NOT NULL DEFAULT true,
    created_at         timestamptz  NOT NULL DEFAULT now()
);

-- ---------- 性能参数规范 ----------
CREATE TABLE IF NOT EXISTS meta_perf_specs (
    id                 bigserial PRIMARY KEY,
    name               varchar(64)  NOT NULL UNIQUE,       -- 探测距离 | 方位分辨率 | 距离分辨率 | 发射声压级 …
    unit               varchar(32),
    value_min          numeric,
    value_max          numeric,
    definition         text,                                -- 业务定义
    enabled            boolean      NOT NULL DEFAULT true,
    created_at         timestamptz  NOT NULL DEFAULT now()
);

-- ---------- 提示词模板（版本化：同一 (stage, name) 多版本，启停切换） ----------
CREATE TABLE IF NOT EXISTS meta_prompt_templates (
    id                 bigserial PRIMARY KEY,
    stage              varchar(32)  NOT NULL,              -- a1_requirement | a2_scheme | a3_cad | a4_validation | a5_knowledge | retrieval | general
    name               varchar(64)  NOT NULL,
    version            int          NOT NULL DEFAULT 1,
    template_text      text         NOT NULL,
    enabled            boolean      NOT NULL DEFAULT false, -- 多版本仅一个启用（应用侧约束）
    remark             text,
    created_at         timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (stage, name, version)
);
CREATE INDEX IF NOT EXISTS idx_prompt_templates_active ON meta_prompt_templates (stage, name, enabled);

-- ---------- 附录 D 种子（示例口径） ----------
INSERT INTO meta_sonar_types (install_form, functional_system, description) VALUES
    ('艇艏阵', '被动', '示例条目：艇艏安装被动听测基阵（附录 D）'),
    ('舷侧阵', '被动', '示例条目：舷侧安装被动阵（附录 D）'),
    ('拖曳阵', '被动', '示例条目：拖曳被动线列阵（附录 D）'),
    ('艇艏阵', '多波束', '示例条目：艇艏主动多波束（附录 D）'),
    ('拖曳阵', '侧扫', '示例条目：拖曳侧扫（附录 D）'),
    ('拖曳阵', '合成孔径(SAS)', '示例条目：拖曳合成孔径声呐（附录 D）')
ON CONFLICT (install_form, functional_system) DO NOTHING;

INSERT INTO meta_freq_bands (name, freq_min_hz, freq_max_hz, application, reference_standard) VALUES
    ('低频段', 100, 1000, '远距离探测、掩埋目标（示例）', NULL),
    ('中频段', 1000, 10000, '通用探测与成像（示例）', NULL),
    ('高频段', 10000, 100000, '高分辨率近程成像（示例）', NULL),
    ('甚高频段', 100000, 1000000, '超近程精细成像（示例）', NULL)
ON CONFLICT (name) DO NOTHING;

INSERT INTO meta_perf_specs (name, unit, value_min, value_max, definition) VALUES
    ('探测距离', 'm', 100, 100000, '满足给定检测概率/虚警下的最大作用距离（示例）'),
    ('方位分辨率', '°', 0.1, 10, '区分同距离两目标的最小方位角间隔（示例）'),
    ('距离分辨率', 'm', 0.01, 10, '区分同方位两目标的最小距离间隔（示例）'),
    ('发射声压级', 'dB re 1µPa@1m', 180, 240, '发射器轴上 1m 处声压级（示例）')
ON CONFLICT (name) DO NOTHING;
