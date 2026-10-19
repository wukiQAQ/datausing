# FlowMask Server（FastAPI 一体机）

> 文件/文档侧的分级脱敏与审计溯源服务：**上传或粘贴 → 多模型分级识别 → 四级脱敏 → 落库留痕 → 哈希链审计**。
> 这是需求 v2（2026-10-19）的主链路实现；仓库里的 Go 采集端与 `cmd/flowmask` 保留为「网络流量旁路」扩展能力。

## 1. 现在能做什么

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 文本/JSON/CSV/SQL/Word/PDF 解析与分段定位 | ✅ | 文本整段；JSON 走 `$` 路径；CSV/TSV 每单元格 `row{n}.{列名}`；SQL 提取 DML 字符串字面量；Word 段落 `p{n}` / 表格 `table{表}.{行}.{列}`；PDF 按页 `page{n}`（依赖可选 `pdfplumber`） |
| pcap 流量导入（服务端） | ✅ | 上传 pcap/pcapng → TCP 重组 + HTTP 请求解析 → 每个正文当一个片段，复用同一套识别/分级/脱敏/任务/审计；`GET /traffic/sample` 提供自带示例（`samples/demo-traffic.pcap`）；HTTPS、压缩体与分块传输暂不解析 |
| L1 规则识别 | ✅ | 12 条规则 + 身份证 MOD 11-2 / 银行卡 Luhn / IPv4 校验 |
| 敏感度评分与四级分级 | ✅ | 加权最大 + 多标签共现修正 + 极端类别直达（L3/L2/L1/SAFE） |
| 四级脱敏 + 左右对照 | ✅ | L3 全屏蔽、L2 保留首字符+星号、L1 前置警示、SAFE 原文；输出「原文哈希 + 脱敏副本 + 命中区间（spans）」供前端高亮，原文不入库 |
| L2 语义识别（独立推理服务） | ✅ | 客户端+降级策略已接；`l2_service/` 提供 HTTP 服务（mock 后端已可跑通纠偏链路，真 RoBERTa 权重待接入） |
| L3 本地大模型复核 | 🚧 | 已实现 Ollama `/api/generate` 客户端 + JSON 输出解析 + 超时降级；模型（Qwen3Guard-Stream 0.6B）待接入 |
| 差分隐私 / l-多样性算子 | ✅ | Laplace 噪声、数值泛化、l-多样性检查（接口化，供动态脱敏演示） |
| 后台任务 + 结果落库 | ✅ | 线程执行、`tasks`/`findings` 表、按级别/类型查询 |
| 审计哈希链 | ✅ | HMAC-SHA256 链式签名，可校验、可导出；篡改/删除能定位到断链条目 |
| 统计看板数据 | ✅ | 分级分布、类别 TopN、评分直方图、任务数 |
| 单页可视化（左右对照） | ✅ | `GET /ui/` 内置页面：上传/粘贴 → 左原文高亮、右脱敏副本、分级统计，无需装前端工具链 |
| 正式前端（Vue3 + Element Plus + ECharts） | ✅ | `web/`：6 个页面（即时检测·左右对照 / 流量导入·pcap / 任务与审计链 / 统计看板 / 识别规则 / 动态脱敏），`npm run build` 后由服务端挂在 `/app`；未构建时自动回落到 `/ui/` |
| 自定义规则 | ✅ | 增删启停 + 正则合法性校验，库内持久化 |
| 评测跑分 | ✅ | 自建标注集 75 条；L1 only 准确率 89.3%，接 L2 后 98.7%、安全误报率 25.9%→0.0%，`--compare-l2` 一条命令复现 |
| 动态脱敏视图（授权切换） | ✅ | `POST /api/v1/dynamic/render`：未授权返回脱敏副本、授权返回原文并写审计链；`GET /dynamic/policy` 给出四级策略表；内置页面「动态脱敏」页签可直接演示 |
| L2 训练侧（RoBERTa 多标签） | ✅ | `l2_training/`：11 标签（10 敏感 + benign）、自举数据生成（875 条，含评测集并入）、数据体检、微调脚本、`labels.json` 契约；真权重训练需本机装 torch（8GB 显存够） |

## 2. 快速开始

环境：Python 3.11+（本机 3.12.10）。依赖只有 FastAPI 全家桶，存储用标准库 `sqlite3`（无 ORM、无 CGO）。

```powershell
cd server
pip install -e ".[dev]"        # 或 pip install fastapi uvicorn python-multipart pytest httpx
python -m pytest -q            # 119 个用例
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

# 终端 B：让一体机启用 L2
$env:FLOWMASK_ENABLE_L2 = "1"
$env:FLOWMASK_L2_ENDPOINT = "http://127.0.0.1:8091/v1/classify"
python -m uvicorn app.main:app --port 8090
```

协同规则（`app/identify/pipeline.py`）：只对 L1 命中的样本升级；L2 补类别加少量分；
`benign≥0.75` 时压分抑制"文档/测试示例"这类形似误报（证件等直达类要 ≥0.90 才压，且取消 `direct`）；
服务异常只标记 `degraded`，结论继续用 L1。实测增益：

| 指标 | L1 only | L1 + L2(mock) |
| --- | ---: | ---: |
| 分级准确率 | 89.3% | **98.7%** |
| 安全误报率 | 25.9% | **0.0%** |
| 高危漏检率 | 0.0% | 0.0% |

## 4. 评测跑分

```powershell
cd server
python -m app.evaluate     # 默认 L1 only，几秒钟出结果
```

- 评测集：`eval/dataset.jsonl`（67 条，覆盖 text/JSON/CSV/SQL 与困难样本），标注口径见 [`eval/README.md`](eval/README.md)；
- 报告：自动写入 [`docs/评测报告.md`](../docs/评测报告.md)——总体指标、四级混淆矩阵、分级/类别 P-R-F1、判错样本清单；
- 当前（L1 only）：分级准确率 **98.5%**、Macro-F1 **0.987**、类别 Micro-F1 **0.993**、高危漏检率 **0.0%**、L1 p95 **0.11 ms**；
- 接入 L2/L3 后加 `--enable-l2 --enable-l3` 复跑，即可对比"多模型协同"的增益与代价。

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

## 5. 配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `FLOWMASK_DATA_DIR` | `server/data` | 数据目录 |
| `FLOWMASK_DB_PATH` | `<data_dir>/flowmask.db` | SQLite 路径 |
| `FLOWMASK_API_TOKEN` | 空 | 非空则开启 Bearer 鉴权 |
| `FLOWMASK_AUDIT_SECRET` | `flowmask-demo-secret` | 审计链 HMAC 密钥（演示值，生产必须换） |
| `FLOWMASK_ENABLE_L2` | `false` | 是否升级 L2 语义识别 |
| `FLOWMASK_L2_ENDPOINT` | `http://127.0.0.1:8091/v1/classify` | BERT 推理服务地址 |
| `FLOWMASK_ENABLE_L3` | `false` | 是否启用 L3 大模型复核 |
| `FLOWMASK_L3_ENDPOINT` | `http://127.0.0.1:11434/api/generate` | Ollama 地址 |
| `FLOWMASK_L3_MODEL` | `qwen3guard-stream-0.6b` | 模型名 |
| `FLOWMASK_L2_TIMEOUT_S` / `FLOWMASK_L3_TIMEOUT_S` | `0.5` / `5.0` | 秒；超时即降级并标记 `degraded` |
| `FLOWMASK_MAX_UPLOAD_MB` | `20` | 上传大小上限 |
| `FLOWMASK_CORS_ORIGINS` | 本机 5173/8080 | 允许跨域的前端地址，逗号分隔 |

## 6. 代码结构

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
      l3.py            Ollama 大模型复核（Qwen3Guard 风格）
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
      routes_*.py      health/detect/tasks/findings/stats/rules/audit
  tests/               108 个用例：识别、解析（含 Word/PDF）、管线、审计、API、界面、动态脱敏、L2 服务/训练侧、评测
  pyproject.toml
web/                   正式前端（Vue3 + Element Plus + ECharts，Vite 构建，产物挂 /app）
  src/views/           DetectView / TasksView / StatsView / RulesView / DynamicView
  src/api.js           接口封装（同源 /api/v1，开发态走 vite proxy 到 8090）
  src/highlight.js     命中区间高亮 + 脱敏副本渲染
```

## 7. 设计要点（答辩口径）

1. **原文不落库**：库里只存 `input_hash`（原文 SHA-256）、脱敏副本与哈希链字段；检索比对靠哈希，泄露风险最低。
2. **分级识别按成本升级**：L1 微秒~毫秒级全量跑；只有 L1 模糊（分数落在 0.25~0.85 灰区）才升级 L2；只有 L2 仍不确定才送 L3。L2/L3 均可在配置里关掉，关掉即「纯规则模式」，链路不阻塞。
3. **失败即降级**：L2/L3 超时或不可用 → 保留 L1 结论并置 `degraded=true`，不让模型成为单点故障。
4. **可复算可审计**：每条 finding 带 `rule_ids`、`confidence_vector`、`model_chain`、`reasons`；审计日志与 finding 一一对应，`prev_hash` 串成链，任何字段改动都会在 `/audit/verify` 暴露。
5. **模块化便于扩展**：解析器、识别器、算子、导出格式都是小函数/协议 + 注册表，新增格式或模型不需改主流程。

## 8. 下一步

- 阶段二：L2 BERT 推理服务（`/v1/classify`）、评测集与指标（F1/分级准确率）、动态脱敏接 UI、看板图表增强。
- 阶段三：动态脱敏（差分隐私/l-多样性开关）、pcap 旁路恢复、答辩材料与视频。
