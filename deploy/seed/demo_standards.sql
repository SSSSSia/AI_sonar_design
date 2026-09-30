-- T9.3（PHASE-009）：军标库首批演示条目（6 条）。
-- A-16 数据来源纪律：真实 GJB 条目文本需授权，未到位前用自拟演示条目验证插拔机制闭环——
-- 编号一律 DEMO-STD-*（不冒充军标编号）、名称带【演示】、confidence=low；
-- 军标资料到位后由内部人员经知识库「军标库」页签（或本表）替换，机制不变。
-- 表达式符号约定：取 IR2 各参数 derivation 的 "符号=数值" 记录 + value（命中参数登记值）。
INSERT INTO kb_standards (code, name, applies_to, requirement, expr, enabled, confidence) VALUES
  ('DEMO-STD-001', '阵元总数上限【演示】', '阵元总数',
   '演示条目（自拟限值，非真实军标）：阵元总数不超过 128，约束阵体规模与成本',
   'n <= 128', true, 'low'),
  ('DEMO-STD-002', '阵元间距下限【演示】', '阵元间距',
   '演示条目（自拟限值，非真实军标）：阵元间距不小于 0.5 m，抑制栅瓣',
   'element_spacing >= 0.5', true, 'low'),
  ('DEMO-STD-003', '声呐罩最大外径【演示】', '外径',
   '演示条目（自拟限值，非真实军标）：耐压壳体外径不大于 325 mm',
   'outer_diameter <= 325', true, 'low'),
  ('DEMO-STD-004', '工作频率范围【演示】', '工作频率',
   '演示条目（自拟限值，非真实军标）：中心频率位于 150–1500 Hz',
   '(f0 >= 150) & (f0 <= 1500)', true, 'low'),
  ('DEMO-STD-005', '接收灵敏度下限【演示】', '灵敏度',
   '演示条目（自拟限值，非真实军标）：接收灵敏度不低于 -201 dB（value 绑定命中参数登记值）',
   'value >= -201', true, 'low'),
  ('DEMO-STD-006', '拖曳长度上限【演示】', '拖曳',
   '演示条目（自拟限值，非真实军标）：拖曳长度不超过 300 m',
   'tow_length <= 300', true, 'low')
ON CONFLICT (code) DO NOTHING;
