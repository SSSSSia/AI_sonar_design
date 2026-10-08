# 阶段 14 开发文档 · 可信度加固——外部审查整改（v0.7）

| 项 | 内容 |
|---|---|
| 文档编号 | PHASE-014 |
| 版本 | v1.0（2026-10-08，编制；外部审查 12 组问题逐条核验后全量整改） |
| 关联 | [外部审查核验报告](../reviews/2026-10-02-外部审查核验.md)（逐条结论与证据）、总体设计方案 §5.4/§5.6/§7.3/§7.4、[PHASE-012](phase-12-二期收尾.md)（E1 变更抑制——与本阶段正交）、[PHASE-011](phase-11-业务泛化与生产上线.md)（E1 反向回写）、[PHASE-008](phase-8-CAD交互微调-FreeCAD集成.md)（FreeCAD 执行链）、roadmap 阶段 14 |
| 阶段目标 | 按"第一批堵错优先"清掉核验确认的 9 组真实缺陷：单位统一、校验状态学与覆盖率、提案正确性、产物版本锁定、审阅版本条件、依赖重算、插件执行链修复（真 bug）、检索/知识小项清账；采纳审查 17 条回归验收案例为 DoD 基准；收官 tag `v0.7.0` |
| 验收形态 | 17 条案例分层执行（纯函数/接口/真机）+ py 单测 + Java 测试 + vitest/tsc + e2e 全量回归 + 文档回填 |

---

## 1. 阶段定位与边界

### 1.1 为什么有这个阶段

2026-10-02 外部审查（对 v0.4.0 快照）经逐条核验（见核验报告）：核心批评"参数、模型、依据、报告不总是对应同一份有效设计；部分错误或未检查项不能可靠阻止系统给出『通过』"在 v0.6.0 依然成立——solver/a3_cad/executor 三个关键模块自 v0.4.0 零改动，v0.5.0 变更抑制与正确性问题正交，且插件 executor 存在一个比重审时更恶化的导入真 bug（每条 LLM 编辑指令必然失败）。审查建议"下一轮优先提高设计结果的可信度，再扩展功能"——本阶段即该轮。此前"无可开工阶段"的判断被这份外部输入推翻：可信度缺陷是真实需求驱动。

### 1.2 本期范围（做）

| 任务 | 对应审查问题 |
|---|---|
| T14.1 FreeCAD 插件执行链修复（导入真 bug/目标锁定/result 严格校验） | 问题 6a/6b/6c |
| T14.2 提案应用正确性与确认幂等（多轮取代/按符号同步/幂等） | 问题 5a/5b + 11 残留 |
| T14.3 单位统一模块（解析/换算收口 + 公式单位登记） | 问题 1 |
| T14.4 校验状态学与验证覆盖率（三态 overall/unverified/来源解耦/coverage） | 问题 2 全部四子项 |
| T14.5 产物版本化与报告失效全覆盖 | 问题 4a/4b |
| T14.6 审阅版本条件（expected_snapshot_id 乐观绑定） | 问题 4c |
| T14.7 公式依赖声明与按依赖重算 + 复算取数源切换 | 问题 8 |
| T14.8 小项清账（缓存键范围/选库时序/Milvus 空范围/A5 过滤/审计覆盖） | 问题 9b/9c/10 |
| T14.9 收官 + tag v0.7.0 | — |

### 1.3 非目标（明确不做）

- 问题 3+7（建模时参数→对象映射架构、A3 结构化参数注入、按尺寸语义分检查器、SEG 稳定标识）——同一架构改动，设计量大，**立账阶段 15**（§7）；
- 问题 12 深化（统一检查表视图/全参数 diff/冻结版本导出门/未决项章节）——体验增强，立账阶段 15；
- 声学公式物理模型的专业适用性审定（审查自己声明未做，非软件工程范畴）；
- 军标条目实质扩充（仍待授权文本，外部阻塞）；
- IR3 特征提取/联动算法重写（问题 3 本体留阶段 15，本阶段只在其上游下游堵错误结论的出口）。

## 2. 设计要点

### 2.1 FreeCAD 插件执行链修复（T14.1）

- **导入修复**：`executor.py` `from snapshot_lite import take` → `from sonarlink.cad import snapshot_lite`（与 panel.py:22 对齐）——当前每条编辑指令在 :178 必抛 ModuleNotFoundError。
- **目标锁定**：编辑任务协议增 `target_object_name`（可选）；插件载入版本产物时记录主实体对象 Name 并随任务上下文回传；`execute_edit` 优先按名从 ActiveDocument 取对象（`doc.getObject(name)`），取不到才回退 `main_model_shape`（最大体积）并在回执 `doc_summary.target_fallback=true` 标注——修"切削后体积变小、下轮选回原模型"。服务端下发编辑代码时若 IR3/任务带对象名则透传。
- **result 严格校验**：`_as_shape` 只接受 FreeCAD Shape / 带 `.Shape` 属性的文档对象 / CQ Workplane；数值、字符串、None 直接抛 ValueError；result 契约校验追加 `ShapeType` 非空、`isValid()`、体积/体积>0 检查（点云/线框 result 明确拒绝或警示按形类型）。
- **幂等边界如实**：execute_edit 无服务端操作幂等键的现状保留（协议层 call_id 409 已有），执行器侧重放风险记录为已知边界（A-16），不在本期引入事务化执行。

### 2.2 提案应用正确性与确认幂等（T14.2）

- **多轮取代语义**：`apply_proposals` 入口先归并——按 (round_no, created_at) 升序排序，同 `param_id` 只保留最新一条，被取代者状态标 `superseded`（状态机加该终态，单向不可回退）；API 确认端点（全量确认=空 proposal_ids）做同样归并并返回实际应用清单与被取代清单——审查用例 05 终值必须=140。
- **按符号同步推导**：`sync_derivation` 加 `symbol` 参数（定位来源：参数 source_ref 符号 / 推导文本中与该参数关联的主符号），`_sub` 只替换 `symbol=数值` 形态且 symbol 精确匹配的片段；找不到目标符号→仅追加修订记录（现行为），**绝不**替换其他符号的同值数字——用例 06 width 不串改。
- **幂等**：确认端点对非 proposed 提案跳过（已 applied 幂等返回同一结果摘要，不重复写 IR2 版本）；`apply_forward_update` 共用 sync_derivation 修复。

### 2.3 单位统一模块（T14.3）

- 新 `py-engine/solver/units.py`：
  - `parse_quantity(v) -> ParsedQuantity(value, unit, kind)`：数值直取；字符串抽 value+unit（长度族 mm/cm/dm/m、无量纲计数、中文量词「个/只/节/阵元」映射 dimensionless）；**范围字符串/模糊表达/未知单位 → 抛 `UnresolvableQuantity`**（不猜、不取首数继续算）。
  - `convert(value, from_unit, to_unit)`：同 kind 换算；跨 kind 抛错。
- `formulas.py`：`FormulaSpec.symbols` 值支持两态——旧 `str`（描述）兼容 / 新 `dict`（`{"desc": ..., "unit": "m", "min": ..., "max": ...}`）机器可读；`available_inputs`/`evaluate_available` 计算前把输入统一换算到公式单位；范围越界 → 明确结果状态而非静默代入。
- **收口**：A4 recompute、E1 `param_proposals.py` UNIT_TO_MM、`geometry_link.py` 单位表全部改引 units.py（单点维护）；`standards.py:14`「单位换算列二期」注释兑现：军标求值入口接入 units 换算（demo 条目口径不变）。
- 边界如实（A-16）：本期覆盖长度+无量纲两族；角度/温度/声速等族按需后补，遇到未登记单位一律"未验证"，不扩展猜测。

### 2.4 校验状态学与验证覆盖率（T14.4，最大件）

- **IR4 schema 演进（向后兼容）**：`overall` enum 扩为 `["pass","fail","incomplete"]`（旧快照两态值天然合法，只扩不缩）；issue `severity` 增 `"unverified"`。
- **A4 升级规则**：公式无法定位 / derivation 缺代入值 / 复算输入不全 / 几何未联动或提取失败（CHK-GEO-NUM-SKIP 族）/ 军标条目待补 → 一律 `unverified`（原 info 升级，消息保留人工复核指引）；`overall` 判定：有 error → fail；无 error 但有 unverified → **incomplete**；否则 pass。
- **来源解耦**：`_check_standard` 去掉 `source_type != "standard"` 分流——按规则 `applies_to` 对**全部参数**做适用性核对（参数来源仅用于展示与追溯）；case/estimated 来源参数同样受军标约束（审查用例 13）。
- **coverage 字段**：IR4 增 `coverage = {"checked": n, "applicable": m, "ratio": x}`——必检项 = formula 参数复算 + 适用军标条目核对 + 几何联动期望比对；口径注释写死（同 CSR 惯例）；CSR 口径保持不变并继续文档化（两者分母不同、并列展示）。
- **展示同步**：ReportTab IR4 卡三态徽标+覆盖率进度、issue 级别色（unverified=灰黄）、ParamTable 来源列与适用规则联动提示；导出 docx/PDF 校验结论表增"状态"列与覆盖率行。
- **兼容**：旧 IR4 快照（pass/fail）不迁移；前端对两态旧数据正常渲染（incomplete 分支仅新数据出现）。

### 2.5 产物版本化与报告失效全覆盖（T14.5）

- **STEP 版本化**：`a3_cad/agent.py` 上传键 `{sid}/a3/{ir3_version|ts}/sonar_model.step`（每次生成新前缀，旧对象天然保留）；IR3 快照/产物记录 `step_object_key` + 内容 `sha256`（用例 10 哈希核对）；下载端点按快照引用的 object_key 取（历史版本可完整复现）。
- **失效全覆盖**：`mark_invalidated(["IR4","IR5"])` 补齐三路径——①微调 `finalize_version`（对话轮+manual 上传，失效后走既有 gate2 复审链路重派）；②忽略全部提案（忽略≠维持旧结论：模型已变）；③0 提案路径（分段不一致/链式微调）。失效记录 reason（tune_upload/proposals_ignored/no_proposals）。
- 用例 08：任何模型变更后，旧报告不以现行身份展示（界面上标"已失效——模型已更新待重验"）。

### 2.6 审阅版本条件（T14.6）

- 前端 ReviewTab 载入审阅数据时记录 IR3/IR4 `snapshot_id`；提交裁决带 `expected_snapshot_id`（workflow.ts DecideRequest 增字段）。
- Java `ReviewController.DecideRequest` + `ReviewService.decide`：expected 与该会话该 IR 类型 latest 快照不符 → 409 `SNAPSHOT_STALE`（消息带两个版本号）；前端捕获后提示"模型已更新，请刷新复核后重新裁决"，不自动代批。
- 兼容：expected 缺省（旧客户端/脚本）保持现行为——字段可选不破坏既有调用。

### 2.7 公式依赖声明与按依赖重算（T14.7）

- `kb_formulas` 表 + `FormulaSpec` 增 `depends_on`（`{"n": "param:ARR_N", "d": "param:ARR_D"}` 形态：公式符号→上游 IR2 参数 id）；solver/recompute 建最小依赖图（formula 参数 → 其 inputs 中的上游参数）。
- **受控下游重算**：E1 正向更新/提案应用落库后，遍历依赖图找受影响 formula 参数→按公式+最新上游值重算→直接更新 value+derivation（修订段标注「依赖重算」+ 触发参数与原值）；公式无法定位/输入不全/超范围 → 生成 pending 提案（不静默、不阻断主变更）。
- **A4 复算取数源切换**：复算 inputs 优先从 IR2 参数快照按符号映射取（上游参数当前值），derivation 文本解析降为兜底并在 issue 消息标注「输入取自推导文本」——推导文本只承担展示，不再当事实存储。

### 2.8 小项清账（T14.8）

- **缓存键范围语义**：`kb_cache_key` 键形态 `cache:kb:query:{fingerprint(query, scope_mode, scope_fingerprint)}`——`None → "unscoped"`、`[]/kb_ids → "scoped:" + 解析后有效 doc 集指纹`；None 与 [] 不再同键（用例 15）。
- **选库时序**：`worker/tasks.py` 会话未选库时先 `route_bases` 定范围、再带范围发起 A1 检索（选库结果先落 `ir1["kb_selection"]` 再检索）；`route_bases` 逐条子查询探测合并（修只测第一条）。
- **Milvus 空范围口径对齐**：scoped 解析为空集时 vector 路径同样返回 unavailable（与文本级一致），不传 `[]` 触发全库。
- **A5 LLM 路径过滤**：`_sanitize` 硬滤 error 点名参数（与兜底同规则）+ prompt 增排除指令；IR5 candidate 增 `source_refs`（ir2_version + 校验报告快照标识）。
- **审计覆盖**：`api/audit.py` 规则表补 `/knowledge/writeback`（现无 /api/v1 前缀漏覆盖）与 `/knowledge/skip`。

## 3. 任务分解（T14.1–T14.9）

### T14.1 FreeCAD 插件执行链修复 ⬜→✅ 已完成（2026-10-08）

- 改动：`executor.py`（顶层导入 snapshot_lite 修真 bug / `_TARGET` 目标锁定+`_resolve_model` 回退标注 / `_as_shape` 严格化 + result 空形体与实体非空校验 / `import_step` 登记主实体 / `_update_lock` 滚动锁定）；新增 `tests/test_t141_executor.py` 7 例（桩形体替身）。
- DoD：连续切削→打孔作用于同一演进对象 ✅（test_edit_ok_and_lock_continuity）；result=42 → ok=False ✅；插件套件 18 绿 ✅；真机冒烟待联调（本机无 FreeCAD，A-16 如实）。
- 决策：目标锁定为插件本地锁（D14-5），服务端协议零改动。
- Commit：`fix(cad): T14.1 FreeCAD 执行链修复——导入真 bug/目标锁定/result 严格校验（外部审查问题 6）`

### T14.2 提案应用正确性与确认幂等 ✅ 已完成（2026-10-08）

- 改动：`param_proposals.py`（`merge_by_param` 归并 / `sync_derivation` 目标符号定位：亲缘符号精确匹配→单值回退→同值歧义不内联只留痕 / `_own_symbol` 参数 id 亲缘解析）、`proposal_store.py`（superseded 终态）、`api/cad_agent.py`（confirm 端点归并+取代标注+round 取最新）。
- DoD：用例 05（100→120→140 终值 140）✅、06（width/length 同值只改目标）✅、11（重复确认走重试路径不追加 IR2 版本）✅——新增 test_t142 12 例，E1 既有 27 例回归绿。
- Commit：`fix(e1): T14.2 提案应用正确性——多轮取代/按符号同步推导/确认幂等（问题 5/11）`

### T14.3 单位统一模块 ✅ 已完成（2026-10-08）

- 改动：新 `solver/units.py`（Quantity 解析 + 同族换算 + UnresolvableQuantity；length/frequency/level/speed/dimensionless 五族，节=kn 按 1852m/h 精确注册）；`formulas.py`（symbols 升级 `{"desc","unit"}` 机器可读登记、available_inputs 单位感知、min/max 字段预留暂不造数）；`intervals.py`（候选池单位感知）；`geometry_link.py` UNIT_TO_MM 收口至 `units.LENGTH_TO_MM`；`standards.py` 长度族 value 绑定统一换算 mm（「单位换算列二期」注释兑现）。
- 口径（A-14/A-16 如实）：裸数值按符号登记单位直取（v0.6 前契约兼容）；带单位同族换算；未知单位/范围表达/数字后未识别内容一律 UnresolvableQuantity→候选剔除。**本期修"产出侧"（A2 公式计算/区间传播/军标 value 绑定）；"验证侧"（A4 复算取数源）随 T14.7 切换**。
- DoD：用例 01 ✅（20 mm/2 cm/0.02 m 同归一值、f_beam 均 37500——审查原始探针复现用例转绿）；未知单位/范围拒绝 ✅；新增 test_t143 19 例；**py 全量 410 passed 1 skipped 零破坏**。
- Commit：`feat(solver): T14.3 单位统一模块——量纲解析换算收口+公式单位登记（问题 1）`

### T14.4 校验状态学与验证覆盖率 ✅ 已完成（2026-10-08）

- 改动：`contracts/ir/ir4.schema.json`（overall 三态/severity 四级/coverage 字段，加法演进）；`agents/a4_validation/agent.py`（unverified 升级族/来源解耦/coverage 统计器/overall 优先级 error>unverified）；`solver/standards.py`（check_param 增逐条目 entries verdict）；`docgen/export_docx.py`（三态结论+覆盖率行+未验证标签）；frontend（workflow.ts 类型/ReportTab 三态徽标+覆盖率+SEVERITY_META/SessionPage 页签/空方案文案）；编排层消费面核查——overall 仅日志级别与前端展示，incomplete 不卡管线。
- 既有测试语义更新 11 处逐个记录于 [devlog 2026-10-08](../devlog/2026-10-08.md)；新增 test_t144 5 例。
- DoD：用例 13 ✅（case 来源参数经军标 expr 判定计数）、14 ✅（unverified → incomplete 不显示通过）；py 相关 67+9 绿、全量 421 passed 1 skipped；tsc 0/vitest 43 绿；旧两态快照 schema 兼容验证 ✅。
- Commit：`feat(a4): T14.4 校验状态学——三态 overall/unverified 级/来源解耦/验证覆盖率（问题 2）`

### T14.5 产物版本化与报告失效全覆盖 ✅ 已完成（2026-10-08）

- 改动：`agents/a3_cad/agent.py`（产物上传提为 `_upload_artifacts`：`{sid}/a3/{token}/` 按次版本化，固定路径 `{sid}/sonar_model.step` 废弃）；`storage/object_store.py`（upload_artifact/upload_bytes 返回 sha256）；`contracts/ir/ir3.schema.json`（file_ref 增可选 sha256，加法演进）；`cad_agent/service.py` finalize_version（IR3 落新版本后立即 mark_invalidated(["IR4","IR5"])——对话轮与 manual 上传共用此函数，忽略提案/0 提案路径随之全覆盖）；`api/cad.py` download 增可选 version 参数（历史 IR3 按版本独立下载+哈希核对）。
- 语义依据：get_latest_snapshot 对算力侧读过滤已失效版本——失效后旧报告不以现行身份存在；微调复审链路照旧（gate2 人工/提案确认触发重验）。
- DoD：用例 08 ✅（finalize 即失效，不经提案应用路径）、10 ✅（两次生成对象键互异+哈希返回）；新增 test_t145 4 例；受影响面（t73/t83/t113/t142）回归绿。
- Commit：`fix(artifact): T14.5 产物版本化+报告失效全覆盖——STEP 按版本落桶/微调定稿即失效（问题 4a/4b）`

### T14.6 审阅版本条件 ✅ 已完成（2026-10-08）

- 改动：Java `ReviewController.DecideRequest` 增 `expectedSnapshotVersion`（可选）；`ReviewService.decide` 乐观版本条件——被审快照（gate1→IR2 / gate2→IR3 / knowledge→IR5）最新版本与审阅人所见不符 → 409 `SNAPSHOT_STALE`（消息带两个版本号）；前端 `postReview` 增 `expectedSnapshotVersion`，ReviewTab 接收 `versions` props 按 gate 携带（gate1→IR2 / gate2→IR3 / knowledge→IR5）。
- 决策 D14-6：用**版本号**而非快照 uuid 做条件——IrEnvelope 已暴露 version，前端零新增读侧面；字段可选，缺省（旧客户端/脚本）保持现行为。
- DoD：审查用例 09 ✅（旧页面批准新模型被拒 409、不落裁决记录不派发任务；版本匹配照常通过；knowledge 关同样设防）——新增 Java 测试 3 例，workflow 42 绿；tsc 0/vitest 43 绿。
- Commit：`feat(review): T14.6 审阅版本条件——expectedSnapshotVersion 乐观绑定 409 SNAPSHOT_STALE（问题 4c）`

### T14.7 公式依赖声明与按依赖重算 ✅ 已完成（2026-10-08）

- 改动：新 `solver/deps.py`（依赖图纯函数：参数名经 SYMBOL_ALIASES 映射公式符号——**会话内现算，无需迁移表**；registry 无命中的动态公式不建边不重算，A-16 如实；BFS 拓扑+环保护+深度上限）；`cad_agent/param_proposals.py` cascade_dependents（下游 formula 参数直接更新 value+derivation（结构化重建+「依赖重算」留痕，D14-3），输入从当前快照取经单位换算）；`api/cad_agent.py` 正向更新/提案确认两入口接线（响应增 cascaded 明细）；A4 `_check_formula` 取数源切换（快照优先，derivation 解析降为兜底并在 error 消息标注「输入取自参数快照」）。
- 口径：改 n=16→32 → DI 12.0412→15.0515 自动重算；过期公式参数（上游已变、落库值未变）被 A4 以 error 暴露——不再被旧 derivation 掩护。
- DoD：用例 17 ✅（级联重算+A4 用新值）；新增 test_t147 6 例；全量 py 427 passed 1 skipped；tsc 0/vitest 43 绿。语义更新（test_case_interval：采纳值 vs 上游不一致同时暴露 error）入 devlog。
- Commit：`feat(solver): T14.7 公式依赖重算——deps 依赖图+下游受控重算+A4 复算取数源切换（问题 8）`

### T14.8 小项清账 ✅ 已完成（2026-10-08）

- 改动：`storage/cache.py` kb_cache_key 范围语义入键（None=unscoped 与 []/库列表=scoped 不再同键，用例 15）；`knowledge/service.py`（scoped 解析空集 → 与文本级同口径 unavailable，不传空列表给 Milvus 退化全库；route_bases 逐条查询探测合并 hits，修只测首条）；`worker/tasks.py`（auto 选库**先于检索**——IR0 原文切段逐库探测圈定范围，A1 检索在选定范围内进行，留痕=真实使用范围，检索后留痕废弃）；`agents/a5_knowledge/agent.py`（LLM 整理路径排除 error 点名参数——与兜底同规则；候选 payload 绑 source_refs={ir2_version, ir4_version}，worker 传入）；`api/audit.py`（/knowledge/writeback 与 /knowledge/writeback/skip 纳入审计规则表——v0.6 无 /api/v1 前缀漏覆盖面）。
- DoD：用例 15 ✅（缓存键隔离+空集不越界）、16 ✅（LLM 路径排除污点参数+版本绑定）；新增 test_t148 9 例；语义更新（t62 route_bases hits 合并计数）入 devlog；全量 py 435 passed 1 skipped。
- Commit：`fix(kb): T14.8 检索范围缓存/选库时序/A5 候选过滤/审计覆盖清账（问题 9/10）`

### T14.9 收官 + tag v0.7.0 ✅ 已完成（2026-10-08）

- 全量回归：py 435 / Java 86 / vitest 43 / tsc 0 / **e2e 7/7（真实 LLM 逐文件）**；17 条案例核对回填核验报告 §3（13✅/2◐/2 阶段 15）；三镜像重建+实机验证（旧 IR4 兼容读取/供应商登记/引擎恢复）；GAP-001 §4 v0.7 行、HANDOFF 入口 9→10/11、用户指南 v0.7（三态/覆盖率/SNAPSHOT_STALE/依赖重算）。
- Commit：`chore(release): T14.9 收官——回归+文档回填+tag v0.7.0`

## 4. 阶段管理

### 4.1 风险与对策

| 风险 | 对策 |
|---|---|
| T14.4 状态学升级破大量既有测试断言（info→unverified、两态→三态） | 执行期逐个更新，devlog 记录清单；schema 只扩不缩保证旧快照可读 |
| T14.3 单位模块改动波及 A2/A4/E1 三链 | 先立模块+单测再切挂点；未知单位宁"未验证"不猜（宁可漏算不可错算） |
| T14.5 失效全覆盖可能让微调流程频繁重派 A4 | 失效与重派分离：失效即时（状态正确性优先），重派按路径既有链路；用户指南说明行为变化 |
| T14.7 依赖重算与变更抑制（Origin Lock）叠加语义 | 依赖重算只在变更已被人工确认后触发，且不反向触发新一轮抑制计数；超深度走既有 409 |
| FreeCAD 真机不可用 | stub 单测+静态检查兜底，任务书如实标待联调（A-16） |

### 4.2 验收流程（阶段末）

17 条案例分层跑（纯函数 → 接口/DB → 可用真机链路）+ 全量回归（py/e2e/Java/vitest/tsc）→ 核验报告 §3 回填 → 收官 tag。

### 4.3 附录 A 自检（宪法，发布检查清单）

- A-12 会话数据不可变：STEP 版本化后旧对象不删不覆盖 ✅；提案 superseded 只加状态不改历史 ✅
- A-14 如实：插件真机冒烟状态、单位覆盖族边界、军标适用覆盖现状——任务书/核验报告如实标注
- A-16 无数据不造假：未知单位/无必检口径 → "未验证"，不产出猜的结果；coverage 无必检项时如实 None
- 宪法 §10 密钥：本阶段无新增密钥面；审计 detail 沿用不落敏感明文口径

## 5. 前置发现（编制期实证，核验报告为详版）

① 插件 executor 导入真 bug（重构漏改，编辑链路实际已坏）；② solver/a3_cad/executor 三模块 v0.4.0 起零改动；③ A3 prompt 反模式条款允许改振幅（制度化例外，随阶段 15 一并处理，本期不动）；④ `/knowledge/writeback` 不在审计规则表（v0.6.0 遗漏面）；⑤ Milvus scoped 空范围全库（与文本级口径不一致）；⑥ 提案确认端点幂等依赖状态机单向隐式保证，无显式幂等返回。

## 6. 决策记录

| # | 决策 | 理由 |
|---|---|---|
| D14-1 | 问题 3+7 立账阶段 15 不进本期 | 同属"建模时参数→对象映射"架构改动，牵涉 A3 生成契约/特征提取/检查器体系，独立成阶段避免本期失焦 |
| D14-2 | CSR 口径保持不变，另加 coverage 并列展示 | CSR 已文档化且前端/导出广泛引用；覆盖率补"验证了多少"维度，两者分母不同语义互补，不混单指标 |
| D14-3 | 依赖重算采用"直接更新+留痕"而非"生成二级提案" | A4 复算兜底存在（复算不符即 error），直接更新+修订段留痕链路最短；无法定位/超范围才升级提案 |
| D14-4 | IR4 severity 用新枚举 unverified 而非复用 info | 语义正交：info=提示，unverified=应检未检——是 overall=incomplete 的判定依据，混用会回到老问题 |
| D14-5 | T14.1 目标锁定采用插件本地锁（import_step/上轮 result 登记 + 失效回退标注），不扩服务端任务协议 | 锁的生命周期与 3D 视口文档绑定（含手动调整/快照恢复），服务端无从感知；本地锁零协议改动达成同一正确性目标，`target_fallback` 附加键回传如实标注 |
| D14-6 | T14.6 乐观条件用快照版本号（expectedSnapshotVersion）而非 uuid（expected_snapshot_id） | IrEnvelope 已暴露 version，前端零新增读侧接口；版本号 per (session, ir_type) 唯一，语义与 uuid 等价且可读（409 消息带 vN） |
| D14-7 | T14.7 依赖图不做 kb_formulas 表结构变更（V20 不落）：依赖边按 SYMBOL_ALIASES 会话内现算 | IR2 参数 id 每会话动态生成，静态 depends_on 登记无处落锚；别名映射与 A2 取数同序、随参数表版本重建，正确性等价且零迁移；kb_formulas 动态公式符号语义未登记——不建边不重算，如实跳过（A-16） |

## 7. 阶段 15 立账（承诺项，非本期范围）

- 建模时参数→对象映射架构（审查问题 3+7）：A3 结构化参数注入 prompt、模型输出参数→特征映射、按尺寸语义分检查器（孔径/壁厚/外形长度）、SEG 稳定标识与跨版本对位、容差按参数登记；
- 审阅/导出深化（问题 12）：统一检查表视图（设计值/实测值/偏差/容差/状态/依据）、版本间全参数 diff、导出含未决项章节与全链版本清单、冻结版本导出门；
- A3 prompt「允许改振幅」例外条款治理（前置发现 ③）。

## 7A. 验收记录（2026-10-08 收官）

| 项 | 结果 |
|---|---|
| py 单测 | **435 passed 1 skipped**（阶段前 379；新增 T14.1×7 / T14.2×12 / T14.3×19 / T14.4×5 / T14.5×4 / T14.7×6 / T14.8×9；既有语义更新 13 处逐个入 devlog） |
| Java | **86 tests BUILD SUCCESS**（auth 14 / workflow 42（+3 T14.6）/ biz 13 / notify 17） |
| 前端 | tsc 0 错误；vitest **43 passed** |
| e2e | **7/7 全绿（真实 LLM 逐文件）**：cad_tune 82s / multikb 235s（陈旧标题夹具修正后单独重跑）/ kb_pipeline 1173s / phase2_pipeline 1001s（T14.4 三态+四级断言真实管线通过）/ e1_forward 28s / e1_reverse 33s / towed 680s |
| 实机验证 | ①旧会话 IR4 v1（两态旧快照）新栈读出正常（pass/csr=100/coverage=None）——契约向后兼容容器级确认；②真实 siliconflow 供应商经管理 API 重新登记（引擎重启后库中唯一 enabled 行丢失，属环境态非代码）；③Docker Desktop 引擎卡死一次，重启后全栈 29 容器恢复、三镜像重建（java/py/frontend）装载 v0.7 代码 |
| 17 条案例 | 13 ✅ / 2 ◐ / 2 阶段 15（逐条见[核验报告 §3](../reviews/2026-10-02-外部审查核验.md)） |
| 环境事件（如实） | e2e 首轮 multikb 失败为**陈旧夹具撞 09-24 标题≤20 字权威校验**（21 字符），非本期代码回归——夹具修正后通过 |

## 8. 修订记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v1.0 | 2026-10-08 | 编制：外部审查核验后全量整改 9 组真实缺陷，T14.1–T14.9 |
| v1.1 | 2026-10-08 | 收官回填：§7A 验收记录（e2e 7/7、实机三项验证、环境事件如实）、执行期决策 D14-5/6/7、roadmap 全 ✅ |
