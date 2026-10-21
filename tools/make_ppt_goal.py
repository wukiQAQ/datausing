"""生成《FlowMask 设计目的与预期结果》初赛汇报 PPT。

复用 tools/make_ppt.py 里的版式工具（16:9、微软雅黑、统一配色），
输出到桌面的 FlowMask-设计目的与预期结果.pptx。
"""

from __future__ import annotations

from pathlib import Path

from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches

from make_ppt import (
    ACCENT,
    BORDER,
    GRAY,
    INK,
    INK2,
    LIGHT,
    ORANGE,
    RED,
    WHITE,
    blank,
    bullet_block,
    card,
    chips,
    footer,
    header,
    kpi,
    new_deck,
    para,
    rect,
    rich,
    table,
    textbox,
)

DESKTOP = Path(r"D:\31387\Desktop")
OUT = DESKTOP / "FlowMask-设计目的与预期结果.pptx"


def s1_cover(prs):
    slide = blank(prs)
    rect(slide, 0, 0, Inches(13.333), Inches(7.5), INK)
    rect(slide, 0, Inches(1.9), Inches(0.09), Inches(2.4), ACCENT)
    tf = textbox(slide, Inches(0.9), Inches(1.9), Inches(11.5), Inches(1.3))
    para(tf, "FlowMask", 54, True, WHITE, first=True)
    tf = textbox(slide, Inches(0.95), Inches(3.05), Inches(11.5), Inches(0.8))
    para(tf, "设计目的与预期结果", 30, True, ACCENT, first=True)
    tf = textbox(slide, Inches(0.95), Inches(3.95), Inches(11.0), Inches(1.0))
    para(tf, "从「看得见流量」到「看得见内容风险」", 16, False, RGBColorGray(), first=True)
    para(tf, "分级准确率 99.7% · 高危漏检 0.0% · 272 个测试用例", 13, False, RGBColorGray(), space_before=8)
    chips(slide, Inches(0.95), Inches(6.25), ["本地推理", "数据不出机", "单文件 exe", "可审计溯源"])
    tf = textbox(slide, Inches(0.95), Inches(6.85), Inches(11.0), Inches(0.4))
    para(tf, "2026-10-21 · 评审用 · 数据均来自本机实测", 10, False, RGBColorGray(), first=True)


def RGBColorGray():
    from pptx.dml.color import RGBColor

    return RGBColor(0x94, 0xA3, 0xB8)


def s2_overview(prs, page):
    slide = blank(prs)
    header(slide, "一页看懂：这个作品的设计目的", "把看不见的泄露，变成可分级、可取证、可复算的事件", "01")
    tf = textbox(slide, Inches(0.62), Inches(1.55), Inches(12.0), Inches(0.9))
    rich(
        tf,
        [
            ("一句话：", True, ACCENT),
            ("复制一份数据流（文本/文档/pcap），用三级协同找出身份证、手机号、银行卡、密钥等敏感内容，"
             "判定危险程度并生成可审计的脱敏副本，全程在本机完成。", False, INK),
        ],
        size=15,
        first=True,
    )
    for i, (value, label, color) in enumerate(
        [
            ("99.7%", "351 条评测集分级准确率（接 L2 真模型）", ACCENT),
            ("0.0%", "高危漏检率 / 安全误报率", ORANGE),
            ("272", "自动化测试用例（271 通过 + 1 跳过）", RED),
        ]
    ):
        kpi(slide, Inches(0.62) + Inches(4.15) * i, Inches(2.75), Inches(3.9), Inches(1.55), value, label, color)
    bullet_block(
        slide,
        Inches(0.62),
        Inches(4.65),
        Inches(12.1),
        Inches(1.9),
        [
            ("设计目的 ①　", "让内容风险可分级：不是「有/无敏感数据」，而是 L3/L2/L1/SAFE 四级，对应不同处置方式"),
            ("设计目的 ②　", "让成本可控：规则全量兜底（微秒级）→ 语义模型只处理命中样本 → 大模型只解释少量疑难"),
            ("设计目的 ③　", "让证据可信：原文不落库、只存脱敏副本与哈希，审计链可验签、可导出"),
        ],
        size=13.5,
        gap=8,
    )
    footer(slide, page)


def s3_pain(prs, page):
    slide = blank(prs)
    header(slide, "为什么做：三个绕不开的现实", "这也是设计目的要解决的问题", "02")
    cards = [
        ("① 流量监控只看得到「量」", "现成的采集端能告诉你谁连了谁、用了多少带宽，但看不见正文里有没有身份证号——真正泄露的恰恰是内容。", ACCENT),
        ("② 纯规则 DLP 误报高、说不清", "正则只看形状：示例文档、测试数据、随机数字都会命中；没有上下文语义，也解释不了「为什么判高危」。", ORANGE),
        ("③ 云端大模型碰不得原文", "把客户报文上传到外部 API，等于自己制造一次泄露；赛题要求「数据不出机」，只能本地推理。", RED),
    ]
    for i, (title, body, color) in enumerate(cards):
        card(slide, Inches(0.62) + Inches(4.15) * i, Inches(1.7), Inches(3.9), Inches(2.1), title, body, color)
    rect(slide, Inches(0.62), Inches(4.15), Inches(12.1), Inches(0.72), LIGHT, BORDER)
    tf = textbox(slide, Inches(0.85), Inches(4.28), Inches(11.7), Inches(0.5))
    rich(
        tf,
        [
            ("所以设计目的不是再做一个监控面板，而是：", True, INK),
            ("在原有采集/看板/告警骨架上，补上「内容级识别 + 分级处置 + 可审计」这一层。", False, GRAY),
        ],
        size=13,
        first=True,
    )
    table(
        slide,
        Inches(0.62),
        Inches(5.15),
        Inches(12.1),
        [3.6, 4.2, 4.3],
        ["传统做法", "它解决什么", "它缺什么"],
        [
            ["关键字/正则过滤", "便宜、可解释", "误报高，换写法就绕过"],
            ["云端大模型判定", "语义强", "原文出网，合规上直接否掉"],
            ["只记录不处置", "不影响业务", "无法分级响应，事后无法取证"],
        ],
        font=11.5,
        row_h=0.44,
    )
    footer(slide, page)


def s4_goals(prs, page):
    slide = blank(prs)
    header(slide, "设计目的：五条主张与对应手段", "每条目的都落到具体代码与指标，不做无法验证的承诺", "03")
    table(
        slide,
        Inches(0.62),
        Inches(1.65),
        Inches(12.1),
        [2.5, 4.6, 5.0],
        ["设计目的", "设计手段", "落点 / 可验证指标"],
        [
            ["① 内容风险可分级", "L1 规则 + 校验算法 → 评分公式（赋权/共现/直达）→ 四级", "app/identify/{rules,scoring}.py；351 条 99.7%"],
            ["② 算力成本可控", "三级协同按需升级：L1 全量、L2 命中、L3 疑难", "L1 0.022 ms/条；L2 4–10 ms；L3 1.0–1.3 s"],
            ["③ 本地闭环不出机", "全部权重本地加载（ONNX int8 / 1.5B 指令裁判）", "server/models/ 本地目录，接口只听回环"],
            ["④ 证据可复算可审计", "评分过程留痕 + SHA-256/HMAC 审计链 + 导出验签", "app/audit/chain.py；/audit/verify → ok"],
            ["⑤ 不侵入业务", "旁路部署：脱敏对象是可审计副本，不是改写线上流量", "app/traffic/live.py 跟随 pcap，文档与 PPT 口径一致"],
        ],
        font=11,
        row_h=0.62,
        aligns=[PP_ALIGN.LEFT, PP_ALIGN.LEFT, PP_ALIGN.LEFT],
    )
    tf = textbox(slide, Inches(0.62), Inches(5.9), Inches(12.1), Inches(0.8))
    rich(
        tf,
        [("设计取舍：", True, ORANGE), ("宁可牺牲一点召回，也不让误报淹没运营；宁可 L3 只做解释，也不让 1.5B 模型去覆盖分级。", False, GRAY)],
        size=12.5,
        first=True,
    )
    footer(slide, page)


def s5_target_table(prs, page):
    slide = blank(prs)
    header(slide, "想要的结果 ①：目标 vs 实测", "立项时定的目标，现在逐条对账（全部本机实测）", "04")
    table(
        slide,
        Inches(0.62),
        Inches(1.62),
        Inches(12.1),
        [3.4, 2.9, 3.2, 2.6],
        ["目标", "立项目标值", "当前实测", "结论"],
        [
            ["分级准确率", "≥ 95%", "99.7%（351 条）", "达成"],
            ["高危漏检率", "≤ 2%", "0.0%", "达成"],
            ["安全误报率", "≤ 5%", "0.0%（接 L2 后）", "达成"],
            ["L1 单条时延", "< 1 ms", "0.022 ms（≈4.4 万条/秒）", "达成"],
            ["L2 单条时延", "5–20 ms", "4.1–7.4 ms（ONNX int8）", "达成"],
            ["L3 解释时延", "2–5 s", "1.0–1.3 s（GPU bf16）", "达成"],
            ["自由文本召回", "≥ 50%", "52.5%（纯 L1）/ 90.0%（开 NER）", "达成"],
            ["审计可验签", "可复算", "/audit/verify ok=true", "达成"],
            ["开箱可用", "双击即用", "单文件 exe 50.5 MB", "达成"],
        ],
        font=11,
        row_h=0.4,
        aligns=[PP_ALIGN.LEFT, PP_ALIGN.CENTER, PP_ALIGN.LEFT, PP_ALIGN.CENTER],
    )
    tf = textbox(slide, Inches(0.62), Inches(6.4), Inches(12.1), Inches(0.5))
    para(tf, "注：准确率/误报为 L1+L2 真模型口径；自由文本召回的 90.0% 为叠加可选 NER 后的压力集结果。", 10, False, GRAY, first=True)
    footer(slide, page)


def s6_cost(prs, page):
    slide = blank(prs)
    header(slide, "想要的结果 ②：三级协同的成本结构", "把算力花在真正可疑的数据上，而不是全量过模型", "05")
    table(
        slide,
        Inches(0.62),
        Inches(1.62),
        Inches(12.1),
        [1.8, 3.6, 2.4, 2.6, 2.8],
        ["级别", "技术", "单条成本", "触发范围", "职责"],
        [
            ["L1 规则", "17 条正则 + MOD11-2 / Luhn 校验", "0.022 ms", "全量数据", "高召回：先把可疑的捞出来"],
            ["L2 语义", "bert-base-chinese 微调 + ONNX int8", "4–10 ms", "仅 L1 命中样本", "压误报：识别「示例/测试」语境"],
            ["L3 解释", "Qwen2.5-1.5B-Instruct 指令裁判（LoRA）", "1.0–1.3 s", "仅少量疑难样本", "给依据：说清为什么危险"],
        ],
        font=11.5,
        row_h=0.55,
    )
    for i, (title, body, color) in enumerate(
        [
            ("为什么不全用大模型", "1.5 s/条 × 全量 = 吞吐灾难；而且多一层原文流转就多一层泄露面。", ORANGE),
            ("为什么不全用规则", "正则只看形状：「示例里的身份证」和真实身份证长得一样，必须靠语义压误报。", ACCENT),
            ("失败了怎么办", "推理服务超时/不可用 → 自动降级回 L1 结果并标 degraded，链路不阻塞。", RED),
        ]
    ):
        card(slide, Inches(0.62) + Inches(4.15) * i, Inches(4.15), Inches(3.9), Inches(1.85), title, body, color)
    tf = textbox(slide, Inches(0.62), Inches(6.2), Inches(12.1), Inches(0.6))
    para(tf, "实测：351 条全量走完 L1+L2+L3 端到端准确率 99.7%，L1 列 p95 0.10 ms、L1+L2 列 p95 60 ms 级。", 12, False, GRAY, first=True)
    footer(slide, page)


def s7_mask(prs, page):
    slide = blank(prs)
    header(slide, "想要的结果 ③：四级脱敏与旁路口径", "按危险程度处置；脱敏对象是可审计副本，不改写业务流量", "06")
    table(
        slide,
        Inches(0.62),
        Inches(1.62),
        Inches(7.3),
        [1.5, 3.0, 3.8],
        ["级别", "处置方式", "效果示例"],
        [
            ["L3 高危", "全屏蔽为类别占位", "11010519491231002X → [已屏蔽:身份证号]"],
            ["L2 中危", "保留首字符 + 星号", "13800138000 → 1******"],
            ["L1 低危", "前置警示，保留原文", "[低危] 电话 13800138000"],
            ["SAFE", "不做处理", "（原文放行）"],
        ],
        font=11,
        row_h=0.52,
    )
    card(
        slide,
        Inches(8.2),
        Inches(1.62),
        Inches(4.5),
        Inches(1.7),
        "只改命中片段",
        "保留上下文，才能看出「这条数据是在什么业务里泄露的」；整段打码会让证据失去价值。",
        ACCENT,
    )
    card(
        slide,
        Inches(8.2),
        Inches(3.5),
        Inches(4.5),
        Inches(1.7),
        "原文不落库",
        "数据库只存脱敏副本 + 原文哈希；原始敏感值只在内存里短暂存在，导出同样走脱敏副本。",
        ORANGE,
    )
    rect(slide, Inches(0.62), Inches(4.62), Inches(12.1), Inches(1.5), LIGHT, BORDER)
    tf = textbox(slide, Inches(0.85), Inches(4.78), Inches(11.7), Inches(1.2))
    rich(tf, [("旁路口径（主动交代，避免被误判）：", True, INK)], size=13, first=True)
    para(
        tf,
        "本作品旁路部署，不串接在业务链路里；「脱敏后放行」做的是留痕 + 分级告警 + 可审计副本，"
        "真正阻断业务流量需要串接部署，作品以旁路演示为主。动态脱敏视图支持「授权后查看原文」，但会写审计链。",
        11.5,
        False,
        GRAY,
        space_before=6,
        line_spacing=1.25,
    )
    footer(slide, page)


def s8_audit(prs, page):
    slide = blank(prs)
    header(slide, "想要的结果 ④：可复算、可审计的证据链", "评审和合规最关心的问题：你凭什么让我信这个结论", "07")
    bullet_block(
        slide,
        Inches(0.62),
        Inches(1.62),
        Inches(6.3),
        Inches(4.6),
        [
            ("每条结论可复算：", "命中类别、权重、共现依据、规则 id 全部留痕，换人换机器能重算同一结果"),
            ("哈希链防篡改：", "每条 finding 生成 SHA-256 条目，prev_hash 串联，HMAC 签名，删改任一环即断链"),
            ("可验签：", "GET /api/v1/audit/verify 返回 ok/total/broken_at；/audit/export 支持 JSONL/JSON/CSV"),
            ("模型可追溯：", "记录 model_chain（如 l1>ner、l1>l2>l3）与置信度向量，解释不是黑盒"),
            ("评测可复现：", "351 条标注集 + 200 条公开语料压力集 + 一条命令跑分，报告自动生成"),
        ],
        size=12.5,
        gap=8,
    )
    rect(slide, Inches(7.3), Inches(1.62), Inches(5.4), Inches(1.15), WHITE, BORDER)
    tf = textbox(slide, Inches(7.55), Inches(1.78), Inches(5.0), Inches(0.9))
    rich(tf, [("审计语义：", True, ACCENT)], size=12, first=True)
    para(tf, "谁（operator/user）在什么时间、对哪份输入（input_hash）、产出了哪份脱敏结果（output_hash）、依据哪条规则。", 11, False, GRAY, space_before=4)
    for i, (value, label, color) in enumerate(
        [
            ("SHA-256", "每条记录哈希", ACCENT),
            ("HMAC", "签名校验", ORANGE),
            ("ok=true", "当前链完整性", RED),
        ]
    ):
        kpi(slide, Inches(7.3) + Inches(1.85) * i, Inches(3.0), Inches(1.72), Inches(1.2), value, label, color)
    card(
        slide,
        Inches(7.3),
        Inches(4.45),
        Inches(5.4),
        Inches(2.0),
        "为什么不做「只信我」",
        "把评分公式、规则表、评测脚本全部开源在仓库里，评委可以自己跑一遍、自己改数据复现——这比任何口头保证都硬。",
        ACCENT,
    )
    footer(slide, page)


def s9_inputs(prs, page):
    slide = blank(prs)
    header(slide, "想要的结果 ⑤：三条输入入口，一套判定与审计", "文本/文档、pcap 文件、实时跟随，最后都落到同一张 findings 表", "08")
    rows = [
        ["文本 / 文档", "粘贴文本；上传 txt/json/csv/sql/docx/pdf", "按段落/JSONPath/CSV 行/SQL 字面量切片段", "✅"],
        ["pcap 文件导入", "上传抓包，解析 TCP 流 + HTTP 正文", "IPv4/IPv6、TCP 重组、明文 HTTP、Content-Type 分派", "✅"],
        ["实时旁路跟随", "跟随持续增长的 pcap（dumpcap/Wireshark 写盘）", "窗口增量检测、按内容去重、迟到包重扫", "✅"],
        ["自由文本 NER", "请求级开关，长叙述里的人名/地名", "bert4ner 序列标注，与规则命中去重叠", "✅"],
    ]
    table(
        slide,
        Inches(0.62),
        Inches(1.65),
        Inches(12.1),
        [2.4, 4.2, 4.8, 0.9],
        ["入口", "怎么用", "内部怎么处理", "状态"],
        rows,
        font=11,
        row_h=0.62,
        aligns=[PP_ALIGN.LEFT, PP_ALIGN.LEFT, PP_ALIGN.LEFT, PP_ALIGN.CENTER],
    )
    tf = textbox(slide, Inches(0.62), Inches(5.0), Inches(12.1), Inches(0.6))
    rich(
        tf,
        [("统一结果：", True, ACCENT), ("同一条 finding 结构（级别/分数/类别/脱敏副本/模型链/审计条目），", False, INK),
         ("看板、任务页、导出、审计链全部复用一套代码。", False, GRAY)],
        size=13,
        first=True,
    )
    chips(slide, Inches(0.62), Inches(5.75), ["/detect", "/tasks", "/traffic/import", "/traffic/live", "/audit", "/stats", "/rules"])
    tf = textbox(slide, Inches(0.62), Inches(6.45), Inches(12.1), Inches(0.4))
    para(tf, "边界：只解析明文 HTTP；HTTPS、压缩、分块、pcapng 实时跟随暂不支持（pcapng 可用文件导入）。", 10.5, False, GRAY, first=True)
    footer(slide, page)


def s10_delivery(prs, page):
    slide = blank(prs)
    header(slide, "想要的结果 ⑥：三种可交付形态", "评委/用户不用装环境也能看到东西", "09")
    items = [
        ("正式前端", "Vue3 + Element Plus + ECharts", "5 个页面：检测对比 / 流量导入 / 任务与审计 / 统计看板 / 动态脱敏；/app/", ACCENT),
        ("零依赖单页", "内置 HTML + 原生 JS", "起服务即可用：/ui/，无 Node 构建、无外网依赖，答辩兜底", ORANGE),
        ("单文件 exe", "PyInstaller 打包", "50.5 MB，双击启动并自动开无地址栏窗口；模型权重放同级目录，缺则降级 L1", RED),
    ]
    for i, (title, sub, body, color) in enumerate(items):
        l = Inches(0.62) + Inches(4.15) * i
        rect(slide, l, Inches(1.7), Inches(3.9), Inches(2.3), WHITE, BORDER)
        rect(slide, l, Inches(1.7), Inches(3.9), Inches(0.075), color)
        tf = textbox(slide, l + Inches(0.25), Inches(1.9), Inches(3.4), Inches(2.0))
        para(tf, title, 15, True, INK, first=True)
        para(tf, sub, 11, False, color, space_before=4)
        para(tf, body, 11, False, GRAY, space_before=8, line_spacing=1.25)
    table(
        slide,
        Inches(0.62),
        Inches(4.45),
        Inches(12.1),
        [3.2, 4.4, 4.5],
        ["形态", "适合谁", "怎么起"],
        [
            ["Web 前端 / 单页", "评委、演示、日常使用", "scripts\\start_local.cmd → http://127.0.0.1:8090/app/"],
            ["单文件 exe", "不想装 Python 的同事/老师", "双击 FlowMask.exe，自动选端口并开窗口"],
            ["HTTP API", "接入其他系统", "/api/v1/detect、/tasks、/traffic/*、/audit/*"],
        ],
        font=11,
        row_h=0.44,
    )
    footer(slide, page)


def s11_progress(prs, page):
    slide = blank(prs)
    header(slide, "现在做到哪一步：五阶段全部落地", "20 天冲刺第 4 天（10-18 起），当前处于「收尾打磨 + 指标坐实」阶段", "10")
    phases = [
        ("阶段一", "10-18", "框架与主链路", "FastAPI 一体机、解析/识别/脱敏/任务/审计/API、50 个测试", ACCENT),
        ("阶段二", "10-19", "文档与界面", "Word/PDF 解析、左右对照高亮、零依赖单页 /ui/", ACCENT),
        ("阶段三", "10-20", "模型与治理", "L2 真模型、L3 指令裁判、动态脱敏、看板、审计链、统计导出", ACCENT),
        ("阶段四", "10-20", "流量接入", "pcap 导入、实时旁路跟随、NER 请求级开关", ACCENT),
        ("阶段五", "10-21", "收尾与坐实", "单文件 exe、一键启动、性能基准、351/200 评测集、L3 微调、L1 自由文本", ORANGE),
    ]
    for i, (name, date, title, body, color) in enumerate(phases):
        y = Inches(1.62) + Inches(0.95) * i
        rect(slide, Inches(0.62), y, Inches(0.85), Inches(0.72), color)
        tf = textbox(slide, Inches(0.62), y + Inches(0.06), Inches(0.85), Inches(0.6), MSO_ANCHOR.MIDDLE)
        para(tf, name, 11, True, WHITE, align=PP_ALIGN.CENTER, first=True)
        tf = textbox(slide, Inches(1.62), y, Inches(3.2), Inches(0.72), MSO_ANCHOR.MIDDLE)
        para(tf, f"{date} · {title}", 12.5, True, INK, first=True)
        tf = textbox(slide, Inches(4.9), y, Inches(7.8), Inches(0.72), MSO_ANCHOR.MIDDLE)
        para(tf, body, 11.5, False, GRAY, first=True)
    footer(slide, page)


def s12_numbers(prs, page):
    slide = blank(prs)
    header(slide, "现状盘点：可量化的家底", "所有数字都能在仓库里 grep 到、重跑出来", "11")
    kpis = [
        ("272", "测试用例", ACCENT),
        ("351", "主评测集（条）", ACCENT),
        ("200", "公开语料压力集", ORANGE),
        ("17", "L1 内置规则", ACCENT),
        ("99.7%", "分级准确率", ORANGE),
        ("0.0%", "高危漏检率", RED),
    ]
    for i, (value, label, color) in enumerate(kpis):
        col, row = i % 3, i // 3
        kpi(slide, Inches(0.62) + Inches(4.15) * col, Inches(1.65) + Inches(1.35) * row, Inches(3.9), Inches(1.15), value, label, color)
    table(
        slide,
        Inches(0.62),
        Inches(4.55),
        Inches(12.1),
        [3.2, 5.0, 3.9],
        ["维度", "当前状态", "备注"],
        [
            ["代码规模", "Python ~9.5k 行 / Vue 1k 行 / 30+ 路由", "server + web，含 12 个测试文件"],
            ["模型资产", "L2 ONNX int8 98 MB、L3 1.5B（LoRA 版另存）、NER 406 MB", "权重不入库，脚本按需下载"],
            ["可交付", "Web 前端、零依赖单页、50.5 MB 单文件 exe、HTTP API", "一键启动脚本 .cmd/.ps1/.sh"],
        ],
        font=11,
        row_h=0.5,
    )
    footer(slide, page)


def s13_limits(prs, page):
    slide = blank(prs)
    header(slide, "已知边界：现在还不做什么", "主动交代边界，比被评委问出来强", "12")
    bullet_block(
        slide,
        Inches(0.62),
        Inches(1.65),
        Inches(12.1),
        Inches(4.9),
        [
            ("旁路不改写线上流量：", "「阻断」需要串接部署，本作品做留痕 + 告警 + 可审计副本，PPT 与文档口径一致"),
            ("L3 只做复核与解释：", "1.5B 裁判零样本偏保守，LoRA 微调后单任务 76.2%，但覆盖分级反而拉低端到端（99.7%→91.2%），故默认只解释"),
            ("NER 默认关闭：", "结构化数据上它只增加多余类别；自由文本场景按请求开启（enable_ner）"),
            ("只解析明文 HTTP：", "HTTPS 需密钥、压缩/分块未实现；pcapng 仅文件导入支持"),
            ("DP / l-多样性是算子级实现：", "Laplace 噪声与 l-多样性检查已写并测试，尚未接进主流程 UI"),
            ("服务端不直抓网卡：", "Npcap/libpcap 需要 C 绑定与管理员权限，会破坏「纯 Python 可部署」；抓包交给 dumpcap/Wireshark/Go 采集端"),
        ],
        size=12.5,
        gap=9,
        bullet_color=ORANGE,
    )
    footer(slide, page)


def s14_next(prs, page):
    slide = blank(prs)
    header(slide, "还没达成的目标与下一步", "按「能否提升真实效果」排序，不做锦上添花", "13")
    table(
        slide,
        Inches(0.62),
        Inches(1.65),
        Inches(12.1),
        [3.6, 5.4, 2.6],
        ["待办", "做什么", "预期收益"],
        [
            ["L3 真召回", "扩大本域标注（含自由文本），指令微调后让它参与分级", "高危召回再提升，端到端不降级"],
            ["地名 gazetteer", "给 address_free 补湖泊/国家/景区词表", "自由文本地址召回 0 → 可测"],
            ["公开语料扩充", "SenResume / MSRA / Resume 等并入评测", "评测集从 351 → 500+，结论更稳"],
            ["服务端直抓网卡", "可选增强：打包 Npcap 或走 Go 采集端推送", "无需外部写盘工具即可实时"],
            ["DP 接进主流程", "在脱敏导出时套差分隐私 / l-多样性", "隐私度量从「实现」变「可演示」"],
        ],
        font=11,
        row_h=0.6,
    )
    tf = textbox(slide, Inches(0.62), Inches(5.7), Inches(12.1), Inches(0.6))
    rich(tf, [("原则：", True, ACCENT), ("先让「已有的 99.7%」站得住（测试、口径、可复现），再追新指标；不为了好看的数字调参掩盖问题。", False, GRAY)], size=12.5, first=True)
    footer(slide, page)


def s15_verify(prs, page):
    slide = blank(prs)
    header(slide, "验收方式：四条命令，评委可自己跑", "不需要外网，不需要模型也能跑通降级链路", "14")
    commands = [
        ("① 跑测试", "cd server && python -m pytest -o addopts=\"\" -q", "272 个用例（271 通过 + 1 跳过）"),
        ("② 跑评测", "python -m app.evaluate --compare-l2 --l2-endpoint http://127.0.0.1:8091/v1/classify", "351 条 → 99.7%，报告写入 docs/评测报告.md"),
        ("③ 跑界面", "scripts\\start_local.cmd", "打开 http://127.0.0.1:8090/app/，五页签可点"),
        ("④ 跑 exe", "FlowMask.exe --no-browser --port 8786", "单文件启动；自检：--self-check 生成路径报告"),
    ]
    for i, (title, cmd, note) in enumerate(commands):
        y = Inches(1.62) + Inches(1.25) * i
        rect(slide, Inches(0.62), y, Inches(12.1), Inches(1.05), WHITE, BORDER)
        rect(slide, Inches(0.62), y, Inches(0.075), Inches(1.05), ACCENT)
        tf = textbox(slide, Inches(0.85), y + Inches(0.1), Inches(2.6), Inches(0.85))
        para(tf, title, 13, True, INK, first=True)
        tf = textbox(slide, Inches(3.5), y + Inches(0.1), Inches(8.9), Inches(0.85))
        para(tf, cmd, 11.5, True, INK2, first=True)
        para(tf, note, 10.5, False, GRAY, space_before=4)
    tf = textbox(slide, Inches(0.62), Inches(6.55), Inches(12.1), Inches(0.4))
    para(tf, "降级说明：没有模型权重时 L2/L3 自动跳过并标 degraded，L1 链路仍可完整演示。", 10.5, False, GRAY, first=True)
    footer(slide, page)


def s16_end(prs, page):
    slide = blank(prs)
    rect(slide, 0, 0, Inches(13.333), Inches(7.5), INK)
    rect(slide, Inches(0.9), Inches(2.0), Inches(0.09), Inches(2.1), ACCENT)
    tf = textbox(slide, Inches(1.2), Inches(2.0), Inches(11.2), Inches(1.0))
    para(tf, "设计的目的，是让风险可见、可分级、可追责；", 26, True, WHITE, first=True)
    para(tf, "想要的结果，是这套东西能被人跑起来、验一遍、信得过。", 26, True, ACCENT, space_before=10)
    tf = textbox(slide, Inches(1.2), Inches(4.55), Inches(11.0), Inches(0.6))
    para(tf, "仓库：github.com/wukiQAQ/datausing · 演示：http://127.0.0.1:8090/app/", 14, False, RGBColorGray(), first=True)
    tf = textbox(slide, Inches(1.2), Inches(5.2), Inches(11.0), Inches(0.6))
    para(tf, "谢谢，欢迎提问 / 现场跑命令验证。", 14, False, RGBColorGray(), first=True)
    chips(slide, Inches(1.2), Inches(6.2), ["272 测试", "99.7%", "0.0% 漏检", "50.5 MB exe"])


def build() -> None:
    prs = new_deck()
    s1_cover(prs)
    s2_overview(prs, 2)
    s3_pain(prs, 3)
    s4_goals(prs, 4)
    s5_target_table(prs, 5)
    s6_cost(prs, 6)
    s7_mask(prs, 7)
    s8_audit(prs, 8)
    s9_inputs(prs, 9)
    s10_delivery(prs, 10)
    s11_progress(prs, 11)
    s12_numbers(prs, 12)
    s13_limits(prs, 13)
    s14_next(prs, 14)
    s15_verify(prs, 15)
    s16_end(prs, 16)
    DESKTOP.mkdir(parents=True, exist_ok=True)
    prs.save(OUT)
    print(f"已生成：{OUT}（{OUT.stat().st_size / 1024:.1f} KB，{len(prs.slides.__iter__.__self__._sldIdLst)} 页）")


if __name__ == "__main__":
    build()
