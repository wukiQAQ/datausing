# FlowMask · 分级脱敏与审计溯源系统

> 福建省大学生数据安全大赛 · 自主命题（数据脱敏方向）参赛作品
> **v2 主链路**：文档/文件/文本输入 → 多模型分级识别 → 四级脱敏 → 落库留痕 → 哈希链审计（Python FastAPI 一体机，见 [`server/README.md`](server/README.md)）
> **网络流量扩展**：旁路镜像 → 载荷还原 → 同一套识别分级 → 检测结果落库与查询（Go 采集端，本 README 下半部分）

| 入口 | 技术栈 | 状态 | 一句话 |
| --- | --- | --- | --- |
| `server/`（v2 主链路） | Python 3.12 · FastAPI · SQLite | ✅ 框架完成、305 个测试（304 通过 + 1 跳过；含 pcap 导入、实时旁路、L2/L3 真模型联调、性能基准、自由文本 NER 开关） | 上传/粘贴（txt/json/csv/sql/docx/pdf）→ 分级脱敏 → 审计链；`http://127.0.0.1:8090/app/` 正式前端、`/ui/` 零依赖单页 |
| `web/`（正式前端） | Vue3 · Element Plus · ECharts · Vite | ✅ 5 个页面 | 即时检测·左右对照 / 任务与审计链 / 统计看板 / 识别规则 / 动态脱敏；构建产物挂在服务端 `/app` |
| `cmd/flowmask` + `cmd/netmon`（网络扩展） | Go 1.25 · 纯 Go 抓包 | ✅ pcap → findings → API 可查 | 旁路 pcap/网卡 → 敏感数据识别 → `/api/v1/dlp/*` |

```text
# v2：一条命令跑起来（文档侧主链路）
cd server && pip install -r requirements.txt
scripts\start_local.cmd                       # 一键起（Windows；Linux/macOS 用 ./scripts/start_local.sh）
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1   # 打包 Windows 单文件 exe（双击即用）
#   正式前端：cd ../web && npm install && npm run build → http://127.0.0.1:8090/app/
#   零依赖单页：http://127.0.0.1:8090/ui/ （未构建前端时根路径自动回落到这里）
# 网络侧：pcap → 识别分级 → 落库 → 接口查询
go run ./cmd/genpcaps && go run ./cmd/flowmask -db data/flowmask.db
```

## 为什么做这个

1. **敏感数据到处裸奔**：文档、导出报表、日志与 HTTP 报文里都有身份证、银行卡、密钥，事后才发现泄露。
2. **传统 DLP 两头不讨好**：纯正则误报高（`4111111111111111` 到底是不是卡号？），纯大模型又慢且贵，还要求把原始数据送出本机。
3. **赛题要求"分级脱敏"**：不是一刀切封堵，而是**按敏感度分级处置**——高危全屏蔽、中危限可见、低危留警示、安全放行，并且每一步可审计、可溯源。

---

## 文档侧核心能力（需求 v2 · 当前完成度）

| 能力 | 说明 | 状态 |
| --- | --- | --- |
| 多格式解析与分段定位 | 文本整段；JSON `$` 路径；CSV/TSV `row{n}.{列名}`；SQL 只取 DML 字面量（DDL 保留）；Word 段落/表格；PDF 按页 | ✅ |
| 三级协同识别 | L1 17 条规则（含 4 条自由文本规则）+ 省/市地名库补白 + 编码载荷解码复核（base64 / URL-safe / hex / 百分号，支持两层嵌套）+ 校验算法 ✅；L2 独立推理服务 ✅（`server/l2_service/`，bert-base-chinese 微调 + ONNX int8 真模型，单条 10–33ms）；L1.5 可选 NER（人名/地址，自由文本召回 22.2%→100%，请求级开关 + 前端标签）✅；L3 独立推理服务 ✅（`server/l3_service/`，Qwen2.5-1.5B-Instruct 指令裁判 + Qwen3Guard-Gen-0.6B 护栏双后端，真权重已实测；默认封顶 L2、只贡献解释） | ✅ |
| 分级与赋权 | 加权最大 + 共现修正 + 极端类别直达 → L3/L2/L1/SAFE | ✅ |
| 四级脱敏 + 命中区间 | 四级模板 + `spans` 区间，前端可高亮「原文 → 脱敏」对照 | ✅ |
| 任务与落库 | 后台线程执行，`tasks`/`findings` 表，按级别/类别/任务查询 | ✅ |
| 审计哈希链 | HMAC-SHA256 链式签名（`prev_hash`/`signature`），可校验、可导出、可定位断链 | ✅ |
| 统计看板数据 | 分级分布、类别 TopN、评分直方图、任务数 | ✅ |
| 自定义规则 | 增删启停 + 正则可校验，库内持久化并即时生效 | ✅ |
| 差分隐私 / l-多样性 | Laplace 噪声、数值泛化、l-多样性检查（已接口化） | ✅ |
| 单页可视化 | `GET /ui/`：上传/粘贴 → 左右对照 + 分级统计，零前端依赖 | ✅ |
| 正式前端（Vue3 + Element Plus + ECharts） | `web/`：6 个页面（即时检测·左右对照 / 流量导入·pcap / 任务与审计链 / 统计看板 / 识别规则 / 动态脱敏），Vite 构建后挂 `/app`；未构建自动回落 `/ui/` | ✅ |
| 流量导入（服务端 pcap） | 上传 pcap/pcapng → TCP 重组 + HTTP 正文提取 → 复用同一套分级/脱敏/任务/审计；`GET /traffic/sample` 自带示例；前端「流量导入 · pcap」页面一键跑示例（`?demo=1`） | ✅ |
| 实时旁路（跟随 pcap 文件） | 持续跟随 dumpcap/Wireshark/自研采集器写出的 pcap，按窗口把新正文汇总成检测任务（结果进任务页/看板/审计链）；`/traffic/live*` + 前端面板 + `samples/simulate_live_traffic.py` 演示脚本 | ✅ |
| 动态脱敏视图（授权切换） | `POST /api/v1/dynamic/render` + `/dynamic/policy`：未授权只给脱敏副本、授权返回原文并写审计链；内置页面「动态脱敏」页签可直接演示 | ✅ |
| 评测集与指标 | 自建标注集 **351 条**（策展 75 + 生成 225 + 稀有类别补样 51，与训练数据 0 重叠）+ 200 条公开语料压力集：L1 only 准确率 94.3% → 接 L2 真模型后 **100.0%**、安全误报率 **20.8%→0.0%**、高危漏检率 0.0%；自由文本压力集纯 L1 0.0% → 52.5%（自由文本规则）→ 61.0%（+地名库）→ 开 NER 90.0%，`--compare-l2 --l2-endpoint` 一键复跑 | ✅ |
| L3 训练侧（LoRA 指令微调） | `server/l3_training/`：351 条金标 → 指令样本，LoRA 微调 Qwen2.5-1.5B；裁判单任务准确率 60.0% → **76.2%**，合并后直接接 `l3_service` | ✅ |
| L3 训练侧（LoRA 指令微调） | `server/l3_training/`：351 条金标 → 指令样本，LoRA 微调 Qwen2.5-1.5B；裁判单任务准确率 60.0% → **76.2%**，合并后直接接 `l3_service` | ✅ |
| L2 训练侧（RoBERTa） | `server/l2_training/`：11 标签（含 benign）、自举数据 800 条（30% 困难负例）、数据体检、微调 + ONNX 导出；本机已训出真模型（macro-F1 0.9844、benign F1 0.954） | ✅ |
## 网络流量扩展（阶段一成果，Go 采集端）

一句话：**旁路镜像流量 → 还原载荷 → 三级协同识别 → 四级分级脱敏 → 审计留痕与看板**，全程本地推理，数据不出机。

```text
10.0.0.5:51001 → 203.0.113.10:80   L3   1.00   3   …{"name":"[已屏蔽:姓名]","phone":"[已屏蔽:手机号]","id_card":"[已屏蔽:身份证号]"…
10.0.0.5:51002 → 203.0.113.10:80   L2   0.56   2   …username=zhangsan&email=z******&address=福******…
10.0.0.5:51003 → 203.0.113.10:80   L3   0.95   1   GET /api/query?api_key=[已屏蔽:密钥凭据]&q=1 HTTP/1.1
10.0.0.5:51004 → 203.0.113.10:80   L3   0.89   2   …name,phone,bank_card 李四,[已屏蔽:手机号],[已屏蔽:银行卡号]…
10.0.0.5:51005 → 203.0.113.10:80   L2   0.62   1   …客户张伟，电话 1******，需要改签
```

## 网络侧核心能力（当前完成度）

| 能力 | 说明 | 状态 |
| --- | --- | --- |
| 载荷抽取 | 从以太网帧里定位 IPv4/IPv6 + TCP/UDP 载荷字节（含 TCP 选项、IPv6 扩展头、以太网填充） | ✅ |
| L1 规则识别 | 身份证（MOD 11-2 校验位）、银行卡（Luhn）、手机号、邮箱、IPv4、密钥/JWT/私钥、姓名/地址/医疗字段 | ✅ |
| 敏感度评分分级 | 类别差异化赋权取加权最大 + 多标签共现修正 + 极端阈值直达 → `L3/L2/L1/SAFE` | ✅ |
| 四级脱敏 | L3 全屏蔽、L2 保留首字符+星号、L1 前置警示、SAFE 原文 | ✅ |
| 演示数据生成 | 6 个真实 HTTP 场景（JSON/表单/CSV/明文/带密钥/对照）一键生成 pcap | ✅ |
| 离线检测 CLI | `cmd/flowmask`：pcap → findings 表格/JSON + 分级统计 | ✅ |
| TCP 重组 + HTTP 解析 | 跨包 body 还原、Content-Type 分派、gzip/分块 | 🚧 |
| findings 落库 + API | `dlp_findings` 表（SQLite/JSONL）、`/api/v1/dlp/findings`、`/stats`、`/export` | ✅ |
| L2 语义识别 | bert-base-chinese 多标签（ONNX int8，CPU 5-20ms） | ⏳ |
| L3 大模型复核 | 本地指令裁判 Qwen2.5-1.5B-Instruct / 护栏 Qwen3Guard-Gen-0.6B（`server/l3_service/`），只复核 L1 灰区、只升不降且默认封顶 L2，带超时降级 | ✅ |
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
              │ L3 本地护栏 0.3-1s，仅复核灰区，超时降级   │
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

## 模型权重（不进仓库，按需下载）

代码与配置在仓库里，**模型权重不提交**（合计 5 GB+）。用脚本按需拉取，不需要就跳过——不下载也能跑，
链路自动降级为 L1-only 并标 `degraded`：

```powershell
cd server
python scripts/download_models.py --list        # 有哪些权重、多大、给谁用
python scripts/download_models.py --only l3 ner # 最常用：L3 指令裁判 + 自由文本 NER
```

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

# 3.5) 落库：把检测结果写进 SQLite，供在线接口/看板查询
go run ./cmd/flowmask -db data/flowmask.db

# 4) 用采集端回放同一份 pcap，看流量侧指标与网页
go run ./cmd/netmon -source replay -replay-file data/demo-dlp.pcap -offline
#    浏览器打开 http://127.0.0.1:8080（离线回放结束后进程保持存活）
```

> 让网页/接口看到 DLP 结果：把采集端配置里的 `storage.sqlite_path` 指向 `data/flowmask.db`
> （`[storage] storage = "sqlite"`），启动后即可查询下面的接口。

| 在线接口 | 说明 |
| --- | --- |
| `GET /api/v1/dlp/findings?since=&level=&type=&src=&page=&page_size=` | 分页查询检测结果（新的在前，`page_size` 上限 100） |
| `GET /api/v1/dlp/findings/{id}` | 单条详情：五元组、级别、命中类别、脱敏副本 |
| `GET /api/v1/dlp/stats?since=&step=` | 看板数据：分级分布 + 类别 TopN + 时间线 |
| `GET /api/v1/dlp/export?format=jsonl&since=` | 审计导出：首行 meta，其后一行一条（已脱敏） |

`flowmask -json` 与 `/api/v1/dlp/findings` 共用同一套字段契约（`types` 是中文展示名，库内存英文键 `id_card`）：

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
server/       v2 主链路（Python FastAPI 一体机）：解析 → 识别 → 脱敏 → 落库 → 审计链
web/          v2 正式前端（Vue3 + Element Plus + ECharts + Vite），构建产物挂服务端 /app
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
client/       （网络侧）Vue3 + Tauri 桌面客户端：图表、会话表、插件面板
docs/         项目介绍 / 设计文档 / 使用指南 / 功能清单 / 基线项目文档
deploy/       部署与打包脚本          plugins/ 声明式面板插件示例
```

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/项目介绍.md](docs/项目介绍.md) | 面向评审：背景、方案、创新点、演示流程、分工 |
| [server/README.md](server/README.md) | **v2 主链路**：接口、配置、目录结构、设计要点 |
| [docs/需求对照与待办清单.md](docs/需求对照与待办清单.md) | 需求 v2 的 58 条对照表、已完成/待办、关键路径与分工 |
| [docs/性能报告.md](docs/性能报告.md) | 本机实测：L1 0.022 ms/条、端到端 2.18 ms、L2 int8 4.1 ms/条、L3 1.16 s/条（`python -m bench` 复现） |
| [docs/设计文档.md](docs/设计文档.md) | 网络侧架构、算法、数据模型、接口契约、指标、里程碑 |
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

20 天冲刺（2026-10-18 → 11-06）：文档侧按需求 v2 五阶段推进（框架 → 解析/前端 → 多模型 → 动态脱敏/评测 → 材料），4-5 人分工与待办优先级见 [docs/需求对照与待办清单.md](docs/需求对照与待办清单.md)。

## 许可

仅用于福建省大学生数据安全大赛参赛与学习交流。
