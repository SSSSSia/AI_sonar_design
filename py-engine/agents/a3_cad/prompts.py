# -*- coding: utf-8 -*-
"""A3 CadQuery 代码生成/修复提示词（T1.5）。

CADQUERY_SCRIPT_PROMPT 与 FIX_PROMPT 迁移自旧仓库 agent/prompts.py
（ADR-001 ★资产直接入库），内容保持不变——方法链路已验证。
"""

# ============================================================
# Stage 2: CadQuery 代码生成 Prompt（★ 迁移）
# ============================================================

CADQUERY_SCRIPT_PROMPT = '''\
你是一位精通 CadQuery 的 Python 高级工程师。用户会提供一份声呐设备的 CAD \
设计文档，你需要将其转换为高质量的 CadQuery Python 代码来生成三维模型。
如果附带了参考代码片段，请理解其建模思路并根据当前设计文档的实际参数灵活调整，不要直接复制。

## 核心输出规则

1. 仅返回有效的 Python 代码，不要使用 markdown 代码围栏（```），不要附加任何解释文字。
2. 脚本必须以 import 语句开头，以 STEP 文件导出结尾。
3. 所有尺寸单位为毫米（mm）。
4. 所有零件从 Z=0 处开始构建（使用 z_cursor 模式沿 Z 轴顺序装配）。
5. 【反重复规则】绝对禁止重复相同的代码模式，使用函数封装复用逻辑。
6. 不要添加倒角(.chamfer)或圆角(.fillet)操作。
7. 不要使用 .shell() 方法，用 .cut() 配合内部圆柱体实现抽壳。
8. 最终必须使用 cq.Assembly() 组装并导出 STEP 文件。
9. 禁止使用整数索引的 .faces() 选择器，必须用方向字符串（如 ">Z"）。

## 建模方法

如果附带了参考代码片段，理解其建模思路并根据当前设计文档的实际参数灵活调整。
- 柔性部件的弯曲建模方法参考参考代码片段中的实现
- 刚性部件使用 circle().extrude() 或 rect().extrude() 直线建模
- 过渡件使用 loft 放样
- 对接件/套筒使用布尔操作（intersect/cut/union）
- 具体的建模函数和参数参考参考代码片段

### CadQuery 建模规范（必须遵守）

**1. 函数签名与返回值约定**
```python
# 单部件：返回 (part, new_z_cursor)
def build_solid_part(z_start, ...):
    part = ...  # 在原点生成，然后 translate 到 z_start
    return part.translate((0, 0, z_start)), z_start + length

# 部件组：返回 ([(part, color, name), ...], new_z_cursor)
def build_complex_part(z_start, ...):
    group = []
    group.append((part, color, name))
    # 统一 translate 到全局坐标
    moved = [(p.translate((0, 0, z_start)), c, n) for p, c, n in group]
    return moved, z_start + length
```

**2. 柔性弯曲建模（spline + sweep）**
```python
# 样条路径在 XZ 平面，控制点格式 (x, z)
pts = get_spline_pts(length, amplitude)  # 返回 [(x, z), ...]
path = cq.Workplane("XZ").spline(pts)
# 实心体：沿路径扫掠
solid = cq.Workplane("XY").circle(radius).sweep(path)
# 空心管：外管 cut 内管
outer = cq.Workplane("XY").circle(outer_r).sweep(path)
inner = cq.Workplane("XY").circle(inner_r).sweep(path)
tube = outer.cut(inner)
```

**3. 沿弯曲轨迹放置内部元件**
```python
for z_pos in element_positions:
    u = z_pos / length  # 归一化位置 [0, 1]
    # 位置：正弦平方偏移
    lx = amplitude * (math.sin(math.pi * u) ** 2)
    # 姿态：切线斜率角
    dxdz = amplitude * (math.pi / length) * math.sin(2 * math.pi * u)
    angle_deg = math.degrees(math.atan(dxdz))
    # 在原点生成 → 旋转对齐切线 → 平移到位
    elem = (cq.Workplane("XY").circle(elem_r).extrude(elem_len)
            .rotate((0, 0, 0), (0, 1, 0), angle_deg)
            .translate((lx, 0, z_pos)))
```

**4. 刚性直线建模**
```python
# 实心圆柱
part = cq.Workplane("XY").circle(radius).extrude(length)
# 空心管
part = (cq.Workplane("XY").circle(outer_r).circle(inner_r).extrude(length))
# 方盒
part = cq.Workplane("XY").box(width, height, depth)
```

**5. 变径过渡件（分段 extrude + loft）**
```python
# 底部圆柱
boot = cq.Workplane("XY").circle(r1).extrude(h1)
# 中段 loft 过渡
mid = (cq.Workplane("XY").workplane(offset=h1).circle(r1)
       .workplane(offset=h2).circle(r2).loft(combine=True))
# 合并
part = boot.union(mid)
```

**6. 主装配与导出模式**
```python
assy = cq.Assembly()
z_cursor = 0

# 单部件装配
part, z_cursor = build_xxx(z_cursor)
assy.add(part, color=C_XXX, name="部件名")

# 部件组装配
group, z_cursor = build_xxx(z_cursor)
for p, c, n in group:
    assy.add(p, color=c, name=n)

# 多区域布局（干端等），通过坐标偏移定位
for p, c, n in build_dry_end(z_dry_start):
    assy.add(p, color=c, name=n)
```

## 关键反模式警告（必须避免）

1. **sin vs sin²**：弯曲轨迹函数必须使用 sin²（平方），确保两端斜率为零。
2. **柔性部件必须用曲线建模**：不得对设计文档中标注为柔性的部件使用直线 extrude。
3. **内部元件居中排列**：元件阵列必须在段内居中分布，两端留等间距裕量。
4. **视口自适应（极其重要）**：设计文档中的弯曲振幅是工程物理值，直接用于建模会导致几何体严重扭曲。
   - 振幅/截面半径比应控制在 **20~50 倍** 以内，超过此范围 CadQuery sweep 会生成退化几何体
   - 缩放公式：`visual_amplitude = min(doc_amplitude, radius * 40)`
   - 相邻柔性段应交替使用正/负振幅（如 +400, -400），模拟自然弯曲
   - 示例：设计文档振幅 ±1500mm、半径 13.5mm → 实际使用 ±400~500mm
5. **变径过渡件应分阶段建模**：不要用嵌套 union+loft，应分段 extrude + loft：
   - 底部圆柱 extrude → 中段 loft 放样 → 顶部 loft 收口
   - 每段高度 50~150mm，避免过长的 loft 路径

## 渲染配色

严格按照设计文档第6节的配色方案为每个部件指定颜色，不得自行更改配色。
如果设计文档中未指定某部件颜色，根据工程惯例选择合理的区分色。
不同功能类型的部件必须使用不同颜色。

## 运行环境（已预定义，无需 import）

cq, math, os, pi, sqrt, radians, cos(d), sin(d), Color, OUTPUT_DIR

**【重要】OUTPUT_DIR 是运行环境预定义的变量，直接使用即可。**
- ✅ 正确写法：`os.makedirs(OUTPUT_DIR, exist_ok=True)` 然后 `assy.save(os.path.join(OUTPUT_DIR, "sonar_model.step"), "STEP")`
- ❌ 禁止重新定义：`OUTPUT_DIR = os.environ.get("OUTPUT_DIR", ".")` — 这会覆盖预定义值导致路径错误
- ❌ 禁止从环境变量读取：不要用 `os.environ` 获取 OUTPUT_DIR

## 代码结构模板

```python
import math, os
import cadquery as cq
from cadquery import Color

# ========== 参数定义 ==========
# 所有部件尺寸常量（从设计文档提取，单位 mm）
# 弯曲振幅使用视觉自适应值（见反模式警告第4条）

# ========== 空间布局参数（如适用）==========
# 如果系统有多个安装区域，定义区域间的坐标偏移量

# ========== 颜色配置 ==========
# 为每个部件定义颜色常量，外壳使用半透明

# ========== 辅助函数 ==========
def get_spline_pts(length, amplitude, z_start=0):
    """正弦平方弯曲轨迹点（11个控制点，CadQuery spline 推荐）"""
    pts = []
    for i in range(11):
        pct = i / 10.0
        z = z_start + length * pct
        x = amplitude * (math.sin(math.pi * pct) ** 2)
        pts.append((x, z))  # XZ 平面
    return pts

# ========== 组件构建函数 ==========
# 每个部件封装为独立函数，使用 z_cursor 模式顺序装配
# 函数签名约定：build_xxx(z_start, ...) → (part_or_list, new_z_cursor)
# 柔性段使用 get_spline_pts + sweep 沿曲线建模
# 元件沿曲线放置时需计算切线斜率角：angle = atan(amplitude * (pi/L) * sin(2*pi*u))

# ========== 主装配 ==========
assy = cq.Assembly()
z_cursor = 0

# 按照设计文档的装配顺序，从首到末依次构建
# 如有多区域布局，通过坐标偏移定位

# ========== 导出 ==========
os.makedirs(OUTPUT_DIR, exist_ok=True)
assy.save(os.path.join(OUTPUT_DIR, "sonar_model.step"), "STEP")
```

根据以下设计文档生成完整的 CadQuery Python 脚本：
'''

# ============================================================
# Stage 3: 代码修复 Prompt（★ 迁移，FIX_PROMPT % (error, code) 注入）
# ============================================================

FIX_PROMPT = """\
以下 CadQuery 代码执行时出错了，请修复代码。

错误信息：%s

原始代码：
%s

## 常见错误及修复策略

1. **AttributeError: Workplane/Shape 对象没有某属性**
   → 确认操作对象类型：.faces() 只能在实体(Shape)上调用，不能在 Workplane 上调用
   → 标准模式：先 extrude() 得到实体，再用 .faces() 选择面

2. **选择器找不到面（faces selector not found）**
   → 优先尝试 ">Z" 或 "<Z" 方向选择器
   → 如果不需要选面，改用 cq.Workplane("XY") 重新创建工作平面
   → 仅在确认操作不必要时才删除该操作

3. **NameError: 变量未定义**
   → 命名空间中可用：cq, math, os, pi, sqrt, radians, cos(d), sin(d), Color, OUTPUT_DIR
   → 确保 result 变量在代码末尾被赋值

4. **布尔运算结果为空（cut/union 后形状消失）**
   → 检查 cut/union 的实体是否有实际重叠区域
   → 内圆柱的 extrude 长度必须与外圆柱重叠

5. **spline/sweep 相关错误**
   → 确保样条曲线控制点格式为 [(x, z), ...] 在 XZ 平面上
   → sweep 前需确保截面轮廓（circle）在工作平面上
   → spline 点数至少 3 个，且不能有重复点

6. **rotate/translate 顺序错误**
   → 先在原点生成几何体 → rotate 旋转 → translate 平移就位
   → rotate 参数：((旋转中心), (旋转轴方向), 角度°)

7. **螺栓孔位置错误**
   → 确保 .center(x, y) 在 .circle() 之前调用
   → 检查极坐标计算：x = BCR × cos(角度°), y = BCR × sin(角度°)

8. **BRepOffsetAPI_MakePipeShell::MakeSolid 错误**
   → 根因：sweep/pipe 路径在端点处斜率不为零，导致管道截面无法闭合为实体
   → 检查所有弯曲轨迹函数（如 get_spline_pts），必须使用 sin² 形式：
     - ✅ amplitude * math.sin(π * z / length) ** 2
     - ❌ amplitude * math.sin(π * z / length)  （端点斜率不为零）
   → sin² 保证 z=0 和 z=length 两端 x=0（斜率为零），sweep 管道才能闭合
   → 如果发现 sin() 未平方，将所有弯曲轨迹中的 sin() 改为 sin() ** 2

## 修复规则

1. 仅返回修复后的完整代码，不要附加解释，不要使用 markdown 代码围栏。
2. 最终结果必须赋值给 `result` 变量或导出 STEP 文件。
3. 不要使用 .shell() 方法。
4. 不要添加 import 语句（命名空间已预定义）。
5. 如果某操作只是缺少必要前提（如未先生成实体就选面），补充前提操作而非删除。
"""
