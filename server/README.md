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
| L2 语义识别（独立推理服务） | ✅ | `l2_service/` HTTP 服务：mock / ONNX int8 / auto 三后端；已用 bert-base-chinese 微调模型实测（单条 10–33 ms，CPU） |
| L3 本地大模型复核 | ✅ | 独立 `l3_service` 双后端：**指令裁判**（Qwen2.5-1.5B-Instruct，按本项目四级标准直出 `level/reason/categories`，默认推荐）或**护栏**（Qwen3Guard-Gen-0.6B，Safe/Controversial/Unsafe）；只复核 L1 灰区、只升不降、默认封顶 L2，异常即降级 |
| 差分隐私 / l-多样性算子 | ✅ | Laplace 噪声、数值泛化、l-多样性检查（接口化，供动态脱敏演示） |
| 后台任务 + 结果落库 | ✅ | 线程执行、`tasks`/`findings` 表、按级别/类型查询 |
| 审计哈希链 | ✅ | HMAC-SHA256 链式签名，可校验、可导出；篡改/删除能定位到断链条目 |
| 统计看板数据 | ✅ | 分级分布、类别 TopN、评分直方图、任务数 |
| 单页可视化（左右对照） | ✅ | `GET /ui/` 内置页面：上传/粘贴 → 左原文高亮、右脱敏副本、分级统计，无需装前端工具链 |
| 正式前端（Vue3 + Element Plus + ECharts） | ✅ | `web/`：6 个页面（即时检测·左右对照 / 流量导入·pcap / 任务与审计链 / 统计看板 / 识别规则 / 动态脱敏），`npm run build` 后由服务端挂在 `/app`；未构建时自动回落到 `/ui/` |
| 自定义规则 | ✅ | 增删启停 + 正则合法性校验，库内持久化 |
| 评测跑分 | ✅ | 自建标注集 75 条；L1 only 准确率 89.3%，接 L2 后 98.7%、安全误报率 25.9%→0.0%，`--compare-l2` 一条命令复现 |
| 动态脱敏视图（授权切换） | ✅ | `POST /api/v1/dynamic/render`：未授权返回脱敏副本、授权返回原文并写审计链；`GET /dynamic/policy` 给出四级策略表；内置页面「动态脱敏」页签可直接演示 |
| L2 训练侧（RoBERTa 多标签） | ✅ | `l2_training/`：11 标签（10 敏感 + benign）、自举数据（默认 800 条，含 30% 困难负例）、弱标注、数据体检、微调 + ONNX 导出；本机真模型 macro-F1 **0.9844**、benign F1 **0.954** |

## 2. 快速开始

环境：Python 3.11+（本机 3.12.10）。依赖只有 FastAPI 全家桶，存储用标准库 `sqlite3`（无 ORM、无 CGO）。

```powershell
cd server
pip install -e ".[dev]"        # 或 pip install fastapi uvicorn python-multipart pytest httpx
python -m pytest -q            # 174 个用例
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
`FLOWMASK_L2_BENIGN_DIRECT_MIN`），默认值是在 75 条留出集上调出来的：误报 25.9%→0%，高危漏检保持 0%。

实测增益（真模型，75 条评测集，训练数据与评测集隔离）：

| 指标 | L1 only | L1 + L2（真模型） |
| --- | ---: | ---: |
| 分级准确率 | 89.3% | **98.7%** |
| 安全误报率 | 25.9% | **0.0%** |
| 高危漏检率 | 0.0% | 0.0% |

> 训练/导出细节（困难负例、`labels.json` 回退、`dynamo=False`、padding mask）见
> [`l2_training/README.md`](l2_training/README.md) 与 [`l2_service/README.md`](l2_service/README.md)。

## 4. L3 大模型复核（指令裁判 / 护栏双后端）

第三级识别跑在独立进程里，职责是「复核 + 解释」，不重新定级。两种后端按需选：

| 模式 | 权重 | 输出 | 说明 |
| --- | --- | --- | --- |
| `FLOWMASK_L3_MODE=instruct`（**推荐**） | Qwen2.5-1.5B-Instruct（3.0 GB） | 一行 JSON：`{"level":"L3\|L2\|L1\|SAFE","categories":[...],"reason":"…"}` | 按本项目四级标准直接给分级建议与中文理由 |
| `FLOWMASK_L3_MODE=hf` | Qwen3Guard-Gen-0.6B（1.5 GB） | `Safe / Controversial / Unsafe` | 内容安全口径，对中文 PII 偏保守（几乎不判 Unsafe） |
| `FLOWMASK_L3_MODE=mock` | 不需要权重 | 启发式 | 测试 / 无权重演示 |

```powershell
# 1) 依赖 + 权重（国内走 ModelScope）
python -m pip install torch transformers modelscope
python -c "from modelscope import snapshot_download; print(snapshot_download('Qwen/Qwen2.5-1.5B-Instruct', local_dir='models/qwen2.5-1.5b-instruct'))"
python -c "from modelscope import snapshot_download; print(snapshot_download('Qwen/Qwen3Guard-Gen-0.6B', local_dir='models/qwen3guard-gen-0.6b'))"

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

**策略矩阵**（同一 75 条评测集、同一 L3 端点；`cd server && python -m l3_measure` 复现）：

| 口径 | 分级准确率 | Macro-F1 | 判错 | 结论 |
| --- | ---: | ---: | ---: | --- |
| 只认 Unsafe，封顶 L2（**当前默认**） | **98.7%** | 0.988 | 1 | 与 L1+L2 持平，不扣分 |
| 采信分级建议，封顶 L2 | **98.7%** | 0.988 | 1 | 同上 |
| 类别感知（PII → 至少中危） | 85.3% | 0.684 | 11 | name/email/address 类低危全被抬成中危，**关** |
| 类别感知 + 分级建议 | 85.3% | 0.684 | 11 | 同上 |
| 允许抬到 L3 | 84.0% | 0.671 | 12 | 连「诊断: 2型糖尿病」都被判 Unsafe/L3，**关** |

指令裁判真权重实测（75 条评测集，RTX 5060 8GB / CUDA bf16，单条约 1.0~1.3 s；首次调用含加载约 10 s）：

| 指标 | 数值 |
| --- | ---: |
| L3 触发（finding 级） | 47 次（评测集敏感样本密集；真实流量远低于此） |
| 判定分布 | Controversial 44 / Unsafe 2 / Safe 1；分级建议 L1 41 / L2 3 / L3 2 / SAFE 1 |
| 对分级的影响 | 0 次升/降级（默认口径），只贡献解释与证据 |
| 分级准确率 | 仍为 **98.7%**（L1 + L2 提供；L3 不扣分） |

> 4B 复核结论：Qwen3Guard-Gen-4B（8.56 GB 权重）同口径 Unsafe 仍为 0、Controversial 9→5、
> 平均延迟 640.8 ms→3413.7 ms（CPU bf16）。它是**内容安全**分类器，对中文 PII 只给
> Controversial/PII，换 4B 对分级零收益、更慢，因此不采用；要让 L3 真正贡献召回，
> 应按本域标注微调（可复用 `l2_training` 的弱标注/训练管线）或换更大指令模型。

## 5. 评测跑分

```powershell
cd server
python -m app.evaluate     # 默认 L1 only，几秒钟出结果
```

- 评测集：`eval/dataset.jsonl`（**75 条**，覆盖 text/JSON/CSV/SQL、文档/测试示例负样本与困难样本），标注口径见 [`eval/README.md`](eval/README.md)；
- 报告：自动写入 [`docs/评测报告.md`](../docs/评测报告.md)——总体指标、四级混淆矩阵、分级/类别 P-R-F1、判错样本清单；
- 当前（L1 only）：分级准确率 **89.3%**、Macro-F1 **0.928**、高危漏检率 **0.0%**、L1 p95 **0.12 ms**；
- 对比多模型协同：`python -m app.evaluate --compare-l2 --compare-l3 --l2-endpoint http://127.0.0.1:8091/v1/classify`
  → L1 only 89.3% / **L1+L2（真模型）98.7%**（安全误报率 25.9% → 0.0%，高危漏检率保持 0.0%）；
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
  eval/                评测集（75 条）与评测说明
  tests/               174 个用例：识别、解析（含 Word/PDF）、管线、审计、API、界面、动态脱敏、L2/L3 服务、训练侧、评测、流量导入、实时旁路
  pyproject.toml
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

## 10. 下一步

- **L2 真权重**：训练脚本与数据集就绪（`l2_training`，875 条自举数据），等安装 torch 后跑通微调并导出 ONNX；
- **LLM 敏感度调优**：L3 已接指令裁判（Qwen2.5-1.5B-Instruct），但零样本仍不足以稳定提升分级（见第 4 节矩阵）；下一步按本域标注微调 / 换更大指令模型；
- **网卡直采（可选）**：实时旁路已支持跟随 pcap 文件（dumpcap/Wireshark/自研采集器写）；若要服务端直接抓网卡，需引入 Npcap/libpcap（管理员权限）作为可选增强；
- 交付侧：只保留开发与联调，答辩材料由他人负责。
