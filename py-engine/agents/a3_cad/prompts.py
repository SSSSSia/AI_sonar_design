# -*- coding: utf-8 -*-
"""A3 CadQuery 代码生成/修复提示词（T1.5 迁移；T16.0 A0-5 通用/领域边界拆分）。

CADQUERY_BASE_PROMPT / FIX_BASE_PROMPT 迁移自旧仓库 agent/prompts.py（ADR-001 ★资产
直接入库）。A0-5（PHASE-016，DEV-001 §1.2）：原单块模板中的拖曳阵领域条款（sin²、
柔性曲线、振幅 20~50 倍视口自适应、干端布局、spline 模板）收敛入 DOMAIN_PROMPTS /
DOMAIN_FIX_PROMPTS / DOMAIN_REF_PATTERNS——**仅当参考匹配声明对应领域时注入**；
其他类型不误用同一组强制建模模式，未支持类型如实只有通用段。

T15.1（PHASE-015，ADR-014 §2/§3）：BASE 增「对象声明注释」通用节（@obj/@display
语法，任何领域适用）；拖曳领域振幅条款改为**物理/显示分离**——视觉缩放必须
@display 显性声明落 IR3 display_transforms，禁止静默缩小工程振幅（A-16）。

T16.0 A0-3：提示词文本参与 codegen 缓存指纹——任何模板文本变更必须递增 PROMPT_VERSION。
"""

# 有效提示词版本（A0-3 缓存指纹组件；v1=A0-5 拆分前单块模板，v2=通用/领域拆分，
# v3=T15.1 对象声明注释节+振幅条款物理/显示分离，v4=声明 params 须引用参数表 id 原文）
PROMPT_VERSION = "4"

# ============================================================
# 通用层：任何 CadQuery 生成任务都适用（领域无关）
# ============================================================

CADQUERY_BASE_PROMPT = '''\
你是一位精通 CadQuery 的 Python 高级工程师。用户会提供一份设备的 CAD \
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

## 对象声明注释（必须遵守，T15.1 起进 IR3 契约）

在脚本中用注释声明对象语义身份与显示变换，供审阅链自动提取（ADR-014；注释不影响执行）：

1. **对象声明**：在每个部件（或元件循环）构建处上方添加一行注释：
   `# @obj <object_id> role=<角色> params=<参数id:尺寸类型,...>`
   - object_id：稳定语义标识（与装配 name 对应，如 lead_cable；同一对象跨版本不变）
   - role：对象语义角色（如 lead_cable / array_section / array_element / tail_flex）
   - params：该对象受哪些设计参数约束及尺寸语义（冒号分隔，多项逗号分隔），
     尺寸类型 ∈ length|diameter|width|height|thickness|other；不受参数约束可省略 params
   - params 中的参数 id 必须使用「结构化参数表」中的 id 原文（逐字符一致）；
     无法确定对应参数时省略 params，仅声明 role
   - 循环构建的同角色元件用区间语法：`# @obj elem-[01-16] role=array_element params=p_elem_diameter:diameter`
   - 示例：`# @obj array_section role=array_section params=p_array_outer_diameter:diameter,p_array_length:length`

2. **显示变换声明**（仅当某工程物理值不能直接用于建模时，必须声明、禁止静默缩放）：
   `# @display param=<参数id或none> physical_mm=<物理值mm> visual_mm=<视觉值mm> rule=<缩放规则>`
   - physical_mm 必须取设计文档/参数表原值，不得篡改；参数表存在对应参数时必须填 param
   - 键值内不得含空白（rule 用紧凑表达式，如 min(doc_amplitude,radius*40)）
   - 示例：`# @display param=p_bend_amplitude physical_mm=1500 visual_mm=450 rule=min(doc_amplitude,radius*40)`

## 建模方法

如果附带了参考代码片段，理解其建模思路并根据当前设计文档的实际参数灵活调整。
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

**2. 刚性直线建模**
```python
# 实心圆柱
part = cq.Workplane("XY").circle(radius).extrude(length)
# 空心管
part = (cq.Workplane("XY").circle(outer_r).circle(inner_r).extrude(length))
# 方盒
part = cq.Workplane("XY").box(width, height, depth)
```

**3. 变径过渡件（分段 extrude + loft）**
```python
# 底部圆柱
boot = cq.Workplane("XY").circle(r1).extrude(h1)
# 中段 loft 过渡
mid = (cq.Workplane("XY").workplane(offset=h1).circle(r1)
       .workplane(offset=h2).circle(r2).loft(combine=True))
# 合并
part = boot.union(mid)
```

**4. 主装配与导出模式**
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

# 多区域布局（如适用），通过坐标偏移定位
for p, c, n in build_region(z_region_start):
    assy.add(p, color=c, name=n)
```

## 关键反模式警告（必须避免）

1. **变径过渡件应分阶段建模**：不要用嵌套 union+loft，应分段 extrude + loft：
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

# ========== 空间布局参数（如适用）==========
# 如果系统有多个安装区域，定义区域间的坐标偏移量

# ========== 颜色配置 ==========
# 为每个部件定义颜色常量，外壳使用半透明

# ========== 辅助函数 ==========
# 按需封装复用逻辑，避免重复代码模式

# ========== 组件构建函数 ==========
# 每个部件封装为独立函数，使用 z_cursor 模式顺序装配
# 函数签名约定：build_xxx(z_start, ...) → (part_or_list, new_z_cursor)

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
# 领域层：仅当参考匹配声明对应领域时拼接（A0-5 边界）
# ============================================================

DOMAIN_PROMPTS = {
    "towed_array": '''\

## 领域规范：拖曳线列阵声呐（随匹配范例注入，其他类型不适用）

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

## 领域反模式警告（必须避免）

1. **sin vs sin²**：弯曲轨迹函数必须使用 sin²（平方），确保两端斜率为零。
2. **柔性部件必须用曲线建模**：不得对设计文档中标注为柔性的部件使用直线 extrude。
3. **内部元件居中排列**：元件阵列必须在段内居中分布，两端留等间距裕量。
4. **振幅物理/显示分离（极其重要）**：设计文档中的弯曲振幅是工程物理值，直接建模会产生退化几何；
   允许按视觉规则缩放，但**必须显性声明，禁止静默缩小工程参数**（A-16 诚实原则）：
   - 振幅/截面半径比应控制在 **20~50 倍** 以内，超过此范围 CadQuery sweep 会生成退化几何体
   - 缩放公式：`visual_amplitude = min(doc_amplitude, radius * 40)`
   - 相邻柔性段应交替使用正/负振幅（如 +400, -400），模拟自然弯曲
   - **每次使用视觉振幅处必须添加显示变换声明注释**（见「对象声明注释」第 2 条）：
     `# @display param=<振幅参数id或none> physical_mm=<设计文档原值> visual_mm=<实际使用值> rule=min(doc_amplitude,radius*40)`
     - 示例：设计文档振幅 ±1500mm、半径 13.5mm → visual_mm=450，声明 physical_mm=1500
   - 未声明而缩小振幅视为静默篡改工程参数；审阅链（A4 CHK-DISPLAY-TRANSFORM）会呈现全部声明

## 领域代码结构模板补充

```python
# ========== 参数定义（领域补充）==========
# 弯曲振幅使用视觉自适应值，且必须逐处 @display 声明（见领域反模式警告第4条）

# ========== 辅助函数（柔性段）==========
def get_spline_pts(length, amplitude, z_start=0):
    """正弦平方弯曲轨迹点（11个控制点，CadQuery spline 推荐）"""
    pts = []
    for i in range(11):
        pct = i / 10.0
        z = z_start + length * pct
        x = amplitude * (math.sin(math.pi * pct) ** 2)
        pts.append((x, z))  # XZ 平面
    return pts

# 柔性段使用 get_spline_pts + sweep 沿曲线建模
# 元件沿曲线放置时需计算切线斜率角：angle = atan(amplitude * (pi/L) * sin(2*pi*u))
```
''',
}

# 参考代码注入的领域专属"必须复制"模式（原 _generate 硬编码 7 条收敛于此；A0-5）
DOMAIN_REF_PATTERNS = {
    "towed_array": (
        "【必须复制的建模模式】（拖曳线列阵声呐 CadQuery API 调用方式）：\n"
        "- 柔性段：get_spline_pts() -> spline(pts) -> circle().sweep(path)\n"
        "- 空心管：outer_sweep.cut(inner_sweep)\n"
        "- 沿曲线放置元件：sin² 偏移 + atan(dxdz) 切线角 + rotate + translate\n"
        "- 对接件：sweep + intersect 截取两端\n"
        "- 变径件：分段 extrude + loft(combine=True)\n"
        "- 装配：z_cursor 递增 + cq.Assembly() + assy.add()\n"
        "- 坐标偏移：多区域布局通过 translate 实现空间隔离\n"
        "- 对象声明注释：@obj/@display 按通用规范「对象声明注释」节语法添加"
        "（振幅缩放必须 @display 声明，禁止静默缩小）\n\n"
    ),
}


def build_system_prompt(domain: str | None = None) -> str:
    """A0-5：通用规范 +（仅当领域已注册时）领域段。未支持领域如实只有通用段，
    不误用其他领域的强制建模模式。"""
    if domain and domain in DOMAIN_PROMPTS:
        return CADQUERY_BASE_PROMPT + DOMAIN_PROMPTS[domain]
    return CADQUERY_BASE_PROMPT


# ============================================================
# 代码修复 Prompt（★ 迁移，% (error, code) 注入）
# ============================================================

FIX_BASE_PROMPT = """\
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

## 修复规则

1. 仅返回修复后的完整代码，不要附加解释，不要使用 markdown 代码围栏。
2. 最终结果必须赋值给 `result` 变量或导出 STEP 文件。
3. 不要使用 .shell() 方法。
4. 不要添加 import 语句（命名空间已预定义）。
5. 如果某操作只是缺少必要前提（如未先生成实体就选面），补充前提操作而非删除。
"""

DOMAIN_FIX_PROMPTS = {
    "towed_array": """
8. **BRepOffsetAPI_MakePipeShell::MakeSolid 错误**
   → 根因：sweep/pipe 路径在端点处斜率不为零，导致管道截面无法闭合为实体
   → 检查所有弯曲轨迹函数（如 get_spline_pts），必须使用 sin² 形式：
     - ✅ amplitude * math.sin(π * z / length) ** 2
     - ❌ amplitude * math.sin(π * z / length)  （端点斜率不为零）
   → sin² 保证 z=0 和 z=length 两端 x=0（斜率为零），sweep 管道才能闭合
   → 如果发现 sin() 未平方，将所有弯曲轨迹中的 sin() 改为 sin() ** 2
""",
}


def build_fix_prompt(domain: str | None = None) -> str:
    """A0-5：通用修复策略 +（仅当领域已注册时）领域修复策略（sin² 条款属拖曳阵领域）。"""
    if domain and domain in DOMAIN_FIX_PROMPTS:
        return FIX_BASE_PROMPT.replace(
            "## 修复规则",
            DOMAIN_FIX_PROMPTS[domain] + "\n## 修复规则",
        )
    return FIX_BASE_PROMPT
