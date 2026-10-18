# FlowMask · 网络流量敏感数据分级脱敏与防泄漏系统

> 福建省大学生数据安全大赛 · 自主命题（数据脱敏方向）参赛作品
> 在自研的 [Linux 网络流量监控系统](docs/网络流量监控系统设计文档.md)（采集端 0.24.1）之上，扩展**内容级敏感数据识别、四级分级脱敏与可审计的检测链路**。

一句话：**旁路镜像流量 → 还原载荷 → 三级协同识别 → 四级分级脱敏 → 审计留痕与看板**，全程本地推理，数据不出机。

```text
10.0.0.5:51001 → 203.0.113.10:80   L3   1.00   3   …{"name":"[已屏蔽:姓名]","phone":"[已屏蔽:手机号]","id_card":"[已屏蔽:身份证号]"…
10.0.0.5:51002 → 203.0.113.10:80   L2   0.56   2   …username=zhangsan&email=z******&address=福******…
10.0.0.5:51003 → 203.0.113.10:80   L3   0.95   1   GET /api/query?api_key=[已屏蔽:密钥凭据]&q=1 HTTP/1.1
10.0.0.5:51004 → 203.0.113.10:80   L3   0.89   2   …name,phone,bank_card 李四,[已屏蔽:手机号],[已屏蔽:银行卡号]…
10.0.0.5:51005 → 203.0.113.10:80   L2   0.62   1   …客户张伟，电话 1******，需要改签
```

## 为什么做这个

1. **敏感数据在"流量里"裸奔**：业务系统把身份证、银行卡、密钥直接放进 HTTP 报文，日志与抓包工具照单全收，事后才发现泄露。
2. **传统 DLP 两头不讨好**：纯正则误报高（`4111111111111111` 到底是不是卡号？），纯大模型又慢且贵，还要求把原始报文送出本机。
3. **赛题要求"分级脱敏"**：不是一刀切封堵，而是**按敏感度分级处置**——高危全屏蔽、中危限可见、低危留警示、安全放行，并且每一步可审计。

## 核心能力（当前完成度）

| 能力 | 说明 | 状态 |
| --- | --- | --- |
| 载荷抽取 | 从以太网帧里定位 IPv4/IPv6 + TCP/UDP 载荷字节（含 TCP 选项、IPv6 扩展头、以太网填充） | ✅ |
| L1 规则识别 | 身份证（MOD 11-2 校验位）、银行卡（Luhn）、手机号、邮箱、IPv4、密钥/JWT/私钥、姓名/地址/医疗字段 | ✅ |
| 敏感度评分分级 | 类别差异化赋权取加权最大 + 多标签共现修正 + 极端阈值直达 → `L3/L2/L1/SAFE` | ✅ |
| 四级脱敏 | L3 全屏蔽、L2 保留首字符+星号、L1 前置警示、SAFE 原文 | ✅ |
| 演示数据生成 | 6 个真实 HTTP 场景（JSON/表单/CSV/明文/带密钥/对照）一键生成 pcap | ✅ |
| 离线检测 CLI | `cmd/flowmask`：pcap → findings 表格/JSON + 分级统计 | ✅ |
| TCP 重组 + HTTP 解析 | 跨包 body 还原、Content-Type 分派、gzip/分块 | 🚧 |
| findings 落库 + API | `dlp_findings` 表、`/api/v1/dlp/findings`、`/api/v1/dlp/stats` | 🚧 |
| L2 语义识别 | bert-base-chinese 多标签（ONNX int8，CPU 5-20ms） | ⏳ |
| L3 大模型复核 | 本地 Qwen2.5-3B（Ollama）只复核 L2/L3 样本，带超时熔断 | ⏳ |
| 可视化看板 | 饼图/柱状/时间线 + 脱敏前后高亮对照 | ⏳ |
| 告警联动 | 脱敏/拦截/放行 + Bearer Token 远程推送 | ⏳ |

> ✅ 已实现且有测试覆盖 · 🚧 进行中 · ⏳ 规划中（逐日排期见 [docs/设计文档.md](docs/设计文档.md)）

## 架构

```text
              ┌──────────── 采集解析层（Go）────────────┐
  网卡/镜像 → capture(pcap/Npcap/AF_PACKET/synthetic) → parser(五元组) → payload(载荷定位)
              └───────────────┬────────────────────────┘
                              │ 有界队列（丢帧计数，不阻塞采集）
              ┌───────────────▼── 识别层（三级协同）──┐
              │ L1 规则+校验   <1ms，纯 Go           │
              │ L2 BERT/ONNX  5-20ms，仅升级 L1 命中  │
              │ L3 本地 LLM   2-5s，仅复核 L2/L3，超时降级 │
              │ 评分 → L3/L2/L1/SAFE  + 脱敏模板      │
              └───────────────┬──────────────────────┘
                              │
       ┌──────────────────────▼───────────────────────┐
       │ 存储与 API（Go）：SQLite dlp_findings / 审计 JSONL / REST / SSE │
       └──────────────────────┬───────────────────────┘
                              │
       ┌──────────────────────▼───────────────────────┐
       │ 展示层：Vue3 + ECharts 看板（Tauri 桌面端 / 浏览器）│
       └──────────────────────────────────────────────┘
```

设计要点：

- **旁路不影响业务**：抓包与识别走旁路、不串接在数据路径上；"脱敏"作用对象是**可审计的脱敏副本与告警**，在线改写只作为可选的反向代理演示。
- **分级识别省算力**：只有 L1 命中的样本才升级 L2，只有 L2/L3 样本才送 L3——绝大多数流量停在微秒/毫秒级。
- **本地闭环**：模型权重、推理、原始报文都不出本机，满足赛题"数据不出机"要求。

## 快速开始

环境：Go 1.25+（Windows / Linux 均可），本机演示不依赖管理员权限与外部网络。

```powershell
# 1) 编译并跑单元测试
go build ./...
go test ./...

# 2) 生成演示数据（6 条会话 / 12 个报文，含 1 条 SAFE 对照）
go run ./cmd/genpcaps                       # 产出 data/demo-dlp.pcap
go run ./cmd/genpcaps -list                 # 只看场景与预期分级

# 3) 跑内容级检测：表格 + 分级统计
go run ./cmd/flowmask -pcap data/demo-dlp.pcap
go run ./cmd/flowmask -pcap data/demo-dlp.pcap -level L3   # 只看高危
go run ./cmd/flowmask -pcap data/demo-dlp.pcap -json       # 结构化输出

# 4) 用采集端回放同一份 pcap，看流量侧指标与网页
go run ./cmd/netmon -source replay -replay-file data/demo-dlp.pcap -offline
#    浏览器打开 http://127.0.0.1:8080（离线回放结束后进程保持存活）
```

`flowmask -json` 的字段契约（后续 `/api/v1/dlp/findings` 复用同一套字段）：

```json
{
  "findings": [
    {
      "ts": "2026-10-18T10:00:00.05Z",
      "src": "10.0.0.5:51001",
      "dst": "203.0.113.10:80",
      "proto": "TCP",
      "level": "L3",
      "score": 1,
      "types": ["姓名", "手机号", "身份证号"],
      "hits": 3,
      "masked": "…{\"name\":\"[已屏蔽:姓名]\",…}",
      "direct": true
    }
  ],
  "stats": { "frames": 12, "with_body": 6, "findings": 5, "by_level": { "L3": 3, "L2": 2 }, "duration_ms": 0 }
}
```

## 目录结构

```text
cmd/
  netmon/     采集端主程序：抓包/回放 → 解析 → 聚合 → 存储 → HTTP API → 网页
  flowmask/   内容级检测 CLI：pcap → 敏感数据识别 → 分级 → 脱敏预览
  genpcaps/   演示数据生成器：合成含敏感数据的 HTTP 会话 pcap
internal/
  capture/    数据源：pcap / Npcap / AF_PACKET / synthetic + dump 导出
  parser/     链路/网络/传输层解码 + 载荷定位（Payload / PayloadSpan）
  dlp/        识别层核心：L1 规则、评分分级、四级脱敏
  flow/       流表（五元组会话）      filter/  pcap 表达式过滤
  aggregator/ 指标聚合                storage/ SQLite / JSONL 存储
  api/        REST + SSE             alert/   告警引擎
  plugin/     声明式面板              app/     进程编排      config/ 配置
client/       Vue3 + Tauri 桌面客户端（图表、会话表、插件面板）
docs/         项目介绍 / 设计文档 / 使用指南 / 功能清单 / 基线项目文档
deploy/       部署与打包脚本          plugins/ 声明式面板插件示例
```

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/项目介绍.md](docs/项目介绍.md) | 面向评审：背景、方案、创新点、演示流程、分工 |
| [docs/设计文档.md](docs/设计文档.md) | 架构、算法、数据模型、接口契约、指标、里程碑 |
| [docs/使用指南.md](docs/使用指南.md) | 采集端安装、远程连接、日常操作（基线能力） |
| [docs/功能清单.md](docs/功能清单.md) | 采集端功能逐项说明（基线能力） |
| [docs/网络流量监控系统设计文档.md](docs/网络流量监控系统设计文档.md) | 基线采集系统的设计文档 |

## 与基线项目的关系

采集/解析/存储/API/客户端来自作者自研的 [Linux 网络流量监控系统](https://github.com/wukiQAQ/Linux-network-control)（只做流量侧监控、不做内容级审计）。FlowMask 在此之上新增：

- `internal/parser/payload.go`：载荷字节定位（原解析器只给长度、不给内容）；
- `internal/dlp/`：L1 规则识别、敏感度评分分级、四级脱敏模板；
- `cmd/genpcaps/`、`cmd/flowmask/`：演示数据生成与内容级检测 CLI。

基线项目的产品文档（使用指南、功能清单、更新日志）保留在 `docs/` 下，作为采集端能力说明。

## 团队与排期

14 天冲刺（2026-10-18 → 11-01），4-5 人分工与逐日里程碑见 [docs/设计文档.md](docs/设计文档.md)。

## 许可

仅用于福建省大学生数据安全大赛参赛与学习交流。
