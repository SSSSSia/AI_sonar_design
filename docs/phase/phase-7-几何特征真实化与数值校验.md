# 阶段 7 · 几何特征真实化与数值校验（PHASE-007）

| 项目 | 内容 |
|---|---|
| 文档编号 | PHASE-007 |
| 版本 | v1.0 |
| 状态 | 开发中（2026-09-22 开工，T7.0 起） |
| 决策依据 | [ADR-003](../adr/ADR-003-几何自校验路线-特征真实化优先.md) |
| 上游输入 | IR2 参数表（含 unit/source_type）；A3 产出的 STEP（MinIO `step-models/{sid}/sonar_model.step`） |
| 编号说明 | 原 PHASE-006 已被「知识库多库与会话选库」占用（2026-09-20 收官），本阶段顺延为 PHASE-007 |

---

## 1. 阶段定位与边界

### 1.1 问题

A3 成功判据 =「沙箱无 traceback + 产出 STEP」，只保证运行时正确。IR3 的 features 目前是按 IR2 参数 id 批量生成的**占位镜像**（`_build_ir3`：segment 恒 `"model"`、geometry 仅 note），导致：

- 「能编译但几何画错」的 silent geometric error 无确定性校验物；
- A4 `CHK-GEO-LINK` 因镜像构造性恒真，「几何自洽」名存实亡；
- 与已确认 mockup 06 的数值级复核画面（偏差 2.1% 超容差示例）不符，属实现欠账。

### 1.2 本期范围（做）

1. **T7.0** 依赖修复：`trimesh` 补入 requirements。
2. **T7.1** IR3 契约加法演进（`geometry` 内部结构 + `geometry_summary` + `linked_param_id` 可空）。
3. **T7.2** 几何提取模块 `cad_engine/geometry_inspect.py`（STEP → 整体/分段实测几何 JSON，子进程隔离）。
4. **T7.3** A3 接入：用真实提取结果构造 features，替代占位镜像；参数自动联动（长度类）。
5. **T7.4** A4 数值级几何复核（`CHK-GEO-NUM` 等新检查码，确定性无 LLM），含旧数据兼容降级。
6. **T7.5** e2e 回归 + 审阅页/校验报告呈现走查。

### 1.3 非目标（明确不做）

- vision-LLM 视觉反馈闭环（ADR-003 留观，重启条件见 ADR §2.2）；
- 分段语义命名（「湿端段/声阵段/尾段」等名称不由 LLM 事后贴标，v1 用位置编号如实呈现）；
- 脚本级特征 tagging 与 SimpleCADAPI 迁移（中期备选，另立 ADR）；
- 审阅关/gate 语义、管线拓扑的任何改动；
- 参数双向联动重算（E1 细化）——本期只做「实测 vs 参数」单向核对。

---

## 2. 设计

### 2.1 数据流（改后）

```
IR2 ──► A3 生成/修复循环（不变）
              │ sandbox ok，得 STEP
              ▼
   geometry_inspect（子进程，新）
   STEP → 网格 → 整体 bbox/体积/段数 + 分段（连通体 → 半径剖面 → 兜底单段）
              │ 失败 → features=[] + geometry_summary.source="extraction_failed"（管线继续）
              ▼
   IR3（features=真实分段，linked_param_id=自动联动或 null）
              ▼
   A4：引用完整性检查（保留）
       + 数值复核（新）：linked 参数 vs 实测维度，容差可配 → CHK-GEO-NUM
```

### 2.2 几何提取（T7.2 细则）

新模块 `py-engine/cad_engine/geometry_inspect.py`，双入口：

- `inspect_step(step_bytes: bytes) -> dict`（进程内函数，供单测）
- `python geometry_inspect.py <step_path> <out_json>`（CLI，供 A3 以 **subprocess** 调用——复刻 `convert_worker.py` 的 GIL 隔离模式，禁止在 agent 进程内跑 OCCT 镶嵌）

输出 JSON 契约（模块级 docstring 固化）：

```json
{
  "ok": true,
  "overall": { "bbox_mm": [dx, dy, dz], "volume_mm3": 0.0, "part_count": 2 },
  "parts": [
    { "index": 1, "center_mm": [x, y, z], "bbox_mm": [..], "volume_mm3": 0.0 }
  ],
  "meta": { "extractor": "cadquery-occt/trimesh", "tolerance_mm": 0.5, "elapsed_s": 1.2 }
}
```

分段降级阶梯（依次尝试，取第一个产出 >1 段的结果；全部失败则 parts=[overall 单段]）：

1. **连通体拆分**：trimesh `split()`——对未融合的多体模型有效；
2. **半径剖面分段**：顶点按轴向坐标排序，以半径簇变化点切分（拖曳阵=同轴变径回转体，融合成单 solid 时此法有效）；切分点阈值 = 半径变化 > max(5%, 2×tolerance_mm)；
3. **兜底**：整体作为单段（诚实：`segment="SEG-01(整体)"`）。

单位约定：STEP 默认 mm；提取值一律 mm/mm³，与 IR2 参数比较时由 A4 侧换算。

### 2.3 IR3 契约演进（T7.1，加法演进，参照 9228708 先例）

`contracts/ir/ir3.schema.json`：

| 变更 | 类型 | 说明 |
|---|---|---|
| `features[].geometry` 增加可选属性 `bbox_mm:[number×3]`、`volume_mm3:number`、`center_mm:[number×3]` | 加法 | 该字段本为自由 object（「结构随实现细化」），定义化不破坏旧数据 |
| 顶层新增可选 `geometry_summary`：`{bbox_mm, volume_mm3, part_count, source: "extracted"\|"extraction_failed", detail?}` | 加法 | `required` 不加，旧快照免校验失败 |
| `features[].linked_param_id`：`string` → `string | null` | 放宽 | 无匹配参数的分段如实 `null`，**禁止再造假联动**（A-16）；键保留于 `required` 但允许 null |

对应原则：**features 与 IR2 参数不再强绑数量**——参数不因此减少或伪造 feature；未联动参数留在 IR2 原样。

### 2.4 A3 接入（T7.3 细则）

`_build_ir3` 重写：

1. sandbox ok → 上传 STEP（不变）→ subprocess 调 `geometry_inspect.py`（超时 `CAD_INSPECT_TIMEOUT_S=60`，默认）；
2. `ok=true` → features = parts 按轴向排序：`feature_id=F-001…`、`segment="SEG-01"`、`geometry={bbox_mm, volume_mm3, center_mm}`、`linked_param_id=<自动联动结果或 null>`；顶层写 `geometry_summary.source="extracted"`；
3. `ok=false` 或超时 → features=[]、`geometry_summary={source:"extraction_failed", detail:<err>}`，**管线继续**（降级不中断，A-14/A-16），日志 warn；
4. **自动联动规则**（纯函数 `_link_params(params, parts)`，单测覆盖）：
   - 候选参数：`unit ∈ {mm, cm, m, dm}`（长度类），value 换算为 mm；
   - 匹配：参数值 vs 该段 bbox 三维中的最佳相对偏差，`≤ CAD_GEO_LINK_TOL_PCT`（默认 **20%**）→ 联动该维（记录 `geometry.linked_dim: 0|1|2`）；一参多段命中时取偏差最小段；一段只联一参（偏差最小者优先）；
   - 未命中 → `null`，如实。
5. IR3 其余字段（script/step 引用）与既有校验链、修复循环**零改动**（A-08/A-13 语义不动）。

### 2.5 A4 数值复核（T7.4 细则）

`_check_geometry` 增加数值支路（保留既有引用完整性检查）：

| 条件 | 检查 | 码 / 级别 |
|---|---|---|
| `geometry_summary.source=="extracted"` 且某段 `linked_param_id≠null` | 该段 `bbox_mm[linked_dim]` vs 参数值（换算 mm），相对偏差 `≤ CAD_GEO_NUM_TOL_PCT`（默认 **2%**，对应 mockup「2.1% 超容差」口径）→ info（消息含复算值/实测值/偏差%）；超容差 → **error**（消息模板：`{param_id} {name} = {value}{unit}，但关联特征 {fid} 实测 {meas} mm，偏差 {pct}%（容差 {tol}%），建议在审阅关结合 IR4 裁决`） | `CHK-GEO-NUM` / info·error |
| `part_count==0` 或 `overall.volume_mm3<=0` | 退化几何 | `CHK-GEO-DEGENERATE` / error |
| `source=="extraction_failed"` 或 features 空 | 数值自洽未执行，如实提示（不阻断） | `CHK-GEO-NUM-SKIP` / info |
| 旧快照（features 无 `bbox_mm`） | 同上跳过，提示「旧版本快照无数值几何，仅引用完整性校验」 | `CHK-GEO-NUM-SKIP` / info |

确定性约束：A4 保持无 LLM（既有签名）；严重度遵守 A-09（只报告不改参数）；「任一 error 即 fail」沿用现有汇总逻辑。

### 2.6 呈现（T7.5，无新布局）

- **审阅与参数页签 · IR3 建模产物卡**：mockup 05 已有该表（特征/所属段/几何摘要/联动参数）。若现实现已渲染 features → 数据真实化即自动生效，仅做数字格式化（mm→m 显示、千分位、`未联动` 显示「—」）；若未渲染 → 按下述补卡（用户批准本计划即视同 §7.3 迷你 mockup 确认）：

```
┌ IR3 建模产物 ──────────────────────────────────── v1 ┐
│ 特征   所属段   几何摘要                 联动参数      │
│ F-001  SEG-01   Ø54 × 480 mm · 0.9 L    P-004 湿端直径 │
│ F-002  SEG-02   Ø54 × 23500 mm 包络      P-002 阵列孔径 │
│ F-003  SEG-03   Ø40 × 1200 mm           —（未联动）    │
│ 整体：3 段 · 总包围盒 25 480 × 54 × 54 mm · 4.2 L     │
└──────────────────────────────────────────────────────┘
```

- **校验报告页签**：新 CHK 码经既有 issue 渲染链路自动呈现，无需改组件；仅确认消息中文可读、关联参数 id 正确展示。
- 实现前先核对审阅页现状（2026-09-19/20 两轮修复后 ReviewTab 结构可能已调整）。

---

## 3. 任务分解

| 任务 | 内容 | 主要文件 | DoD（同 commit 回填 roadmap+devlog） | 估时 |
|---|---|---|---|---|
| T7.0 | requirements 补 `trimesh>=4.0`（注释写明：convert_worker/geometry_inspect 实际依赖，修复新环境转换链路断裂） | `py-engine/requirements.txt` | 干净 venv `pip install -r` 通过；`test_convert_worker.py` 绿 | 0.1d |
| T7.1 | IR3 契约加法演进（§2.3）+ 契约测试新旧两形态均合法 | `contracts/ir/ir3.schema.json`、契约测试 | 旧形态 IR3（无新字段/非空 linked）仍通过；新形态（null linked + geometry 数值 + summary）通过；**提交信息标 `api:`**（宪法 §5） | 0.5d |
| T7.2 | `geometry_inspect.py` 双入口 + 分段降级阶梯（§2.2）+ 单测（合成双圆柱 STEP：part_count/bbox/体积误差断言；融合单 solid：半径剖面切分生效） | `py-engine/cad_engine/geometry_inspect.py`、`tests/` | 子进程调用验证；失败路径返回 `{ok:false}` 不抛异常；OCCT 调用不出现在 agent 进程 | 1d |
| T7.3 | A3 接入：`_build_ir3` 重写 + `_link_params` 纯函数 + 提取失败降级 + CLI 调试入口回归 | `py-engine/agents/a3_cad/agent.py`、`prompts.py`（ECIP 风格注释建议，可选） | A3 单测（mock 提取器）全分支：成功联动/部分联动/全未联动/提取失败；IR3 过新 schema；`CAD_FIX_MAX_ROUNDS` 语义不变 | 1d |
| T7.4 | A4 数值复核（§2.5 全表分支）+ 容差环境变量 `CAD_GEO_LINK_TOL_PCT` / `CAD_GEO_NUM_TOL_PCT` | `py-engine/agents/a4_validation/agent.py`、`tests/` | 单测覆盖 5 分支（pass-info / 超 2% error / 未联动跳过 / extraction_failed 跳过 / 旧快照跳过）；A4 仍无 LLM、确定性 | 0.5–1d |
| T7.5 | e2e 回归 + UI 走查 + 验收记录回填 | — | 见 §4 验收标准；截图留档本文件 §5 | 0.5d |

合计 ≈ 3.5–4 人日。与 v0.2 迭代其他项（容器化/bundle 分割/待接能力，见 HANDOFF 下一步入口 0）可并行，模块不相交（本期全部在 py-engine + contracts）。

**T7.5 前置条件**：`llm_providers` 表当前 0 行（HANDOFF 待验证项）——e2e 前重播 enabled mock 行或确认走 env 兜底，避免假失败。

---

## 4. 阶段验收标准

1. **契约**：新旧两种 IR3 形态均通过 schema 校验；`api:` 提交可追溯。
2. **单测**：geometry_inspect（含分段阶梯两分支）、`_link_params`、A4 数值支路 5 分支、A3 降级路径全绿；既有 workflow 单测不回归。
3. **e2e**：`test_towed_array_e2e.py` 全绿；新会话 IR3 满足——features 数 = 实测段数、`geometry_summary.source=="extracted"`、至少一个长度类参数自动联动；IR4 含 `CHK-GEO-NUM` info（或如实 error）条目；无 `CHK-GEO-NUM-SKIP`（新会话不得命中跳过分支）。
4. **UI 走查**（1440 + 640）：审阅页特征表呈现实测数字与「—（未联动）」；校验报告新条目中文可读、关联参数正确；旧会话打开不报错、如实显示跳过提示。
5. **诚实性抽查（A-16）**：人为把 e2e 需求中阵元间距改错（如 2m→20m）重跑 A3+A4，IR4 必须产出 `CHK-GEO-NUM` error——即 mockup 06 场景实机复现一次（作为验收证据截图）。
6. 宪法附录 B DoD 逐项核对（roadmap/devlog/文档同 commit、契约测试同步）。

## 5. 阶段验收记录

| 项 | 结果 | 证据 | 日期 |
|---|---|---|---|
| （待验收时回填） | | | |

## 6. 风险与回滚

| 风险 | 概率 | 缓解 | 回滚 |
|---|---|---|---|
| 拖曳阵融合为单 solid，连通体拆分失效 | 高（预期内） | 半径剖面分段（§2.2-2）；再不行兜底单段——校验价值仍在（整体 bbox vs 全长类参数） | 兜底路径即回滚态，无阻断 |
| 半径剖面切分点误判（圆滑过渡段） | 中 | 阈值可调（5%/2×tol）；误切只是多分段，不影响 overall 校验与 error 判定 | 调参或关至兜底 |
| 自动联动误配（参数值恰近无关维度） | 低 | 20% 粗匹配 + 仅长度类单位；误配后果仅为多一条 info/error，人工 gate2 可见可裁 | `CAD_GEO_LINK_TOL_PCT` 调 0 关闭联动 |
| 数值 error 使既有易跑通过的会话变 fail | 低 | 只对「已联动」参数检查；容差 2% 对齐 mockup 口径；上线前用验收预跑 A/B 重放核对 | `CAD_GEO_NUM_TOL_PCT` 放宽 |
| 提取子进程挂死拖慢 A3 | 低 | 60s 超时 + 失败降级（管线继续）；复用 convert_worker 陈旧判定经验 | 环境变量关提取（`CAD_INSPECT_TIMEOUT_S=0` 视为跳过，summary 如实标 disabled） |

## 7. 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-22 | 初版：依据 ADR-003 定稿，T7.0–T7.5 与验收标准；编号自 PHASE-006 顺延为 PHASE-007（006 已被知识库多库占用） |
