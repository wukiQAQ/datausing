"""生成 FlowMask 初赛说明 PPT（简洁展示向）。

用法（在仓库根目录）：
    python tools/make_ppt.py [输出路径]
默认输出到桌面：D:\\31387\\Desktop\\FlowMask-初赛说明.pptx
"""

from __future__ import annotations

import os
import sys

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

FONT = "微软雅黑"

INK = RGBColor(0x0F, 0x17, 0x2A)
INK2 = RGBColor(0x1E, 0x29, 0x3B)
ACCENT = RGBColor(0x0D, 0x94, 0x88)
ORANGE = RGBColor(0xEA, 0x58, 0x0C)
RED = RGBColor(0xDC, 0x26, 0x26)
GRAY = RGBColor(0x64, 0x74, 0x8B)
LIGHT = RGBColor(0xF1, 0xF5, 0xF9)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
BORDER = RGBColor(0xE2, 0xE8, 0xF0)

W, H = Inches(13.333), Inches(7.5)


# ---------------------------------------------------------------- 基础工具

def new_deck() -> Presentation:
    prs = Presentation()
    prs.slide_width = W
    prs.slide_height = H
    return prs


def blank(prs: Presentation):
    return prs.slides.add_slide(prs.slide_layouts[6])


def set_font(run, size: float, bold: bool = False, color: RGBColor = INK, name: str = FONT) -> None:
    """同时设置西文与中文（East Asian）字体，避免中文回退成宋体。"""
    f = run.font
    f.size = Pt(size)
    f.bold = bold
    f.color.rgb = color
    f.name = name
    rpr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rpr.find(qn(tag))
        if el is None:
            el = rpr.makeelement(qn(tag), {})
            rpr.append(el)
        el.set("typeface", name)


def rect(slide, l, t, w, h, fill: RGBColor | None, line: RGBColor | None = None,
         shape=MSO_SHAPE.RECTANGLE, radius: float | None = None):
    s = slide.shapes.add_shape(shape, l, t, w, h)
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid()
        s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line
        s.line.width = Pt(1)
    if radius is not None:
        try:
            s.adjustments[0] = radius
        except Exception:
            pass
    s.shadow.inherit = False
    return s


def textbox(slide, l, t, w, h, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(l, t, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    return tf


def para(tf, text: str, size: float = 14, bold: bool = False, color: RGBColor = INK,
         space_before: float = 0, align=PP_ALIGN.LEFT, line_spacing: float = 1.15, first: bool = False):
    p = tf.paragraphs[0] if first else tf.add_paragraph()
    p.alignment = align
    p.space_before = Pt(space_before)
    p.line_spacing = line_spacing
    r = p.add_run()
    r.text = text
    set_font(r, size, bold, color)
    return p


def rich(tf, parts, size: float = 14, space_before: float = 0, first: bool = False,
         align=PP_ALIGN.LEFT, line_spacing: float = 1.15):
    """parts: [(text, bold, color), ...] 同一段内混排。"""
    p = tf.paragraphs[0] if first else tf.add_paragraph()
    p.alignment = align
    p.space_before = Pt(space_before)
    p.line_spacing = line_spacing
    for text, bold, color in parts:
        r = p.add_run()
        r.text = text
        set_font(r, size, bold, color)
    return p


def header(slide, title: str, kicker: str = "", step: str = "") -> None:
    rect(slide, 0, 0, W, Inches(1.0), INK)
    rect(slide, 0, Inches(1.0), W, Inches(0.045), ACCENT)
    tf = textbox(slide, Inches(0.6), Inches(0.18), Inches(9.6), Inches(0.7), MSO_ANCHOR.MIDDLE)
    para(tf, title, 25, True, WHITE, first=True)
    if kicker:
        tf2 = textbox(slide, Inches(0.62), Inches(1.16), Inches(11.0), Inches(0.4))
        para(tf2, kicker, 12.5, False, GRAY, first=True)
    if step:
        tf3 = textbox(slide, Inches(10.2), Inches(0.3), Inches(2.6), Inches(0.4), MSO_ANCHOR.MIDDLE)
        para(tf3, step, 12, True, RGBColor(0x94, 0xA3, 0xB8), align=PP_ALIGN.RIGHT, first=True)


def footer(slide, page: int, note: str = "FlowMask · 网络流量敏感数据分级脱敏与防泄漏系统") -> None:
    rect(slide, Inches(0.6), Inches(6.98), Inches(12.13), Emu(9525), BORDER)
    tf = textbox(slide, Inches(0.6), Inches(7.05), Inches(10.0), Inches(0.3))
    para(tf, note, 9.5, False, RGBColor(0x94, 0xA3, 0xB8), first=True)
    tf2 = textbox(slide, Inches(11.7), Inches(7.05), Inches(1.0), Inches(0.3))
    para(tf2, str(page), 9.5, True, RGBColor(0x94, 0xA3, 0xB8), align=PP_ALIGN.RIGHT, first=True)


def table(slide, l, t, w, col_widths: list[float], header_row: list[str], rows: list[list[str]],
          font: float = 11.5, header_font: float = 12, row_h: float = 0.42,
          header_h: float = 0.44, aligns: list | None = None, header_fill: RGBColor = INK2,
          wrap: bool = True):
    n_rows, n_cols = len(rows) + 1, len(header_row)
    shape = slide.shapes.add_table(n_rows, n_cols, l, t, w, Inches(header_h + row_h * len(rows)))
    tbl = shape.table
    tbl.first_row = False
    tbl.horz_banding = False
    total = sum(col_widths)
    for i, cw in enumerate(col_widths):
        tbl.columns[i].width = Emu(int(w * (cw / total)))
    tbl.rows[0].height = Inches(header_h)
    for i in range(1, n_rows):
        tbl.rows[i].height = Inches(row_h)
    aligns = aligns or [PP_ALIGN.LEFT] * n_cols

    def cell_text(cell, text: str, size: float, bold: bool, color: RGBColor, align):
        cell.margin_left = Inches(0.09)
        cell.margin_right = Inches(0.09)
        cell.margin_top = Inches(0.03)
        cell.margin_bottom = Inches(0.03)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        tf = cell.text_frame
        tf.word_wrap = wrap
        p = tf.paragraphs[0]
        p.alignment = align
        r = p.add_run()
        r.text = text
        set_font(r, size, bold, color)

    for j, h in enumerate(header_row):
        c = tbl.cell(0, j)
        c.fill.solid()
        c.fill.fore_color.rgb = header_fill
        cell_text(c, h, header_font, True, WHITE, PP_ALIGN.CENTER)
    for i, row in enumerate(rows, start=1):
        for j, val in enumerate(row):
            c = tbl.cell(i, j)
            c.fill.solid()
            c.fill.fore_color.rgb = WHITE if i % 2 else LIGHT
            cell_text(c, val, font, False, INK, aligns[j])
    return tbl


def bullet_block(slide, l, t, w, h, items, size=14, gap=10, color=INK, bullet_color=ACCENT):
    tf = textbox(slide, l, t, w, h)
    for idx, item in enumerate(items):
        p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
        p.space_before = Pt(0 if idx == 0 else gap)
        p.line_spacing = 1.2
        r0 = p.add_run()
        r0.text = "▍ "
        set_font(r0, size, True, bullet_color)
        if isinstance(item, str):
            r = p.add_run()
            r.text = item
            set_font(r, size, False, color)
        else:  # (加粗前缀, 正文)
            a, b = item
            r1 = p.add_run()
            r1.text = a
            set_font(r1, size, True, color)
            r2 = p.add_run()
            r2.text = b
            set_font(r2, size, False, color)


def card(slide, l, t, w, h, title: str, body: str, accent=ACCENT, title_size=13, body_size=11):
    rect(slide, l, t, w, h, WHITE, BORDER, MSO_SHAPE.ROUNDED_RECTANGLE, 0.06)
    rect(slide, l, t, Inches(0.075), h, accent)
    tf = textbox(slide, l + Inches(0.22), t + Inches(0.13), w - Inches(0.4), h - Inches(0.2))
    para(tf, title, title_size, True, INK, first=True)
    para(tf, body, body_size, False, GRAY, space_before=5, line_spacing=1.2)


def kpi(slide, l, t, w, h, value: str, label: str, color=ACCENT):
    rect(slide, l, t, w, h, WHITE, BORDER, MSO_SHAPE.ROUNDED_RECTANGLE, 0.08)
    tf = textbox(slide, l, t + Inches(0.16), w, Inches(0.6))
    para(tf, value, 26, True, color, align=PP_ALIGN.CENTER, first=True)
    tf2 = textbox(slide, l, t + Inches(0.82), w, Inches(0.5))
    para(tf2, label, 10.5, False, GRAY, align=PP_ALIGN.CENTER, first=True)


def chips(slide, l, t, texts, size=12, fill=INK2, color=WHITE, gap=0.16, pad=0.28):
    x = l
    for text in texts:
        w = Inches(pad * 2 + len(text) * (0.155 if any("\u4e00" <= c <= "\u9fff" for c in text) else 0.095))
        rect(slide, x, t, w, Inches(0.4), fill, None, MSO_SHAPE.ROUNDED_RECTANGLE, 0.5)
        tf = textbox(slide, x, t, w, Inches(0.4), MSO_ANCHOR.MIDDLE)
        para(tf, text, size, True, color, align=PP_ALIGN.CENTER, first=True)
        x += w + Inches(gap)


# ---------------------------------------------------------------- 幻灯片内容

def slide_cover(prs):
    s = blank(prs)
    rect(s, 0, 0, W, H, INK)
    rect(s, 0, Inches(6.35), W, Inches(0.06), ACCENT)
    tf = textbox(s, Inches(1.0), Inches(1.5), Inches(11.2), Inches(1.2))
    para(tf, "FlowMask", 54, True, WHITE, first=True)
    tf = textbox(s, Inches(1.0), Inches(2.75), Inches(11.2), Inches(1.2))
    para(tf, "网络流量敏感数据分级脱敏与防泄漏系统", 30, True, RGBColor(0x5E, 0xEA, 0xD4), first=True)
    tf = textbox(s, Inches(1.0), Inches(3.85), Inches(11.2), Inches(0.6))
    para(tf, "旁路流量  →  内容还原  →  三级识别  →  四级脱敏  →  审计看板", 17, False,
         RGBColor(0xC7, 0xD2, 0xE1), first=True)
    rect(s, Inches(1.0), Inches(4.75), Inches(4.6), Inches(0.03), ACCENT)
    tf = textbox(s, Inches(1.0), Inches(5.0), Inches(6.0), Inches(1.2))
    para(tf, "数据安全大赛 · 自主命题（数据脱敏方向）", 15, True, WHITE, first=True)
    para(tf, "团队 4-5 人 · 2026.10", 13, False, RGBColor(0x94, 0xA3, 0xB8), space_before=8)
    chips(s, Inches(1.0), Inches(6.45), ["本地推理", "数据不出机", "8GB 显存可跑"], 11,
          RGBColor(0x1E, 0x29, 0x3B), RGBColor(0xC7, 0xD2, 0xE1))


def slide_overview(prs):
    s = blank(prs)
    header(s, "一页看懂：它是什么", "把看不见的数据泄露，变成看得见、可分级、可审计的事件", "01")
    tf = textbox(s, Inches(0.6), Inches(1.7), Inches(12.0), Inches(1.5))
    rich(tf, [("一句话：", True, ACCENT),
              ("复制一份网络流量，从报文里找出身份证、手机号、银行卡、密钥，"
               "判断危险程度、脱敏留档。", False, INK)], 17, first=True, line_spacing=1.35)
    kpi(s, Inches(0.6), Inches(3.25), Inches(3.85), Inches(1.55), "< 1ms", "规则引擎单条识别（L1 全量扫描）")
    kpi(s, Inches(4.72), Inches(3.25), Inches(3.85), Inches(1.55), "2.0 GB", "大模型体积，8GB 显存即可本地运行", ORANGE)
    kpi(s, Inches(8.84), Inches(3.25), Inches(3.85), Inches(1.55), "100%", "本地推理，敏感数据不出机", RED)

    steps = ["① 采集：复制一份流量（旁路，不影响业务）", "② 识别：规则 + 语义模型 + 大模型三级协同",
             "③ 处置：按四级脱敏、留档、告警、上大屏"]
    x = Inches(0.6)
    for i, text in enumerate(steps):
        rect(s, x, Inches(5.25), Inches(3.9), Inches(1.1), LIGHT, BORDER, MSO_SHAPE.ROUNDED_RECTANGLE, 0.1)
        tf = textbox(s, x + Inches(0.2), Inches(5.35), Inches(3.55), Inches(0.9), MSO_ANCHOR.MIDDLE)
        para(tf, text, 12.5, False, INK, first=True, line_spacing=1.3)
        x += Inches(4.22)
    footer(s, 2)


def slide_pain(prs):
    s = blank(prs)
    header(s, "为什么要做：三个绕不过去的问题", "现状不是没人做 DLP，而是「准、快、可落地」难以同时满足", "02")
    card(s, Inches(0.6), Inches(1.7), Inches(3.9), Inches(2.35), "① 敏感数据在流量里裸奔",
         "业务把身份证、银行卡、密钥明文放进 HTTP 报文；抓包、日志、第三方 SDK 照单全收，"
         "事后才发现已经泄露。", RED)
    card(s, Inches(4.72), Inches(1.7), Inches(3.9), Inches(2.35), "② 纯正则：误报高",
         "4111111111111111 是卡号还是随机数？13800138000 是测试数据还是真实手机？"
         "形状对了不代表是敏感数据。", ORANGE)
    card(s, Inches(8.84), Inches(1.7), Inches(3.9), Inches(2.35), "③ 纯大模型：太慢太贵",
         "单条 2-5 秒，线上流量跑不动；还要把原文送到外部服务，"
         "等于用新的泄露风险去解决泄露问题。", GRAY)
    rect(s, Inches(0.6), Inches(4.35), Inches(12.13), Inches(1.05), INK, None, MSO_SHAPE.ROUNDED_RECTANGLE, 0.08)
    tf = textbox(s, Inches(0.95), Inches(4.5), Inches(11.5), Inches(0.8), MSO_ANCHOR.MIDDLE)
    rich(tf, [("我们的取舍：", True, RGBColor(0x5E, 0xEA, 0xD4)),
              ("用规则保证吞吐、用模型保证准确、用分级决定处置——不是一刀切封堵，"
               "而是按危险程度分级处理。", False, WHITE)], 15, first=True, line_spacing=1.25)

    bullets = [("赛题要求不是「拦住」，而是「分级脱敏」。", ""),
               ("低危信息继续可用，高危信息全屏蔽，每一步都留痕可审计。", ""),
               ("评委关心的是：能不能跑起来、结果对不对、有没有闭环。", "")]
    bullet_block(s, Inches(0.7), Inches(5.75), Inches(11.8), Inches(1.1),
                 [f"{a}{b}" for a, b in bullets], 13, 7)
    footer(s, 3)


def slide_arch(prs):
    s = blank(prs)
    header(s, "整体架构：四层，旁路异步", "采集链路与业务流量解耦，识别在独立队列里跑，不阻塞、不影响线上", "03")
    layers = [
        ("采集解析层", "pcap 回放 / Windows Npcap / Linux AF_PACKET 抓包，定位 TCP/UDP 载荷正文", RGBColor(0xE2, 0xE8, 0xF0), RGBColor(0x33, 0x41, 0x55)),
        ("识别层", "L1 规则识别 → 评分分级 → 四级脱敏 ｜ L2 BERT 语义复核 ｜ L3 本地大模型解释", RGBColor(0xFE, 0xF3, 0xC7), RGBColor(0x92, 0x40, 0x0E)),
        ("服务层", "Go 服务：REST + SSE 接口、SQLite 落库、告警联动、审计导出", RGBColor(0xDC, 0xFC, 0xE7), RGBColor(0x16, 0x65, 0x34)),
        ("展示层", "Vue3 + ECharts 看板：分级分布、类别 TopN、时间线、脱敏前后对照", RGBColor(0xDB, 0xEA, 0xFE), RGBColor(0x1D, 0x4E, 0xD8)),
    ]
    y = Inches(1.75)
    for i, (name, desc, bg, fg) in enumerate(layers):
        rect(s, Inches(0.6), y, Inches(12.13), Inches(1.0), bg, None, MSO_SHAPE.ROUNDED_RECTANGLE, 0.08)
        tf = textbox(s, Inches(0.85), y, Inches(2.1), Inches(1.0), MSO_ANCHOR.MIDDLE)
        para(tf, name, 15, True, fg, first=True)
        tf2 = textbox(s, Inches(2.95), y, Inches(9.5), Inches(1.0), MSO_ANCHOR.MIDDLE)
        para(tf2, desc, 12.5, False, INK, first=True)
        if i < 3:
            arrow = s.shapes.add_shape(MSO_SHAPE.DOWN_ARROW, Inches(1.05), y + Inches(0.86),
                                       Inches(0.25), Inches(0.22))
            arrow.fill.solid()
            arrow.fill.fore_color.rgb = RGBColor(0x94, 0xA3, 0xB8)
            arrow.line.fill.background()
        y += Inches(1.15)
    tf = textbox(s, Inches(0.6), Inches(6.35), Inches(12.13), Inches(0.5))
    para(tf, "三条工程原则：① 旁路部署，不串接业务链路 ② 有界队列 + 丢帧计数，绝不拖慢采集 "
             "③ 推理失败自动降级，不让模型成为单点", 11.5, False, GRAY, first=True)
    footer(s, 4)


def slide_modules(prs):
    s = blank(prs)
    header(s, "功能模块与作用（1/2）：从流量到一条检测结果", "每个模块解决一个具体问题，缺一环链路就断了", "04")
    rows = [
        ["采集与载荷还原", "抓包（回放 / Npcap / AF_PACKET），解析 IP/TCP/UDP，定位报文正文",
         "没有内容就无法检测，这是「看见」的前提"],
        ["L1 规则识别", "12 条正则 + 身份证 MOD11-2、银行卡 Luhn 校验",
         "毫秒级全量扫描，用极低成本挡掉绝大多数流量"],
        ["L2 语义识别", "BERT 多标签分类（CPU int8 推理）",
         "看上下文判断真假，把「形似」的误报压下去"],
        ["L3 大模型复核", "本地 Qwen2.5-3B 生成级别与自然语言解释",
         "只对少量可疑样本使用，给出人能看懂的依据"],
        ["评分与分级", "加权最大 + 多标签共现修正 + 极端类别直达",
         "把一堆命中变成可决策的四个级别"],
        ["四级脱敏", "全屏蔽 / 保首字符+星号 / 前置警示 / 放行",
         "按危险程度处置，且原始敏感值不入库"],
    ]
    table(s, Inches(0.6), Inches(1.75), Inches(12.13), [2.0, 5.3, 4.6],
          ["模块", "做什么", "解决什么问题（作用）"], rows, font=11.5, row_h=0.62)
    footer(s, 5)


def slide_modules2(prs):
    s = blank(prs)
    header(s, "功能模块与作用（2/2）：从结果到闭环", "检测出结果只是开始，能被查阅、追溯、复盘才算闭环", "05")
    rows = [
        ["数据落库", "findings 写入 SQLite（分级、类别、得分、脱敏副本、解释）",
         "结果可检索、可统计、可回溯"],
        ["告警联动", "按级别触发告警，支持 Bearer Token 推送到外部系统",
         "高危事件第一时间通知到人"],
        ["可视化看板", "分级饼图、类别柱状图、时间线、脱敏前后对照",
         "让「泄露了什么、多严重」一眼看懂"],
        ["审计导出", "JSONL 审计日志，含时间、来源、级别、脱敏内容",
         "满足事后审计与责任追溯"],
        ["演示数据生成", "6 类场景合成 pcap（含预期级别）",
         "无真实数据也能演示、也能当回归测试"],
        ["命令行工具", "pcap → 识别 → 评分 → 脱敏 → 表格输出",
         "答辩现场一条命令看全过程"],
    ]
    table(s, Inches(0.6), Inches(1.75), Inches(12.13), [2.0, 5.3, 4.6],
          ["模块", "做什么", "解决什么问题（作用）"], rows, font=11.5, row_h=0.62)
    tf = textbox(s, Inches(0.6), Inches(6.35), Inches(12.13), Inches(0.5))
    rich(tf, [("一句话：", True, ACCENT),
              ("前面两层解决「检测得准」，这一层解决「让人信、能追责、能复盘」。", False, GRAY)], 12, first=True)
    footer(s, 6)


def slide_l1l2l3(prs):
    s = blank(prs)
    header(s, "核心设计 ①：三级协同，按需升级", "全量走 L1，命中才升级——算力只花在真正可疑的数据上", "06")
    rows = [
        ["L1 规则", "正则 + 校验算法", "< 1 ms", "全量流量", "高召回：先把可疑的捞出来"],
        ["L2 BERT", "bert-base-chinese 多标签（CPU int8）", "5 - 20 ms", "仅 L1 命中样本", "看语义：压掉形似误报"],
        ["L3 大模型", "本地 Qwen2.5-3B（Q4）", "2 - 5 s", "仅 L2/L3 样本", "给解释：说清为什么危险"],
    ]
    table(s, Inches(0.6), Inches(1.75), Inches(12.13), [1.5, 4.3, 1.2, 2.0, 3.2],
          ["级别", "技术", "单条成本", "触发范围", "职责"], rows, font=11.5, row_h=0.55)
    card(s, Inches(0.6), Inches(4.1), Inches(3.85), Inches(1.85), "为什么不全用大模型",
         "单条 2-5 秒、显存占用高，全量跑等于给线上加一个瓶颈；而且原文外送会引入新的泄露风险。")
    card(s, Inches(4.72), Inches(4.1), Inches(3.85), Inches(1.85), "为什么不全用规则",
         "正则只看形状：测试数据、示例文档、随机数字都会被误判；上下文语义必须靠模型。", ORANGE)
    card(s, Inches(8.84), Inches(4.1), Inches(3.85), Inches(1.85), "失败了怎么办",
         "推理服务超时或不可用 → 自动降级为 L1 结果并标记 degraded，链路不阻塞、不漏检。", RED)
    footer(s, 7)


def slide_score(prs):
    s = blank(prs)
    header(s, "核心设计 ②：可解释的四级分级", "不是拍脑袋定级，每一步都有公式、有依据、可复算", "07")
    rect(s, Inches(0.6), Inches(1.75), Inches(5.6), Inches(2.1), LIGHT, BORDER, MSO_SHAPE.ROUNDED_RECTANGLE, 0.06)
    tf = textbox(s, Inches(0.85), Inches(1.9), Inches(5.15), Inches(1.85))
    para(tf, "评分公式", 12.5, True, ACCENT, first=True)
    para(tf, "得分 = 最高加权项（类别权重 × 置信度）", 12, False, INK, space_before=8)
    para(tf, "     + 多标签共现修正（每类 +0.06，封顶 0.18）", 12, False, INK, space_before=4)
    para(tf, "     + 身份类共现加成（2 类 +0.10 / ≥3 类 +0.15）", 12, False, INK, space_before=4)
    para(tf, "身份证 / 银行卡 / 密钥：一票直达最高级", 12, True, RED, space_before=6)

    levels = [("L3 高危", "≥ 0.85 或一票直达", RED), ("L2 中危", "≥ 0.55", ORANGE),
              ("L1 低危", "≥ 0.25", RGBColor(0xCA, 0x8A, 0x04)), ("SAFE 安全", "< 0.25", RGBColor(0x16, 0xA3, 0x4A))]
    x = Inches(6.5)
    for name, cond, color in levels:
        rect(s, x, Inches(1.75), Inches(1.5), Inches(1.5), WHITE, BORDER, MSO_SHAPE.ROUNDED_RECTANGLE, 0.08)
        rect(s, x, Inches(1.75), Inches(1.5), Inches(0.09), color)
        tf = textbox(s, x, Inches(2.0), Inches(1.5), Inches(0.5))
        para(tf, name, 12.5, True, color, align=PP_ALIGN.CENTER, first=True)
        tf2 = textbox(s, x, Inches(2.5), Inches(1.5), Inches(0.6))
        para(tf2, cond, 9.5, False, GRAY, align=PP_ALIGN.CENTER, first=True)
        x += Inches(1.62)

    rows = [
        ["仅手机号", "0.65 × 0.95 = 0.6175", "L2"],
        ["仅邮箱", "0.45 × 0.90 = 0.405", "L1"],
        ["邮箱 + 地址", "0.405 + 0.06 + 0.10 = 0.565", "L2"],
        ["手机 + 银行卡", "0.828 + 0.06 = 0.888", "L3"],
        ["姓名 + 手机 + 身份证", "0.931 + 0.18 + 0.15 = 1.00（封顶）", "L3"],
    ]
    table(s, Inches(0.6), Inches(4.15), Inches(12.13), [3.2, 5.6, 1.2],
          ["样本", "计算过程", "级别"], rows, font=11.5, row_h=0.4,
          aligns=[PP_ALIGN.LEFT, PP_ALIGN.LEFT, PP_ALIGN.CENTER])
    footer(s, 8)


def slide_mask(prs):
    s = blank(prs)
    header(s, "核心设计 ③：四级脱敏", "按危险程度处置：高危全遮、中危限可见、低危留警示、安全放行", "08")
    rows = [
        ["L3 高危", "全屏蔽为类别占位", "11010519491231002X  →  [已屏蔽:身份证号]"],
        ["L2 中危", "保留首字符 + 星号", "13800138000  →  1******"],
        ["L1 低危", "前置警示，保留原文", "[低危] 电话 13800138000"],
        ["SAFE", "不做处理", "（原文放行）"],
    ]
    table(s, Inches(0.6), Inches(1.75), Inches(8.6), [1.4, 2.9, 4.3],
          ["级别", "处置方式", "效果示例"], rows, font=11.5, row_h=0.5)
    card(s, Inches(9.5), Inches(1.75), Inches(3.23), Inches(1.4), "只改命中片段",
         "保留上下文，才能看懂「这条数据是在什么业务里泄露的」。")
    card(s, Inches(9.5), Inches(3.3), Inches(3.23), Inches(1.4), "原文不入库",
         "数据库只存脱敏副本，原始敏感值只在内存中短暂存在。", ORANGE)
    card(s, Inches(9.5), Inches(4.85), Inches(3.23), Inches(1.4), "类别用中文",
         "写「已屏蔽:身份证号」而不是 [MASKED]，演示与报告更直观。", RED)
    card(s, Inches(0.6), Inches(4.35), Inches(8.6), Inches(1.9), "处置动作（旁路口径）",
         "每条命中：写入脱敏副本与分级结果 → 按级别告警 → 供人工复核与审计导出。"
         "真正「阻断业务流量」需要串接部署，本作品以旁路演示为主，避免影响业务系统。",
         RGBColor(0x1D, 0x4E, 0xD8))
    footer(s, 9)


def slide_demo(prs):
    s = blank(prs)
    header(s, "实测效果：一条命令跑完整条链路", "演示数据 12 帧 / 含载荷 6 帧 / 命中 5 条 / 耗时 0 ms", "09")
    rows = [
        ["L3", "JSON 下单报文", "身份证号", "1.00", '{"name":"[已屏蔽:姓名]","phone":"[已屏蔽:手机号]","id_card":"[已屏蔽:身份证号]"}'],
        ["L2", "表单注册", "邮箱 + 地址", "0.56", "email=z******&address=福******"],
        ["L3", "带 key 的 GET", "密钥凭据", "0.95", "api_key=[已屏蔽:密钥凭据]&q=1"],
        ["L3", "CSV 客户名单", "手机 + 银行卡", "0.89", "李四,[已屏蔽:手机号],[已屏蔽:银行卡号]"],
        ["L2", "明文工单", "手机号", "0.62", "客户张伟，电话 1******，需要改签"],
    ]
    table(s, Inches(0.6), Inches(1.75), Inches(12.13), [0.7, 2.1, 1.8, 0.9, 6.6],
          ["级别", "场景", "识别类别", "得分", "脱敏预览"], rows, font=10.5, row_h=0.62,
          aligns=[PP_ALIGN.CENTER, PP_ALIGN.LEFT, PP_ALIGN.LEFT, PP_ALIGN.CENTER, PP_ALIGN.LEFT])
    tf = textbox(s, Inches(0.6), Inches(5.75), Inches(12.13), Inches(1.0))
    rich(tf, [("同一套代码两种跑法：", True, INK),
              ("① go run ./cmd/flowmask —— 命令行看全过程；"
               "② go run ./cmd/netmon 回放 —— 网页看板看分布。", False, GRAY)], 12, first=True, line_spacing=1.3)
    footer(s, 10)


def slide_module_task(prs):
    s = blank(prs)
    header(s, "模块 → 任务 → 分工", "按「谁负责哪块、这块起到什么作用」拆分，4 人也能跑，5 人更快", "10")
    rows = [
        ["A 采集协议", "抓包数据源、载荷定位、TCP 重组、HTTP/JSON/CSV 解析、演示 pcap",
         "负责「拿到内容」：没有这一步，后面全是空谈", "internal/parser、cmd/genpcaps"],
        ["B 检测引擎", "L1 规则、校验算法、评分分级、四级脱敏、规则回归测试",
         "负责「判断准不准」：系统的核心算法与规则资产", "internal/dlp/rules*.go、score.go、mask.go"],
        ["C 推理 AI", "BERT 训练/量化导出、Prompt 设计、FastAPI 推理服务",
         "负责「降误报、给解释」：让结果可信、说服评委", "inference/、dlp/client.go"],
        ["D 编排与 API", "异步队列与 worker、findings 落库、REST 接口、配置",
         "负责「跑得稳、查得到」：把算法变成能用的系统", "dlp/engine.go、store.go、api/dlp*.go"],
        ["E 前端与评测", "看板图表、脱敏对照、标注与评测、答辩材料",
         "负责「看得懂、说得清」：对外呈现与质量证明", "client/src、docs/、testdata/"],
    ]
    table(s, Inches(0.6), Inches(1.75), Inches(12.13), [1.9, 4.3, 3.9, 3.3],
          ["角色 / 模块", "主要任务", "在项目里的作用", "主要写作用域"], rows, font=10.5, row_h=0.86)
    footer(s, 11)


def slide_plan(prs):
    s = blank(prs)
    header(s, "20 天计划：先保底，再增强", "任何阶段都能拿出一条完整可演示的链路，越往后越准、越好看", "11")
    phases = [
        ("阶段一 10.18-10.21", "骨架与数据", "环境就绪、仓库整合、L1 规则、演示 pcap 生成", ACCENT),
        ("阶段二 10.22-10.26", "核心链路", "评分分级、四级脱敏、落库与 API、命令行与看板联调", RGBColor(0x1D, 0x4E, 0xD8)),
        ("阶段三 10.27-11.01", "识别升级", "TCP 重组与 HTTP 解析、L2 BERT、L3 本地大模型", ORANGE),
        ("阶段四 11.02-11.06", "收尾交付", "压测与指标、报告/PPT/视频、打标签、材料提交", RED),
    ]
    y = Inches(1.8)
    for name, sub, detail, color in phases:
        rect(s, Inches(0.6), y, Inches(12.13), Inches(0.95), WHITE, BORDER, MSO_SHAPE.ROUNDED_RECTANGLE, 0.08)
        rect(s, Inches(0.6), y, Inches(0.1), Inches(0.95), color)
        tf = textbox(s, Inches(0.9), y + Inches(0.08), Inches(2.6), Inches(0.8))
        para(tf, name, 12.5, True, INK, first=True)
        para(tf, sub, 10.5, True, color, space_before=2)
        tf2 = textbox(s, Inches(3.65), y + Inches(0.08), Inches(8.8), Inches(0.8), MSO_ANCHOR.MIDDLE)
        para(tf2, detail, 12, False, GRAY, first=True)
        y += Inches(1.06)
    tf = textbox(s, Inches(0.6), Inches(6.3), Inches(12.13), Inches(0.6))
    rich(tf, [("保底策略：", True, ACCENT),
              ("阶段二结束（10.26）就具备「pcap → 识别分级 → 脱敏 → 落库 → 页面」完整演示；"
               "之后每一层模型都是加分项，不是生存必需。", False, GRAY)], 11.5, first=True)
    footer(s, 12)


def slide_value(prs):
    s = blank(prs)
    header(s, "创新点与预期指标", "五点创新都落在「能落地」，指标都可现场复验", "12")
    bullets = [
        ("成本分级：", "大模型从「全量识别器」降为「少量样本解释器」，8GB 显存也能跑三级协同"),
        ("可解释分级：", "每条结果带命中类别、权重、共现依据与自然语言理由，可复算、可审计"),
        ("校验位抑制误报：", "身份证 MOD11-2、银行卡 Luhn 二次验证，排除形似数字"),
        ("完全本地闭环：", "采集 → 识别 → 脱敏 → 落库 → 看板 → 导出全在本机，服务只监听回环地址"),
        ("工程化底座：", "基于已开源、双平台可跑、带看板与告警的流量监控系统二次开发，不是 PPT 原型"),
    ]
    tf = textbox(s, Inches(0.6), Inches(1.75), Inches(7.3), Inches(4.6))
    for idx, (a, b) in enumerate(bullets):
        p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
        p.space_before = Pt(0 if idx == 0 else 12)
        p.line_spacing = 1.25
        r0 = p.add_run(); r0.text = f"{idx + 1}. "; set_font(r0, 13, True, ACCENT)
        r1 = p.add_run(); r1.text = a; set_font(r1, 13, True, INK)
        r2 = p.add_run(); r2.text = b; set_font(r2, 12, False, GRAY)

    tf2 = textbox(s, Inches(8.2), Inches(1.75), Inches(4.5), Inches(0.4))
    para(tf2, "预期指标（可现场复验）", 12.5, True, ACCENT, first=True)
    rows = [
        ["识别 F1", "≥ 0.85"],
        ["分级准确率", "≥ 0.85"],
        ["L1 单条耗时", "< 1 ms"],
        ["L1+L2 端到端 P95", "< 50 ms"],
        ["回放吞吐", "≥ 5k pkt/s"],
        ["显存占用", "< 6 GB"],
    ]
    table(s, Inches(8.2), Inches(2.25), Inches(4.5), [2.4, 2.1],
          ["指标", "目标"], rows, font=11, row_h=0.42, header_h=0.4,
          aligns=[PP_ALIGN.LEFT, PP_ALIGN.CENTER])
    tf3 = textbox(s, Inches(8.2), Inches(5.0), Inches(4.5), Inches(0.9))
    para(tf3, "指标来源：演示数据 + 人工标注集，测量方法与基准命令见 docs/设计文档.md 第 10 章。",
         10.5, False, GRAY, first=True, line_spacing=1.25)
    footer(s, 13)


def slide_end(prs):
    s = blank(prs)
    rect(s, 0, 0, W, H, INK)
    rect(s, 0, Inches(4.6), W, Inches(0.05), ACCENT)
    tf = textbox(s, Inches(1.0), Inches(1.3), Inches(11.2), Inches(0.9))
    para(tf, "现在能拿出来的东西", 30, True, WHITE, first=True)
    items = [
        ("可运行的代码", "采集、载荷还原、L1 规则、评分分级、四级脱敏、命令行工具、演示数据生成"),
        ("可演示的闭环", "一条命令输出分级与脱敏结果；Web 端可查流量与后续检测结果"),
        ("可评审的文档", "设计文档（架构、算法、接口、数据模型）、项目介绍、README"),
        ("可复现的效果", "6 类场景演示数据，覆盖 L3/L2/SAFE，输出与预期一致"),
    ]
    y = Inches(2.3)
    for name, desc in items:
        rect(s, Inches(1.0), y, Inches(11.3), Inches(0.95), RGBColor(0x1E, 0x29, 0x3B), None,
             MSO_SHAPE.ROUNDED_RECTANGLE, 0.08)
        tf = textbox(s, Inches(1.3), y, Inches(2.8), Inches(0.95), MSO_ANCHOR.MIDDLE)
        para(tf, name, 14, True, RGBColor(0x5E, 0xEA, 0xD4), first=True)
        tf2 = textbox(s, Inches(4.2), y, Inches(7.9), Inches(0.95), MSO_ANCHOR.MIDDLE)
        para(tf2, desc, 12.5, False, RGBColor(0xC7, 0xD2, 0xE1), first=True)
        y += Inches(1.08)
    tf = textbox(s, Inches(1.0), Inches(6.75), Inches(11.3), Inches(0.5))
    para(tf, "谢谢观看 · 演示与代码见 GitHub：github.com/wukiQAQ/datausing", 13, True,
         RGBColor(0x94, 0xA3, 0xB8), first=True)


def build() -> Presentation:
    prs = new_deck()
    slide_cover(prs)
    slide_overview(prs)
    slide_pain(prs)
    slide_arch(prs)
    slide_modules(prs)
    slide_modules2(prs)
    slide_l1l2l3(prs)
    slide_score(prs)
    slide_mask(prs)
    slide_demo(prs)
    slide_module_task(prs)
    slide_plan(prs)
    slide_value(prs)
    slide_end(prs)
    return prs


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.expanduser("~"), "Desktop", "FlowMask-初赛说明.pptx")
    prs = build()
    out_dir = os.path.dirname(os.path.abspath(out))
    os.makedirs(out_dir, exist_ok=True)
    prs.save(out)
    print(f"saved: {out}")
    print(f"slides: {len(prs.slides._sldIdLst)}")


if __name__ == "__main__":
    main()
