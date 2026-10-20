# FlowMask Server（FastAPI 一体机）

> 文件/文档侧的分级脱敏与审计溯源服务：**上传或粘贴 → 多模型分级识别 → 四级脱敏 → 落库留痕 → 哈希链审计**。
> 这是需求 v2（2026-10-19）的主链路实现；仓库里的 Go 采集端与 `cmd/flowmask` 保留为「网络流量旁路」扩展能力。

## 1. 现在能做什么

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 文本/JSON/CSV/SQL/Word/PDF 解析与分段定位 | ✅ | 文本整段；JSON 走 `$` 路径；CSV/TSV 每单元格 `row{n}.{列名}`；SQL 提取 DML 字符串字面量；Word 段落 `p{n}` / 表格 `table{表}.{行}.{列}`；PDF 按页 `page{n}`（依赖可选 `pdfplumber`） |
| pcap 流量导入（服务端） | ✅ | 上传 pcap/pcapng → TCP 重组 + HTTP 请求解析 → 每个正文当一个片段，复用同一套识别/分级/脱敏/任务/审计；`GET /traffic/sample` 提供自带示例（`samples/demo-traffic.pcap`）；HTTPS、压缩体与分块传输暂不解析 |
| 实时旁路（跟随 pcap 文件） | ✅ | 服务端持续跟随一个不断增长的 pcap（由 dumpcap/Wireshark/自研采集器写），按「落库窗口」把新正文汇总成检测任务；`/traffic/live*` 管理会话，演示脚本 `samples/simulate_live_traffic.py`；只支持经典 pcap |
| L1 规则识别 | ✅ | 12 条规则 + 身份证 MOD 11-2 / 银行卡 Luhn / IPv4 校验 |
| 敏感度评分与四级分级 | ✅ | 加权最大 + 多标签共现修正 + 极端类别直达（L3/L2/L1/SAFE） |
| 四级脱敏 + 左右对照 | ✅ | L3 全屏蔽、L2 保留首字符+星号、L1 前置警示、SAFE 原文；输出「原文哈希 + 脱敏副本 + 命中区间（spans）」供前端高亮，原文不入库 |
| L2 语义识别（独立推理服务） | ✅ | `l2_service/` HTTP 服务：mock / ONNX int8 / auto 三后端；已用 bert-base-chinese 微调模型实测（单条 10–33 ms，CPU）；基座对比 roberta-wwm-ext 96.0% < bert 98.7%，保留 bert |
| L1.5 实体识别（NER，可选） | ✅ | `app/identify/ner.py`：BERT4NER 补人名/地址（B-PER/B-LOC）；自由文本类型召回 22.2% → 100%（8 条探针）、公开语料压力集 200 条准确率 0.0% → 89.0%（`python -m ner_probe`），代价约 +60–70 ms/条 CPU 前向；结构化数据上会多出类别 ⇒ **服务端不做默认开启**，由每次请求的 `enable_ner` 决定（前端检测页有「自由文本 NER」开关 + 状态标签） |
| L3 本地大模型复核 | ✅ | 独立 `l3_service` 双后端：**指令裁判**（Qwen2.5-1.5B-Instruct，按本项目四级标准直出 `level/reason/categories`，默认推荐）或**护栏**（Qwen3Guard-Gen-0.6B，Safe/Controversial/Unsafe）；只复核 L1 灰区、只升不降、默认封顶 L2，异常即降级 |
| 差分隐私 / l-多样性算子 | ✅ | Laplace 噪声、数值泛化、l-多样性检查（接口化，供动态脱敏演示） |
| 后台任务 + 结果落库 | ✅ | 线程执行、`tasks`/`findings` 表、按级别/类型查询 |
| 审计哈希链 | ✅ | HMAC-SHA256 链式签名，可校验、可导出；篡改/删除能定位到断链条目 |
| 统计看板数据 | ✅ | 分级分布、类别 TopN、评分直方图、任务数 |
| 单页可视化（左右对照） | ✅ | `GET /ui/` 内置页面：上传/粘贴 → 左原文高亮、右脱敏副本、分级统计，无需装前端工具链 |
| 正式前端（Vue3 + Element Plus + ECharts） | ✅ | `web/`：6 个页面（即时检测·左右对照 / 流量导入·pcap / 任务与审计链 / 统计看板 / 识别规则 / 动态脱敏），`npm run build` 后由服务端挂在 `/app`；未构建时自动回落到 `/ui/` |
| 自定义规则 | ✅ | 增删启停 + 正则合法性校验，库内持久化 |
| 评测跑分 | ✅ | 自建标注集 **300 条**（策展 75 + 生成 225，id/内容唯一、与训练集 0 重叠）+ 200 条 NER 压力集；L1 only 准确率 93.0%，接 L2 后 99.7%、安全误报率 28.6%→0.0%，`--compare-l2` 一条命令复现 |
| 性能基准 | ✅ | `server/bench.py` 本机实测：L1 **0.022 ms**/条（≈4.4 万条每秒）、端到端 `/detect` **2.18 ms**（≈459 请求/秒）、L2 int8 单条 7.4→4.1 ms、L3 1.5B **1.16 s**/条；报告 `docs/性能报告.md` 可一条命令复跑 |
| 动态脱敏视图（授权切换） | ✅ | `POST /api/v1/dynamic/render`：未授权返回脱敏副本、授权返回原文并写审计链；`GET /dynamic/policy` 给出四级策略表；内置页面「动态脱敏」页签可直接演示 |
| L2 训练侧（RoBERTa 多标签） | ✅ | `l2_training/`：11 标签（10 敏感 + benign）、自举数据（默认 800 条，含 30% 困难负例）、弱标注、数据体检、微调 + ONNX 导出；本机真模型 macro-F1 **0.9844**、benign F1 **0.954** |

## 2. 快速开始

环境：Python 3.11+（本机 3.12.10）。依赖只有 FastAPI 全家桶，存储用标准库 `sqlite3`（无 ORM、无 CGO）。

### 2.1 一键启动（推荐）

```powershell
cd server
pip install -r requirements.txt          # 运行依赖（固定版本）；开发/测试用 requirements-dev.txt

# Windows：双击或命令行都行（内部已用 -ExecutionPolicy Bypass，绕过 PS 执行策略限制）
scripts\start_local.cmd
scripts\start_local.cmd -WithL2 -EnableNer        # 叠加 L2 语义模型 + 自由文本 NER
scripts\start_local.cmd -WithL2 -WithL3 -L3Device cuda
scripts\stop_local.cmd                            # 停止全部

# Linux / macOS
./scripts/start_local.sh
./scripts/start_local.sh --with-l2 --ner
./scripts/stop_local.sh
```

脚本会：加载 `.env`（可从 `.env.example` 复制）→ 按开关拉起 L2/L3 → 起主服务 → 等 `/api/v1/health` →
打印入口并（默认）打开浏览器；pid/日志写在 `server/data/run/`。参数见第 11 章。

### 2.2 手动启动（分步调试用）

```powershell
python -m pytest -o addopts="" -q    # 218 个用例
python -m bench --markdown-out ../docs/性能报告.md   # 性能基准（L1/端到端/L2；加 --l3-endpoint 测 L3）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8090
# 构建正式前端（可选，需 Node 18+；产物在 ../web/dist）
cd ../web && npm install && npm run build && cd ../server
# 打开 http://127.0.0.1:8090/app/ 用正式前端（构建过时根路径自动跳这里）
# 或 http://127.0.0.1:8090/ui/ 用零依赖内置单页；接口文档在 /docs
# 想支持 PDF：pip install pdfplumber（未安装时 /formats 会提示，上传 PDF 返回 415）
```

一次最小闭环（PowerShell）：

```powershell
# 1) 即时检测（不落库，用于左右对照）
curl.exe -s -X POST http://127.0.0.1:8090/api/v1/detect -H "Content-Type: application/json" `
  -d '{\"text\":\"客户张三，手机 13800138000，身份证 11010519491231002X\"}'

# 2) 建任务（带审计留痕）
curl.exe -s -X POST http://127.0.0.1:8090/api/v1/tasks/text -H "Content-Type: application/json" `
  -d '{\"text\":\"客户张三，手机 13800138000\"}'

# 3) pcap 流量导入（HTTP 正文 → 同一套识别/分级/审计）
curl.exe -s -F "pcap=@samples/demo-traffic.pcap" "http://127.0.0.1:8090/api/v1/traffic/import"
curl.exe -s -o demo.pcap "http://127.0.0.1:8090/api/v1/traffic/sample"

# 4) 查结果 / 统计 / 校验审计链 / 导出
curl.exe -s "http://127.0.0.1:8090/api/v1/tasks/1/findings"
curl.exe -s "http://127.0.0.1:8090/api/v1/stats"
curl.exe -s "http://127.0.0.1:8090/api/v1/audit/verify"
curl.exe -s -o findings.jsonl "http://127.0.0.1:8090/api/v1/tasks/1/export?format=jsonl"
```

## 3. 多模型协同（L2 推理服务）

L2 跑在独立进程里（模型体积与依赖都重），一体机按 HTTP 契约调用、失败自动降级：

```powershell
# 终端 A：起 L2 服务（默认 mock 后端，零模型依赖；接真模型见 l2_service/README.md）
cd server && python -m l2_service            # http://127.0.0.1:8091

# 接真模型（本仓实测过的路径）：bert-base-chinese 微调 + ONNX int8
python -m l2_training dataset --out data/l2_training.jsonl --count 800
python -m l2_training train --dataset data/l2_training.jsonl --base-model models/bert-base-chinese
FLOWMASK_L2_MODE=onnx FLOWMASK_L2_MODEL_DIR=l2_models/bert-sens-onnx python -m l2_service

# 终端 B：让一体机启用 L2
$env:FLOWMASK_ENABLE_L2 = "1"
$env:FLOWMASK_L2_ENDPOINT = "http://127.0.0.1:8091/v1/classify"
python -m uvicorn app.main:app --port 8090
```

协同规则（`app/identify/pipeline.py`）：只对 L1 命中的样本升级；L2 补类别加少量分；
`benign≥0.65` 时压分抑制"文档/测试示例"这类形似误报（证件等直达类要 `≥0.75` 才压，且取消 `direct`）；
服务异常只标记 `degraded`，结论继续用 L1。阈值可用环境变量覆盖（`FLOWMASK_L2_BENIGN_MIN`、
`FLOWMASK_L2_BENIGN_DIRECT_MIN`），阈值是在 75 条留出集上调出来的；扩样到 300 条后误报仍为 0%，高危漏检保持 0%。

实测增益（真模型，**300 条**评测集：策展 75 + 生成 225，训练数据与评测集隔离）：

| 指标 | L1 only | L1 + L2（真模型） | 变化 |
| --- | ---: | ---: | ---: |
| 分级准确率 | 93.0% | **99.7%** | ↑ +6.7% |
| 类别 Micro-F1 | 0.875 | 0.875 | → 持平（L2 主要修分级，不新增类别） |
| 安全误报率 | 28.6% | **0.0%** | ↓ -28.6% |
| 高危漏检率 | 0.0% | 0.0% | → 稳定 |

> 训练/导出细节（困难负例、`labels.json` 回退、`dynamo=False`、padding mask）见
> [`l2_training/README.md`](l2_training/README.md) 与 [`l2_service/README.md`](l2_service/README.md)。

基座对比（同期实测，同一份 800 条自举数据、同一套调参）：`chinese-roberta-wwm-ext` 分级准确率 **96.0%**，同权重 fp32 ONNX 也是 96.0%（排除 int8 量化因素），阈值 0.40–0.85 扫描无变化 ⇒ 在自举数据规模下**换更大基座没有收益，保留 bert-base-chinese**。

### 3.1 L1.5 实体识别（NER，可选）

规则与 L2 都依赖「字段名上下文」：`name=张三`、`地址：…` 能命中，但「张三昨天从福州赶过来」这类叙述文本里，
人名/地址几乎抓不到。可选 NER 层补的就是这块：

- 实现：`app/identify/ner.py`（`TokenNerDetector` 通用 HF 序列标注 / `RaNerDetector` ModelScope 管道），
  默认权重 `shibing624/bert4ner-base-chinese`（406 MB，不入库），B-PER/B-LOC → 姓名/地址；
- 挂载点：`pipeline._run_chain` 在 L1 之后补命中，跳过与已有命中重叠的区间（不重复计分），异常一律降级；
- 开关：`FLOWMASK_ENABLE_NER=1` + `FLOWMASK_NER_MODEL_DIR=models/bert4ner`
  （`FLOWMASK_NER_BACKEND=token|raner`、`FLOWMASK_NER_DEVICE=cpu|cuda`）：服务端开关只决定「是否加载/是否默认启用」，
  **每次请求可用 `enable_ner` 显式覆盖**（true 打开、false 关闭、缺省跟服务端）；`/detect`、`/detect/file`、`/tasks/text`、`/traffic/*` 均支持；
  `/health` 的 `ner` / `ner_backend` 告诉前端开关是否可用；
- 探针（`python -m ner_probe --model-dir models/bert4ner`，8 条自由叙述 / 18 个标注类别）：
  类型召回 **22.2% → 100.0%**，8/8 样本提升；
- 同一开关在 **300 条**评测集上：分级准确率不变（99.7%）、类别 Micro-F1 **0.875 → 0.945**
  （召回 0.829 → 0.997，代价是多出 18 个 FP 类别）、p95 时延 67.1 → 150.3 ms；
- **公开语料压力集**（`eval/ner_stress.jsonl`，200 条 weibo_ner 自由文本，L1 规则抓不到）：
  纯 L1 准确率 **0.0%** / Micro-F1 **0.000** → 开 NER 后 **89.0%** / **0.936**、p95 28.5 ms
  （剩 4 条错例是语料标注噪声，如“电器城/游乐园/宿舍”这类非标准地点）；
- 结论：结构化数据上 NER 主要补召回、不改变分级，所以**默认关闭**；自由文本场景按请求打开
  （`http://127.0.0.1:8090/app/?ner=1#detect` 会带样例文本自动演示）。

## 4. L3 大模型复核（指令裁判 / 护栏双后端）

第三级识别跑在独立进程里，职责是「复核 + 解释」，不重新定级。两种后端按需选：

| 模式 | 权重 | 输出 | 说明 |
| --- | --- | --- | --- |
| `FLOWMASK_L3_MODE=instruct`（**推荐**） | Qwen2.5-1.5B-Instruct（3.0 GB） | 一行 JSON：`{"level":"L3\|L2\|L1\|SAFE","categories":[...],"reason":"…"}` | 按本项目四级标准直接给分级建议与中文理由 |
| `FLOWMASK_L3_MODE=hf` | Qwen3Guard-Gen-0.6B（1.5 GB） | `Safe / Controversial / Unsafe` | 内容安全口径，对中文 PII 偏保守（几乎不判 Unsafe）；**权重不随仓库分发，需要时用上面的 ModelScope 命令按需下载** |
| `FLOWMASK_L3_MODE=mock` | 不需要权重 | 启发式 | 测试 / 无权重演示 |

```powershell
# 1) 依赖 + 权重
python -m pip install torch transformers modelscope
python scripts/download_models.py --list        # 看有哪些权重、多大、给谁用（已下载的标 [ok]）
python scripts/download_models.py --only l3     # 只下 L3 指令裁判（3.0 GB，权重不随仓库分发）

# 2) 起服务（默认 127.0.0.1:8092；GPU 用 FLOWMASK_L3_DEVICE=cuda）
$env:FLOWMASK_L3_MODE = "instruct"
$env:FLOWMASK_L3_MODEL_DIR = "models/qwen2.5-1.5b-instruct"
cd server && python -m l3_service

# 3) 让一体机启用 L3
$env:FLOWMASK_ENABLE_L3 = "1"
$env:FLOWMASK_L3_ENDPOINT = "http://127.0.0.1:8092/v1/judge"
```

协同策略（`app/identify/pipeline.py`，全部可用环境变量改）：

- 只对 **L1 命中且分数落在灰区（0.25~0.85）、且不是直达高危** 的样本调用，触发面窄；
- **只升不降**，且**所有升级（含 Unsafe）默认封顶 L2**（`FLOWMASK_L3_MAX_TARGET=L3` 才允许升高危）；
- 采信指令模型的分级建议（`FLOWMASK_L3_TRUST_LEVEL_HINT=1`，默认开）；
- 类别感知（模型点出 PII 就抬到中危）**默认关**（`FLOWMASK_L3_CATEGORY_RAISE=1` 开启）；
- 服务超时/不可用 → `degraded=true`，保留 L1/L2 结论，不阻塞主链路。

**策略矩阵**（75 条策展子集、同一 L3 端点；`cd server && python -m l3_measure` 复现；扩样到 300 条后 L3 仍不改变分级，见 §5）：

| 口径 | 分级准确率 | Macro-F1 | 判错 | 结论 |
| --- | ---: | ---: | ---: | --- |
| 只认 Unsafe，封顶 L2（**当前默认**） | **98.7%** | 0.988 | 1 | 与 L1+L2 持平，不扣分 |
| 采信分级建议，封顶 L2 | **98.7%** | 0.988 | 1 | 同上 |
| 类别感知（PII → 至少中危） | 85.3% | 0.684 | 11 | name/email/address 类低危全被抬成中危，**关** |
| 类别感知 + 分级建议 | 85.3% | 0.684 | 11 | 同上 |
| 允许抬到 L3 | 84.0% | 0.671 | 12 | 连「诊断: 2型糖尿病」都被判 Unsafe/L3，**关** |

指令裁判真权重实测（策展 75 条子集，RTX 5060 8GB / CUDA bf16，单条约 1.0~1.3 s；首次调用含加载约 10 s。300 条扩样集上 L1+L2 与 L1+L2+L3 同为 **99.7%**）：

| 指标 | 数值 |
| --- | ---: |
| L3 触发（finding 级） | 47 次（评测集敏感样本密集；真实流量远低于此） |
| 判定分布 | Controversial 44 / Unsafe 2 / Safe 1；分级建议 L1 41 / L2 3 / L3 2 / SAFE 1 |
| 对分级的影响 | 0 次升/降级（默认口径），只贡献解释与证据 |
| 分级准确率 | 75 条子集 **98.7%**；300 条扩样集上 L1+L2 与 L1+L2+L3 同为 **99.7%**（L3 不扣分） |

> 4B 复核结论：Qwen3Guard-Gen-4B（8.56 GB 权重）同口径 Unsafe 仍为 0、Controversial 9→5、
> 平均延迟 640.8 ms→3413.7 ms（CPU bf16）。它是**内容安全**分类器，对中文 PII 只给
> Controversial/PII，换 4B 对分级零收益、更慢，因此不采用；要让 L3 真正贡献召回，
> 应按本域标注微调（可复用 `l2_training` 的弱标注/训练管线）或换更大指令模型。

## 5. 评测跑分

```powershell
cd server
python -m app.evaluate     # 默认 L1 only，几秒钟出结果
```

- 评测集：`eval/dataset.jsonl`（**300 条** = 策展 75 + 生成 225，覆盖 text/JSON/CSV/SQL、文档/测试示例负样本与困难样本；id/内容唯一、与训练集 0 重叠），可 `python eval/build_dataset.py` 复现；标注口径见 [`eval/README.md`](eval/README.md)；
- 报告：自动写入 [`docs/评测报告.md`](../docs/评测报告.md)——总体指标、四级混淆矩阵、分级/类别 P-R-F1、判错样本清单；
- 当前（L1 only，300 条）：分级准确率 **93.0%**、Macro-F1 **0.927**、类别 Micro-F1 **0.875**、高危漏检率 **0.0%**、L1 p95 **0.07 ms**；
- 对比多模型协同：`python -m app.evaluate --compare-l2 --compare-l3 --l2-endpoint http://127.0.0.1:8091/v1/classify`
  → L1 only **93.0%** / **L1+L2（真模型）99.7%**（安全误报率 28.6% → 0.0%，高危漏检率保持 0.0%；再加 L3 仍 99.7%）；
- 阈值消融：`FLOWMASK_L2_BENIGN_MIN=0.9` 会退回 L1-only 行为，可用于现场对比"接/不接模型"的收益；

### 接口一览（前缀 `/api/v1`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/`、`/ui/` | 根路径 307 跳到内置单页界面（左右对照 + 分级统计） |
| GET | `/health` | 健康检查（版本、时间、L2/L3 是否启用） |
| POST | `/detect` | 即时检测文本（不落库） |
| POST | `/detect/file` | 即时检测上传文件（multipart） |
| GET | `/formats` | 支持的格式、已接入/待接入、各格式是否可用 |
| GET | `/traffic/formats` | 流量导入支持的格式与限制（pcap/pcapng、上传上限） |
| POST | `/traffic/import` | 上传 pcap：解析 HTTP 正文并创建检测任务（201 + 任务 ID 与解析统计） |
| GET | `/traffic/sample` | 下载自带示例抓包 `demo-traffic.pcap`（3 条会话，含 L3/L2/SAFE） |
| POST | `/traffic/ingest` | 外部推送正文片段（Go 采集端/Agent 用），复用同一套识别与落库 |
| POST | `/traffic/live/start` | 开始实时跟随：`{pcap_path, interval_ms, window_s, ...}` → 返回会话（201） |
| GET | `/traffic/live` | 实时会话列表（报文/片段/窗口任务数、最近任务、最近错误） |
| POST | `/traffic/live/{live_id}/stop` | 停止跟随（会把未写完的窗口先落库） |
| DELETE | `/traffic/live` | 清理已停止的会话 |
| POST | `/tasks` | 上传文件建任务 |
| POST | `/tasks/text` | 粘贴文本建任务 |
| GET | `/tasks` / `/tasks/{id}` | 任务列表 / 详情 |
| GET | `/tasks/{id}/findings` | 任务结果（`level`/`page`/`page_size`） |
| GET | `/tasks/{id}/export?format=jsonl\|json\|csv\|sql` | 导出脱敏结果 |
| GET | `/findings` / `/findings/{id}` | 全量结果查询 |
| GET | `/stats` | 看板：分级分布、类别 TopN、评分直方图 |
| GET/POST/PATCH/DELETE | `/rules` `/rules/{id}` | 规则查询、新增、启停、删除（内置规则可启停不可删） |
| GET | `/audit` | 审计日志分页 |
| GET | `/audit/verify` | 校验哈希链（返回是否完整、断链位置） |
| GET | `/dynamic/policy` | 动态脱敏策略表（四级各自的未授权/授权行为） |
| POST | `/dynamic/render` | 动态脱敏渲染：`{text, authorized, mask_level?}`；每次渲染写审计链 |
| GET | `/audit/export?format=jsonl\|json\|csv` | 审计导出 |

鉴权：设置 `FLOWMASK_API_TOKEN` 后，`/api/v1/*` 需 `Authorization: Bearer <token>`；留空即本机演示模式。

## 6. 实时旁路（跟随 pcap 文件）

服务端**不直接抓网卡**：Npcap/libpcap 需要 C 绑定与管理员权限，会破坏"纯标准库、可移植、零外网"的部署口径。
抓包交给外部工具持续写文件，服务端只做「文件增长 → 增量解析 → 按窗口检测」：

```powershell
# 终端 1：起服务
python -m uvicorn app.main:app --port 8090

# 终端 2：模拟持续抓包（每 1s 追加一条含敏感正文的 HTTP 会话）
python samples/simulate_live_traffic.py --interval 1.0 --rounds 20

# 终端 3：开始跟随（也可以直接在 /app/ → 流量导入 → 实时旁路 点按钮）
curl.exe -s -X POST http://127.0.0.1:8090/api/v1/traffic/live/start ^
  -H "Content-Type: application/json" -d "{"pcap_path":"data/live.pcap","interval_ms":500,"window_s":2.0}"
```

真实场景把 `pcap_path` 指向 dumpcap 的滚动输出即可，例如：

```powershell
dumpcap -i 1 -w data/live.pcap -b filesize:10240
```

语义与边界：

- **窗口 = 一个任务**：`window_s` 到了就把这段时间新出现的正文汇总成一个检测任务，结果自动进「任务与审计链」「统计看板」，审计链照常逐条追加；
- **去重**：同一 HTTP 请求（方法+路径+正文）只检测一次，跨窗口重复的握手/重传不会重复报；
- **延迟**：最坏 = `interval_ms` + `window_s`，默认约 2.5 s；
- **不支持的**：pcapng 实时跟随（用文件导入）、HTTPS 密文、压缩体、分块传输；
- **权限**：只允许跟随 `data_dir` 下的文件（默认 `data/`），避免任意文件读取。

## 7. 配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `FLOWMASK_DATA_DIR` | `server/data` | 数据目录 |
| `FLOWMASK_DB_PATH` | `<data_dir>/flowmask.db` | SQLite 路径 |
| `FLOWMASK_API_TOKEN` | 空 | 非空则开启 Bearer 鉴权 |
| `FLOWMASK_AUDIT_SECRET` | `flowmask-demo-secret` | 审计链 HMAC 密钥（演示值，生产必须换） |
| `FLOWMASK_ENABLE_L2` | `false` | 是否升级 L2 语义识别 |
| `FLOWMASK_L2_ENDPOINT` | `http://127.0.0.1:8091/v1/classify` | BERT 推理服务地址 |
| `FLOWMASK_ENABLE_L3` | `false` | 是否启用 L3 大模型复核 |
| `FLOWMASK_L3_MODE` | `http` | `http` 调独立服务；`ollama` 直连 Ollama |
| `FLOWMASK_L3_ENDPOINT` | `http://127.0.0.1:8092/v1/judge` | L3 服务地址（`l3_service`） |
| `FLOWMASK_L3_MODEL` | `qwen3guard-gen-0.6b` | 模型名（仅展示/日志用） |
| `FLOWMASK_L3_MODEL_DIR` | `server/models/qwen3guard-gen-0.6b` | 本地权重目录（ModelScope 下载到此处；instruct 模式指向 `qwen2.5-1.5b-instruct`） |
| `FLOWMASK_L3_DEVICE` / `FLOWMASK_L3_DTYPE` | `auto` / `auto` | `cuda`/`cpu`；`auto`=GPU bf16、CPU fp32 |
| `FLOWMASK_L3_MAX_TARGET` | `L2` | L3 升级上限；设 `L3` 才允许抬到高危 |
| `FLOWMASK_L3_TRUST_LEVEL_HINT` | `1` | 是否采信指令模型的分级建议 |
| `FLOWMASK_L3_CATEGORY_RAISE` | `0` | 类别感知（点出 PII 即抬中危）；实测会显著降准确率，默认关 |
| `FLOWMASK_L2_TIMEOUT_S` / `FLOWMASK_L3_TIMEOUT_S` | `0.5` / `5.0` | 秒；超时即降级并标记 `degraded` |
| `FLOWMASK_MAX_UPLOAD_MB` | `20` | 上传大小上限 |
| `FLOWMASK_CORS_ORIGINS` | 本机 5173/8080 | 允许跨域的前端地址，逗号分隔 |

## 8. 代码结构

```text
server/
  app/
    main.py            FastAPI 入口（建库 → 规则启停 → 挂路由）
    core/
      config.py        Settings（环境变量，frozen dataclass + lru_cache）
      db.py            sqlite3 封装：tasks/findings/audit_log/rules 四张表 + 写锁 + WAL
    parsers/           输入分段：text/json/csv/sql/docx/pdf + registry 统一入口（PDF 依赖可选 pdfplumber）
    webui/
      index.html       自带单页界面：上传/粘贴 → 左原文高亮 / 右脱敏副本（无需前端工具链）
    identify/
      rules.py         L1 规则表（含校验算法、可启停）
      scoring.py       评分公式与阈值
      masking.py       四级脱敏模板
      types.py         Level/EntityType/Hit/Result 等基础类型
      l2.py            BERT 语义识别客户端（HTTP 契约 + 降级）
      l3.py            L3 客户端：调 `l3_service`（http）或直连 Ollama（ollama）
      operators.py     差分隐私 / l-多样性 / 泛化算子
      pipeline.py      编排：解析 → L1 → (L2) → (L3) → 分级 → 脱敏 → 证据打包
    tasks/
      service.py       后台任务执行、findings 落库、逐条写审计
      export.py        jsonl/json/csv/sql 导出
    audit/
      chain.py         HMAC 哈希链：追加、查询、校验
    api/
      deps.py          鉴权、操作人、流水线单例
      schemas.py       请求/响应模型
      routes_*.py      health/detect/tasks/findings/stats/rules/audit/dynamic/traffic
    traffic/
      pcap.py          pcap/pcapng 解析、write_pcap
      frames.py        以太网/IP/TCP/UDP → Frame（载荷定位）
      streams.py       会话聚合（TCP 重组）+ HTTP 正文拆分 + 内容类型识别
      importer.py      抓包 → Document（复用解析/识别/任务链路）
      live.py          实时旁路：增量跟随 pcap 文件 + 窗口落库
  l2_service/          L2 语义推理服务（mock / onnx 后端 + /v1/classify）
  l2_training/         自举数据集、弱标注、微调脚本
  l3_service/          L3 推理服务（mock / hf / instruct 后端 + /v1/judge”）
  samples/             自带示例抓包生成脚本与 demo-traffic.pcap
  eval/                评测集（300 条主集 + 200 条 NER 压力集）与评测说明
  tests/               256 个用例：识别、解析（含 Word/PDF）、管线、审计、API、界面、动态脱敏、L2/L3 服务、训练侧、评测（含数据集完整性）、流量导入、实时旁路、NER 探针、部署脚本
  pyproject.toml
  requirements.txt     运行依赖（固定版本）
  requirements-ml.txt  可选：L2/L3/NER/训练与导出
  requirements-dev.txt 开发测试依赖
  .env.example         环境变量模板（复制成 .env 后用）
  scripts/             一键启动/停止（start_local.ps1/.sh/.cmd、stop_local.*）
web/                   正式前端（Vue3 + Element Plus + ECharts，Vite 构建，产物挂 /app）
  src/views/           DetectView / TasksView / StatsView / RulesView / DynamicView / TrafficView
  src/api.js           接口封装（同源 /api/v1，开发态走 vite proxy 到 8090）
  src/highlight.js     命中区间高亮 + 脱敏副本渲染
```

## 9. 设计要点（答辩口径）

1. **原文不落库**：库里只存 `input_hash`（原文 SHA-256）、脱敏副本与哈希链字段；检索比对靠哈希，泄露风险最低。
2. **分级识别按成本升级**：L1 微秒~毫秒级全量跑；只有 L1 命中且分数落在 0.25~0.85 灰区的样本才送 L3 复核；L3 只在 `Unsafe` 时升级，`Controversial/Safe` 只记理由，避免小模型把低危抬成中危。L2/L3 均可在配置里关掉，关掉即「纯规则模式」，链路不阻塞。
3. **失败即降级**：L2/L3 超时或不可用 → 保留 L1 结论并置 `degraded=true`，不让模型成为单点故障。
4. **可复算可审计**：每条 finding 带 `rule_ids`、`confidence_vector`、`model_chain`、`reasons`；审计日志与 finding 一一对应，`prev_hash` 串成链，任何字段改动都会在 `/audit/verify` 暴露。
5. **模块化便于扩展**：解析器、识别器、算子、导出格式都是小函数/协议 + 注册表，新增格式或模型不需改主流程。

## 10. 性能基准（本机实测）

一次真实运行的样本，报告在 [`docs/性能报告.md`](../docs/性能报告.md)（由下面的命令直接生成，不手工填数）：

```powershell
python -m bench --skip-l2                        # 只测 L1 + 进程内 /detect
python -m bench --l3-endpoint http://127.0.0.1:8092/v1/judge --markdown-out ../docs/性能报告.md
```

| 链路 | 单条均值 | P95 | 吞吐 | 口径 |
| --- | ---: | ---: | ---: | --- |
| L1 规则识别（12 条规则 + 评分分级） | **0.022 ms** | 0.023 ms | ≈4.4 万条/秒 | 纯 Go 等价物在 Python 侧，无网络/模型 |
| 端到端 `POST /api/v1/detect` | **2.18 ms** | 2.42 ms | ≈459 请求/秒 | 进程内 HTTP（`TestClient`，不含浏览器开销） |
| L2 ONNX int8（batch=1 → 32） | 7.38 → **4.09 ms**/条 | 4.19 ms | ≈244 条/秒 | CPU 推理；批越大单条越便宜 |
| L3 指令裁判（Qwen2.5-1.5B） | **1.16 s** | 1.26 s | 0.86 条/秒 | RTX 5060 Laptop 8GB / CUDA，回环 HTTP |

测试机：AMD Ryzen 9 8940HX（32 线程）、31 GB 内存、Windows 11、Python 3.12.10，2026-10-20 实测。
诚实边界：L3 只复核 L1 灰区样本（默认关闭），全量走 L3 会因 1.16 s/条成为吞吐瓶颈 —— 这正是「三级协同、按需升级」的设计理由。

## 11. 部署与一键启动

见「2.1 一键启动」；下面只补充部署相关的约定。

### 11.1 依赖清单（三个文件，按需装）

| 文件 | 内容 | 什么时候装 |
| --- | --- | --- |
| `requirements.txt` | fastapi / uvicorn / pydantic / python-multipart / pdfplumber（均固定版本） | 必装：最小闭环（L1 规则 + 解析 + 任务/审计/API + 前端静态托管） |
| `requirements-ml.txt` | onnxruntime / torch / transformers / onnx / onnxscript / modelscope | 需要 L2 真模型、L3 真模型、NER 或重新训练/导出时 |
| `requirements-dev.txt` | `-r requirements.txt` + pytest / httpx | 跑测试、二次开发时 |

模型权重**不随仓库分发**（`server/models/`、`server/l2_models/` 都在 .gitignore 里），用
`python scripts/download_models.py --list` 看清单、`--only l3 ner` 按需下载（见 11.5 节）；
不下载也能跑，L2/L3/NER 关闭时链路自动降级为 L1-only 并标 `degraded`。

### 11.2 环境变量与 `.env`

```powershell
Copy-Item .env.example .env     # 然后按需改；启动脚本会自动加载
```

全部变量见 `.env.example`（含默认值与注释）。演示最常改的三个开关：`FLOWMASK_ENABLE_L2`、
`FLOWMASK_ENABLE_L3`、`FLOWMASK_ENABLE_NER`；`.env` 里留空的项会回落默认值（有测试覆盖）。

### 11.3 启动脚本参数（`scripts/start_local.ps1` / `.sh`）

| 参数（PowerShell） | 参数（bash） | 默认 | 说明 |
| --- | --- | --- | --- |
| `-Port` | `--port` | 8090 | 主服务端口 |
| `-BindHost` | `--host` | 127.0.0.1 | 监听地址（对外提供服务时改 0.0.0.0 并设置 `FLOWMASK_API_TOKEN`） |
| `-WithL2` | `--with-l2` | 关 | 起 `l2_service`（需 `l2_models/bert-sens-onnx`） |
| `-WithL3` | `--with-l3` | 关 | 起 `l3_service`（需 `models/qwen2.5-1.5b-instruct`） |
| `-L3Device` | `--l3-device` | cpu | L3 推理设备：cpu / cuda |
| `-EnableNer` | `--ner` | 关 | 起主服务时启用 NER（需 `models/bert4ner`） |
| `-NoBrowser` | `--no-browser` | 打开浏览器 | 不自动打开浏览器（服务器/CI 用） |

产物位置：`server/data/run/<name>.pid`、`<name>.log`、`<name>.err.log`；
`stop_local` 按 pid 文件停止并清理记录。

### 11.4 部署注意（答辩口径）

- **默认只监听回环**，模型与数据都在本机，满足「数据不出机」；对外暴露必须改 `FLOWMASK_API_TOKEN`
  与 `FLOWMASK_AUDIT_SECRET`（审计链密钥不要用示例值）。
- **Windows 执行策略**：直接 `\scripts\start_local.ps1` 可能被拦（`AuthorizationManager 检查失败`），
  用 `.cmd` 包装或 `powershell -ExecutionPolicy Bypass -File .\scripts\start_local.ps1`。
- **无模型降级路径**：不装 `requirements-ml.txt`、没有权重时，主服务照常可用，
  只是 L2/L3/NER 不参与（结果里 `degraded=true`）。
- **Tauri 桌面打包未做**（需 Rust 工具链）；给评委的 Windows 单文件 exe 见 11.6，双击即用。

### 11.5 按需下载模型权重（`scripts/download_models.py`）

权重不进仓库、也不打进 exe，用脚本按需拉取（本机 huggingface.co 不可直连，脚本默认走 ModelScope，
NER 权重走 hf-mirror）：

```powershell
cd server
python scripts/download_models.py --list          # 清单：名字/大小/落地目录/用途，已下载标 [ok]
python scripts/download_models.py --only l3 ner   # 常用组合：L3 指令裁判 + L1.5 NER（约 3.3 GB）
python scripts/download_models.py --all           # 全部 4 个目标（约 6.4 GB，含 L2 训练基座与护栏备选）
python scripts/download_models.py --only l3 --dry-run   # 只看会做什么，不真下
python scripts/download_models.py --all --dest D:\FlowMask\models   # 自定义落地目录
```

| 目标 | 大小 | 落地目录 | 用途 |
| --- | ---: | --- | --- |
| `l3` | 2955 MB | `models/qwen2.5-1.5b-instruct` | L3 解释与分级复核（`FLOWMASK_ENABLE_L3=1`） |
| `ner` | 388 MB | `models/bert4ner` | 自由文本人名/地址（请求级 `enable_ner`） |
| `l2-base` | 1632 MB | `models/bert-base-chinese` | 重训 L2 用；线上推理只需已导出的 ONNX |
| `l3-guard` | 1449 MB | `models/qwen3guard-gen-0.6b` | 护栏口径的 L3 备选后端 |

脚本行为：目标已完整（有 `config.json` + ≥1 MB 权重）就**跳过**，`--force` 可强制重下；
依赖缺失时给出 `pip install` 提示并以退出码 2 结束，不会留下半个目录当“已下载”。
L2 的 `l2_models/bert-sens-onnx` 是自训练产物、无法下载，需按 §3 重训导出或直接拷贝。

### 11.6 打包 Windows 单文件 exe（双击即用）

前端已构建的前提下，一条命令得到 `server/dist_exe/FlowMask.exe`（约 50 MB）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
# 已构建过前端可加 -SkipFrontend 跳过 npm run build
```

双击 exe 之后：

- 自动挑端口（8765 起，被占用顺延），起服务并用 **Edge/Chrome 的 app 模式**开一个无地址栏窗口（没有就回落默认浏览器）；
- 数据写 `%LOCALAPPDATA%\FlowMask\data`（不污染安装/解压目录），日志 `%LOCALAPPDATA%\FlowMask\logs\desktop.log`；
  若该目录被安全软件/组策略拒绝访问（WinError 5），自动回落为 **便携模式**：数据与日志放 exe 同级 `data/`、`logs/`；
- 常用参数：`--port`、`--data-dir`、`--enable-l2`、`--enable-l3`、`--enable-ner`、`--no-browser`（详见 `FlowMask.exe --help`）；
  双击没反应时先跑 `FlowMask.exe --self-check`，会在 exe 同级生成 `FlowMask-selfcheck.txt`（路径、可写性一目了然）。

模型权重**不打进 exe**（这是体积能压到 50 MB 的原因）：把 `l2_models/`、`models/` 放到 **exe 同级目录**即可被自动识别；
没有权重也能启动，链路自动降级为 L1-only（`degraded=true`）。

已知边界：exe 只跑**主服务**；L2/L3/NER 的 torch/onnxruntime 推理仍以 Python 进程单独启动（`python -m l2_service`），
以免把 CUDA/驱动版本耦合进 exe。打包用 PyInstaller（`flowmask.spec`），**不需要** Rust/Node 运行时。

## 12. 下一步

- **L2 真权重**：已用 bert-base-chinese 微调并导出 ONNX int8（`l2_models/bert-sens-onnx`，单条 10–33 ms）；权重不入库，重训命令见 `l2_training/README.md`；
- **LLM 敏感度调优**：L3 已接指令裁判（Qwen2.5-1.5B-Instruct），但零样本仍不足以稳定提升分级（见第 4 节矩阵）；下一步按本域标注微调 / 换更大指令模型；
- **NER 权重升级**：机制与前端开关已就绪（服务端开关 + 请求级 `enable_ner`），待更换更高质量中文 NER 权重/领域微调以提升结构化场景精度；
- **网卡直采（可选）**：实时旁路已支持跟随 pcap 文件（dumpcap/Wireshark/自研采集器写）；若要服务端直接抓网卡，需引入 Npcap/libpcap（管理员权限）作为可选增强；
- 交付侧：只保留开发与联调，答辩材料由他人负责。
