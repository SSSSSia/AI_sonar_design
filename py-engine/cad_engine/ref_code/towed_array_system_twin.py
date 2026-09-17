# -*- coding: utf-8 -*-
"""
========================================================================================
项目名称：拖曳阵声呐分布式母船数字孪生总装三维模型生成器
开发框架：CadQuery 2.4+ (基于 OpenCASCADE 几何内核的面向对象参数化 CAD 编程框架)
架构模式：分布式空间解耦拓扑 (Distributed Spatial Decoupling Topology)
========================================================================================
【核心要点提示】：
1. 空间解耦（Spatial Decoupling）：通过引入大跨度横移(X轴)与跨甲板跃层(Y轴)参数，在无边界三维空间中
   划定出"露天湿端机械区"与"室内干端显控区"两个独立环境，用"留白"艺术模拟巨型船体。
2. 几何形变与姿态对齐（Deformation & Alignment）：利用正弦平方样条曲线模拟线缆水下柔性形态。
   通过计算曲线在特定点的一阶导数（dxdz），求出切线斜率角，动态调整水听器阵元的旋转姿态。
3. 视场自适应（Viewport Adaptation）：引入 VISUAL_ONLY 参数化缩尺开关，解决长宽比达 3000:1 的
   超细长实体与常规尺度机柜在同一 CAD 视场（Viewport）下的渲染级视觉冲突。
========================================================================================
"""

import math
import os
import cadquery as cq
from cadquery import Color

# 确保输出目录存在，避免文件写操作引发 I/O 异常
os.makedirs("output", exist_ok=True)


# ======================================================================================
# 🛠️ 第一部分：系统级精密工程参数（1:1 工程物理模式，单位: mm）
# ======================================================================================

# --- 声阵基元参数 ---
ELEMENTS_PER_SECTION = 16         # 标准 16 基元阵列
ELEMENT_SPACING = 1500            # 严格的 1.5 米阵元半波长布阵间距

# --- 各段长度参数（单位：mm）---
TAIL_ROPE_LENGTH = 10000          # 10 米尼龙尾绳
ACOUSTIC_SECTION_LENGTH = 24000   # 24 米超长声阵段
DAMPING_SECTION_LENGTH = 10000    # 10 米前置高弹减振段
TRANSMISSION_CABLE_LENGTH = 10000 # 10 米铠装光电复合传输缆
WATERTIGHT_CONNECTOR_LENGTH = 1500# 1.5 米变径水密航空插头

# --- 各段截面半径参数（单位：mm）---
AS_outer_radius = 13.5            # 严格的标准 Ø27mm 外径
AS_inner_radius = 10.5            # 内径 21mm，预留内部走线空间

# --- 内部元器件参数（单位：mm）---
SENSOR_RADIUS = 8.5               # 姿态/压力传感器外径半径
SENSOR_LENGTH = 120               # 姿态传感器长度

# --- 派生几何参数（基于基准半径非线性推导，确保缩放时整体造型不穿模） ---
DAMPING_outer_radius = AS_outer_radius - 0.5         # 减振段略细于声阵段
TRANSITION_MAX_RADIUS = AS_outer_radius * 1.4         # 汇流头最大过渡外径
CABLE_radius = AS_outer_radius * 0.6                  # 传输缆半径（保持纤细）
TAIL_ROPE_RADIUS = AS_outer_radius * 0.2              # 尾绳半径
HYDROPHONE_RADIUS = AS_inner_radius - 3.0             # 水听器外径（必须小于护套内径，确保容纳）
HYDROPHONE_LENGTH = 80                                # 水听器圆柱体长度


# ======================================================================================
# 🎨 第二部分：数字化孪生空间设色标准 (RGBA 物理模型映射)
# ======================================================================================
# 【提示】：通过给不同的工业部件赋予具备明确隐喻的色彩，可以大幅提升模型拓扑的可读性。
C_TAIL_ROPE      = Color(0.65, 0.65, 0.65)            # 灰色：末端无源编织尾绳
C_ACOUSTIC_SKIN  = Color(0.15, 0.50, 0.75, 0.35)      # 亮蓝半透明：透声聚氨酯外护套（全景下可见内部结构）
C_HYDROPHONE     = Color(0.90, 0.45, 0.00)            # 橙色高亮：有源水声换能器阵元（核心研究对象）
C_SENSOR         = Color(0.50, 0.00, 0.80, 1.0)       # 紫色：姿态/压力传感器
C_COUPLING       = Color(0.25, 0.28, 0.30, 1.0)       # 深灰色：钛合金对接套筒
C_DAMPING        = Color(0.20, 0.50, 0.30)            # 墨绿：内部充注高粘度硅油的减振柔性体
C_TRANSITION     = Color(0.20, 0.20, 0.20)            # 深黑：钛合金刚性承载汇流头/驱动电机
C_CABLE          = Color(0.80, 0.15, 0.15)            # 红色：高压铠装光电复合传输缆
C_CONNECTOR      = Color(0.85, 0.70, 0.10)            # 铜黄色：黄铜耐压水密航插接头

# --- 分布式母船场景环境色 ---
C_STERN_DECK     = Color(0.20, 0.22, 0.24)            # 低反射深灰：战舰后甲板露天防滑涂层
C_CABIN_FLOOR    = Color(0.50, 0.58, 0.52)            # 浅绿灰：室内舱室防静电活动地板
C_WINCH          = Color(0.30, 0.33, 0.35)            # 机械中灰：重型海工收放机械
C_CABINET_BODY   = Color(0.22, 0.28, 0.24)            # 军工绿灰：电子标准控制机柜标准色
C_SERVER_CHASSIS = Color(0.12, 0.14, 0.12)            # 哑光黑：服务器机箱铝合金面板
C_CONSOLE_DESK   = Color(0.18, 0.20, 0.22)            # 碳素灰：声呐操作台人体工程学台面
C_SCREEN_FRAME   = Color(0.05, 0.05, 0.05)            # 纯黑：抗冲击加固显示器外框
C_SCREEN_GLOW    = Color(0.00, 0.70, 0.90)            # 青蓝色自发光：数字瀑布谱阵战术视窗
C_CONDUIT        = Color(0.75, 0.55, 0.10)            # 亮黄：主干线缆走线管廊（作为分布式拓扑的物理连接纽带）


# ======================================================================================
# 📐 第三部分：数学形态发生器（样条曲线离散化计算）
# ======================================================================================
def get_spline_pts(length, amplitude, z_start):
    """
    【数学算子】：生成符合真实水流拖拽形变的"正弦平方"三维空间样条线控制点集。
    原理：y = amplitude * sin^2(pi * i / 10)，形成平滑平流过渡，避免端点处曲率突变（阶跃）。
    """
    pts = []
    for i in range(11):
        percentage = i / 10.0
        # 计算当前分段点在全长中的纵向 Z 轴坐标位置
        z_curr = z_start + length * percentage
        # 使用正弦平方函数计算 X 轴的非线性横向位移量（横摆波浪造型）
        x_curr = amplitude * (math.sin(math.pi * percentage) ** 2)
        pts.append((x_curr, z_curr))  # 返回 XZ 平面上的二维坐标点元组
    return pts


# ======================================================================================
# 🌊 第四部分：水下湿端柔性线阵列组件参数化建模
# ======================================================================================
def build_tail_rope(z_start, amplitude=150):
    """ 生成柔性尾绳：采用 sweep（扫掠）算子将圆形截面沿 XZ 样条曲线路径拉伸 """
    pts = get_spline_pts(TAIL_ROPE_LENGTH, amplitude, 0)
    path = cq.Workplane("XZ").spline(pts)  # 创建 XZ 平面控制路径
    # 在 XY 平面画圆，并沿路径扫掠，随后平移至全局坐标系的 z_start 起始点
    rope = cq.Workplane("XY").circle(TAIL_ROPE_RADIUS).sweep(path)
    return rope.translate((0, 0, z_start)), z_start + TAIL_ROPE_LENGTH

def build_acoustic_segment(z_start, name, amplitude=400):
    """
    生成空心透声护套，精密嵌入离散阵列化水听器、姿态传感器与钛合金对接套筒。
    【重点】：姿态矩阵计算逻辑 —— 通过微分求导获取切线斜率，动态旋转组件对齐弯曲轴线。
    """
    segment_group = []
    pts = get_spline_pts(ACOUSTIC_SECTION_LENGTH, amplitude, 0)
    path = cq.Workplane("XZ").spline(pts)

    # 核心算子组合：通过 cut（布尔求减）生成内外径分明的空心管状护套
    skin = (cq.Workplane("XY").circle(AS_outer_radius).sweep(path)
            .cut(cq.Workplane("XY").circle(AS_inner_radius).sweep(path)))
    segment_group.append((skin, C_ACOUSTIC_SKIN, f"{name}_外护套"))

    # 对接件：沿相同 path 扫掠加厚套管，用布尔交集截取两端（精准定位段间连接套筒）
    thick_sleeve_raw = cq.Workplane("XY").circle(AS_outer_radius + 0.05).sweep(path)
    inner_sleeve_void = cq.Workplane("XY").circle(AS_inner_radius - 0.2).sweep(path)
    full_length_sleeve = thick_sleeve_raw.cut(inner_sleeve_void)

    cutter_boxes = (cq.Workplane("XY").workplane(offset=-50).box(5000, 5000, 250, centered=(True, True, False))
                    .union(cq.Workplane("XY").workplane(offset=ACOUSTIC_SECTION_LENGTH - 200).box(5000, 5000, 250, centered=(True, True, False))))
    couplings = full_length_sleeve.intersect(cutter_boxes)
    segment_group.append((couplings, C_COUPLING, f"{name}_流线对接件组"))

    # 计算水听器均匀布设的边界裕量
    start_margin = (ACOUSTIC_SECTION_LENGTH - (ELEMENTS_PER_SECTION - 1) * ELEMENT_SPACING) / 2

    # 顺应弯曲嵌套水听器基元
    for j in range(ELEMENTS_PER_SECTION):
        z_pos = start_margin + j * ELEMENT_SPACING
        u = z_pos / ACOUSTIC_SECTION_LENGTH  # 归一化纵向位置参数

        # 1. 位置方程：计算该水听器中心点的 X 轴偏移量
        lx = amplitude * (math.sin(math.pi * u) ** 2)

        # 2. 姿态方程（微分求导）：对正弦平方位置函数求 Z 的一阶导数，获得切线斜率
        dxdz = amplitude * (math.pi / ACOUSTIC_SECTION_LENGTH) * math.sin(2 * math.pi * u)
        # 通过反正切函数（atan）将斜率转化为角度制，用于三维旋转对齐
        angle_deg = math.degrees(math.atan(dxdz))

        # 3. 建模与空间变换：生成基础圆柱体 → 绕Y轴旋转至切线方向 → 平移至路径中心点
        hydrophone = (cq.Workplane("XY").circle(HYDROPHONE_RADIUS).extrude(HYDROPHONE_LENGTH)
                      .rotate((0, 0, 0), (0, 1, 0), angle_deg)
                      .translate((lx, 0, z_pos)))
        segment_group.append((hydrophone, C_HYDROPHONE, f"{name}_水听器_#{j+1}"))

    # 顺应弯曲嵌套 2 支姿态压力传感器（位于声阵段前后裕量区）
    sensor_positions = [start_margin / 2, ACOUSTIC_SECTION_LENGTH - (start_margin / 2) - SENSOR_LENGTH]
    for idx, z_pos in enumerate(sensor_positions):
        u = z_pos / ACOUSTIC_SECTION_LENGTH
        lx = amplitude * (math.sin(math.pi * u) ** 2)
        dxdz = amplitude * (math.pi / ACOUSTIC_SECTION_LENGTH) * math.sin(2 * math.pi * u)
        angle_deg = math.degrees(math.atan(dxdz))

        sensor = (cq.Workplane("XY").circle(SENSOR_RADIUS).extrude(SENSOR_LENGTH)
                  .rotate((0, 0, 0), (0, 1, 0), angle_deg)
                  .translate((lx, 0, z_pos)))
        segment_group.append((sensor, C_SENSOR, f"{name}_姿态传感器_#{idx+1}"))

    # 全局坐标系统一转换
    moved_group = [(part.translate((0, 0, z_start)), color, p_name) for part, color, p_name in segment_group]
    return moved_group, z_start + ACOUSTIC_SECTION_LENGTH

def build_damping_section(z_start, amplitude=200):
    """ 生成前减振段：实心高弹性聚氨酯结构，衰减母船下水缆传导的轴向高频振动噪声 """
    pts = get_spline_pts(DAMPING_SECTION_LENGTH, amplitude, 0)
    path = cq.Workplane("XZ").spline(pts)
    damping = cq.Workplane("XY").circle(DAMPING_outer_radius).sweep(path)
    return damping.translate((0, 0, z_start)), z_start + DAMPING_SECTION_LENGTH

def build_transition_and_cable(z_start, amplitude=-200):
    """
    生成钛合金刚性汇流头（刚柔过渡区）与铠装传输缆。
    【提示】：这里演示了 loft（放样）算子在异径圆柱体平滑过渡中的应用。
    """
    group = []
    # 通过多次向上构建工作平面(workplane)并画圆，最后使用 loft 串联，生成流线型锥度变径汇流头
    boot = (cq.Workplane("XY").circle(DAMPING_outer_radius).extrude(50)
            .union(cq.Workplane("XY").workplane(offset=50).circle(DAMPING_outer_radius)
                   .workplane(offset=100).circle(TRANSITION_MAX_RADIUS).loft(combine=True))
            .union(cq.Workplane("XY").workplane(offset=150).circle(TRANSITION_MAX_RADIUS)
                   .workplane(offset=100).circle(CABLE_radius).loft(combine=True)))
    group.append((boot.translate((0, 0, z_start)), C_TRANSITION, "1_汇流头"))

    # 建立长程铠装光电复合传输缆（将高压供电与高速光信号整合，输送至绞车）
    z_cable_start = z_start + 250
    pts = get_spline_pts(TRANSMISSION_CABLE_LENGTH - 250, amplitude, 0)
    path = cq.Workplane("XZ").spline(pts)
    cable = cq.Workplane("XY").circle(CABLE_radius).sweep(path)
    group.append((cable.translate((0, 0, z_cable_start)), C_CABLE, "1_传输缆"))
    return group, z_start + TRANSMISSION_CABLE_LENGTH

def build_watertight_connector(z_start):
    """ 生成多针变径高耐压水密航空插头：利用 faces(">Z").workplane() 在实体顶面建立局部工作面 """
    connector = (cq.Workplane("XY").circle(TRANSITION_MAX_RADIUS).extrude(100)
                 .faces(">Z").workplane().circle(AS_outer_radius).extrude(WATERTIGHT_CONNECTOR_LENGTH - 100))
    return connector.translate((0, 0, z_start)), z_start + WATERTIGHT_CONNECTOR_LENGTH


# ======================================================================================
# 🚢 第五部分：战舰分布式两舷大跨度、跨甲板干端场景建模
# ======================================================================================
# 【核心总体布置参数（General Arrangement Parameters）】
# 设定横向空间大跨度（15米），视觉化隔离湿端和干端；设定纵向跃层高度（2.5米），模拟主甲板与上层显控舱的落差。
REAL_SHIP_SPAN = -15000
REAL_DECK_LEAP = 2500

def build_stern_deck(z_start):
    """ 空间1：船尾露天作业甲板物理基座（用于承载巨型机械绞车） """
    deck = cq.Workplane("XY").box(2000, 40, 2200).translate((0, -370, z_start + 1100))
    return deck

def build_machinery_winch(z_start):
    """ 空间1：重型液压收放机组（绞车）建模 —— 包含主基座、A字型刚性承重支架与缠绕滚筒 """
    winch_group = []
    # 1. 钢结构基础底座
    w_base = cq.Workplane("XY").box(1200, 40, 1200).translate((0, -330, z_start + 650))
    winch_group.append((w_base, C_WINCH, "1_船尾甲板_绞车群基座"))

    # 2. 左右对称式承重双支架（利用布尔 union 联合成单一工件）
    supp = (cq.Workplane("XY").box(60, 550, 450).translate((-500, -100, z_start + 650))
            .union(cq.Workplane("XY").box(60, 550, 450).translate((500, -100, z_start + 650))))
    winch_group.append((supp, C_WINCH, "1_船尾甲板_绞车主支架"))

    # 3. 大容量储缆主滚筒（通过外侧法兰盘圆盘 union 限制电缆轴向窜动）
    drum = cq.Workplane("YZ").circle(220).extrude(900).translate((-450, 220, z_start + 650))
    flange = (cq.Workplane("YZ").circle(400).extrude(50).translate((-500, 220, z_start + 650))
              .union(cq.Workplane("YZ").circle(400).extrude(50).translate((450, 220, z_start + 650))))
    winch_group.append((drum.union(flange), C_WINCH, "1_船尾甲板_电缆主滚筒"))

    # 4. 甲板垂直导入电缆段
    bridge_cable = cq.Workplane("XY").circle(CABLE_radius).extrude(650).translate((0, 0, z_start))
    winch_group.append((bridge_cable, C_CABLE, "1_拖线阵列_入水引导段"))
    return winch_group


def build_operation_cabin_floor(z_start):
    """ 空间2：高层战术指挥舱室内防静电地板基座 —— 整体平移至 15 米外，升起 2.5 米 """
    floor = cq.Workplane("XY").box(3000, 30, 2400).translate((REAL_SHIP_SPAN, -365 + REAL_DECK_LEAP, z_start + 1300))
    return floor

def build_indoor_cabinet(z_start):
    """ 空间2：前置波束形成与信号高动态解调集群机柜（19英寸标准军工加固柜） """
    cab_group = []
    # 机柜金属主壳体
    main_body = cq.Workplane("XY").box(700, 1500, 700).translate((REAL_SHIP_SPAN + 800, -350 + 750 + REAL_DECK_LEAP, z_start + 1600))
    cab_group.append((main_body, C_CABINET_BODY, "2_上层显控舱_前置波束形成柜体"))

    # 顶罩强迫风冷排风单元
    top_cap = cq.Workplane("XY").box(650, 60, 650).translate((REAL_SHIP_SPAN + 800, -350 + 1530 + REAL_DECK_LEAP, z_start + 1600))
    cab_group.append((top_cap, C_TRANSITION, "2_上层显控舱_独立风冷散热顶罩"))

    # 密集抽屉式多通道数据采集机插卡面板
    panel = cq.Workplane("XY").box(600, 1200, 20).translate((REAL_SHIP_SPAN + 800, -350 + 700 + REAL_DECK_LEAP, z_start + 1600 - 345))
    cab_group.append((panel, C_SERVER_CHASSIS, "2_上层显控舱_多通道高动态采集插卡"))
    return cab_group

def build_indoor_console(z_start):
    """ 空间2：声呐兵战术操作显控一体化台架（双联主控工作站） """
    con_group = []
    # 人体工程学双人操作主键盘台面
    sub_rack = cq.Workplane("XY").box(1400, 700, 700).translate((REAL_SHIP_SPAN - 500, -350 + 350 + REAL_DECK_LEAP, z_start + 1100))
    table_top = cq.Workplane("XY").box(1500, 50, 800).translate((REAL_SHIP_SPAN - 500, -350 + 725 + REAL_DECK_LEAP, z_start + 1050))
    con_group.append((sub_rack.union(table_top), C_CONSOLE_DESK, "3_上层显控舱_双联综合操控台基座"))

    # 倾斜 22 度角安装的液晶抗冲击强固双显示器外框
    f_left = cq.Workplane("XY").box(550, 360, 40).rotate((0,0,0), (1,0,0), 22).translate((REAL_SHIP_SPAN - 800, -350 + 980 + REAL_DECK_LEAP, z_start + 1250))
    f_right = cq.Workplane("XY").box(550, 360, 40).rotate((0,0,0), (1,0,0), 22).translate((REAL_SHIP_SPAN - 200, -350 + 980 + REAL_DECK_LEAP, z_start + 1250))
    con_group.append((f_left.union(f_right), C_SCREEN_FRAME, "3_上层显控舱_抗冲击强固全幅液晶框"))

    # 数字自发光时频空间级联瀑布谱（模拟多目标声学跟踪战术屏幕面）
    g_left = cq.Workplane("XY").box(520, 330, 5).rotate((0,0,0), (1,0,0), 22).translate((REAL_SHIP_SPAN - 800, -350 + 980 + REAL_DECK_LEAP, z_start + 1271))
    g_right = cq.Workplane("XY").box(520, 330, 5).rotate((0,0,0), (1,0,0), 22).translate((REAL_SHIP_SPAN - 200, -350 + 980 + REAL_DECK_LEAP, z_start + 1271))
    con_group.append((g_left.union(g_right), C_SCREEN_GLOW, "3_上层显控舱_三维空间多目标跟踪谱阵"))
    return con_group

def build_shipwide_cable_conduit(z_start):
    """
    【架构物理纽带】：构建横跨 15 米全船复杂水密隔舱的立体"三段式封闭走线管廊"。
    通过将三段直角折交的箱体进行 union（布尔并集），打通全分布式系统的信号拓扑血脉。
    """
    # 路径1：从船尾绞车垂直下潜入下甲板电缆层
    drop_run = cq.Workplane("XY").box(60, 500, 60).translate((0, -350 - 250, z_start + 650))
    # 路径2：沿着船体主肋骨横向水平奔袭 15 米大跨度
    long_run = cq.Workplane("XY").box(abs(REAL_SHIP_SPAN) - 800, 60, 60).translate((REAL_SHIP_SPAN/2 + 400, -350 - 500, z_start + 650))
    # 路径3：在前部主显控舱下方垂直向上穿透舱壁，将信号直接引入前置预处理机柜
    vertical_trunk = cq.Workplane("XY").box(60, REAL_DECK_LEAP + 600, 60).translate((REAL_SHIP_SPAN + 800, -350 - 500 + (REAL_DECK_LEAP + 600)/2, z_start + 650))

    return drop_run.union(long_run).union(vertical_trunk)


# ======================================================================================
# 🚀 第六部分：全局主装配总成与高精度 STEP 实体导出
# ======================================================================================
print("="*80)
print(f"开始编译...")
print(f"当前模式: 【1:1工程物理模式 (全长近80米)】")
print("="*80)

# 实例化高级装配体树状结构（Assembly 对象是多组件设色导出的工业通用容器）
assy = cq.Assembly()
z_cursor = 0  # 沿主轴递增的纵向定位游标，保证各个柔性段顺次咬合连接

# --------- 1. 顺次构建水下柔性线阵列系统 (湿端) ---------
part, z_cursor = build_tail_rope(z_cursor)
assy.add(part, name="1_湿端_尼龙尾绳", color=C_TAIL_ROPE)

parts_group, z_cursor = build_acoustic_segment(z_cursor, "1_湿端_声阵段2")
for part, color, p_name in parts_group:
    assy.add(part, name=p_name, color=color)

parts_group, z_cursor = build_acoustic_segment(z_cursor, "1_湿端_声阵段1", amplitude=-400) # 反向正弦摆动
for part, color, p_name in parts_group:
    assy.add(part, name=p_name, color=color)

part, z_cursor = build_damping_section(z_cursor)
assy.add(part, name="1_湿端_前减振段", color=C_DAMPING)

parts_group, z_cursor = build_transition_and_cable(z_cursor)
for part, color, p_name in parts_group:
    assy.add(part, name=p_name, color=color)

part, z_cursor = build_watertight_connector(z_cursor)
assy.add(part, name="1_湿端_变径航插接头", color=C_CONNECTOR)


# --------- 2. 构建战舰分布式母船两舷空间布置 (干端/半干端) ---------
z_dry_zone = z_cursor  # 锁定干端在主轴方向的起始切线对齐点

# 空间一组件：船尾露天防滑作业甲板及电控收放机组
assy.add(build_stern_deck(z_dry_zone), name="空间1_后甲板_钢制基础露天平台", color=C_STERN_DECK)
for part, color, p_name in build_machinery_winch(z_dry_zone):
    assy.add(part, name=p_name, color=color)

# 空间二组件：核心战术舱段一号甲板室内声呐综合显控室（实现空间高拟真分布式跨越）
assy.add(build_operation_cabin_floor(z_dry_zone), name="空间2_一号甲板防静电室内基座", color=C_CABIN_FLOOR)
for part, color, p_name in build_indoor_cabinet(z_dry_zone):
    assy.add(part, name=p_name, color=color)
for part, color, p_name in build_indoor_console(z_dry_zone):
    assy.add(part, name=p_name, color=color)

# 架构拓扑大动脉：全船级超长跨甲板、穿水密舱封闭式综合电缆管廊系统
assy.add(build_shipwide_cable_conduit(z_dry_zone), name="架构纽带_全船主干跨舱电缆管廊", color=C_CONDUIT)


# --------- 3. 执行模型编译与通用 STEP 工业格式无损落地 ---------
assy.save("output/towed_array_system_twin.step", "STEP")
print("="*80)
print("   通用工业 STEP 格式输出路径: output/towed_array_system_twin.step")
print("="*80)
