# l3_service · 本地大模型护栏推理服务

FlowMask 的第三级识别：对 L1 命中的**模糊样本**做一次"语义复核 + 自然语言解释"。
默认加载 Qwen3Guard-Gen-0.6B（约 1.2 GB，CPU 可跑），输出三档安全判定：

| 判定 | 映射级别 | 含义 |
| --- | --- | --- |
| `Unsafe` | L3 高危 | 明确包含敏感信息，建议全屏蔽 |
| `Controversial` | L2 中危 | 疑似敏感，需人工复核 |
| `Safe` | SAFE | 语义上不构成敏感信息 |

> 设计取舍：**L3 不重新分级，只在高危方向升级**。流水线里只有 `Unsafe` 会把级别抬到 L3，
> `Controversial`/`Safe` 只记录理由与证据——避免小模型把低危样本误抬成中危（实测会把
> 分级准确率从 98.7% 拉到 94.7%）。

## 快速开始

```powershell
cd server

# 1) 装依赖（CPU 版 torch 也够：本服务是单条推理）
python -m pip install torch transformers modelscope

# 2) 下模型（国内走 ModelScope，别用 HuggingFace）
python -c "from modelscope import snapshot_download; print(snapshot_download('Qwen/Qwen3Guard-Gen-0.6B', local_dir='models/qwen3guard-gen-0.6b'))"

# 3) 起服务（默认 127.0.0.1:8092）
python -m l3_service

# 4) 冒烟
curl.exe -s -X POST http://127.0.0.1:8092/v1/judge -H "Content-Type: application/json" -d "{\"text\":\"客户张三 身份证 11010519491231002X\"}"
```

主服务侧把 L3 指向它即可（`.env` / 环境变量）：

```powershell
$env:FLOWMASK_ENABLE_L3 = "1"
$env:FLOWMASK_L3_MODE = "http"                    # 默认值
$env:FLOWMASK_L3_ENDPOINT = "http://127.0.0.1:8092/v1/judge"
```

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 后端就绪状态、模型名、模式 |
| GET | `/v1/model` | 模型元信息 |
| POST | `/v1/judge` | `{"text": "..."}` → `{"risk","level","reason","model","degraded","latency_ms"}` |

任何后端异常都返回 `200 + degraded=true`（附 `reason`），**不让推理故障变成主链路故障**。

## 后端模式（`FLOWMASK_L3_MODE`）

| 模式 | 说明 |
| --- | --- |
| `hf` | 本地 transformers 加载权重（默认 `models/qwen3guard-gen-0.6b`） |
| `mock` | 零依赖启发式，用于开发/CI 与演示降级 |
| `echo` | 固定返回，用于契约测试 |
| `auto` | 有本地权重走 `hf`，否则回退 `mock` |

模型目录可用 `FLOWMASK_L3_MODEL_DIR` 覆盖，设备用 `FLOWMASK_L3_DEVICE`（`cpu` 默认）。

## 输出解析

Qwen3Guard 的生成结果不是稳定 JSON，`parse_guard_text()` 兼容三种写法：

1. 纯 JSON：`{"risk": "Unsafe", "reason": "..."}`
2. 代码块包裹：```` ```json ... ``` ````
3. 原生行格式：

   ```text
   Safety: Unsafe
   Categories: PII
   ```

解析失败按 `Controversial` 处理并标 `degraded=true`——宁可疑也不放过，但把不确定性暴露出来。

## 延迟与开销

| 项 | 实测/预估 |
| --- | --- |
| 冷启动（加载权重） | 5~15 s（一次） |
| 单条推理（CPU，≤512 字） | 0.6~2 s |
| 显存 | 0（CPU）/ 约 1.5 GB（GPU） |
| 触发比例 | 只对 L1 命中且分数落在 0.25~0.85 的样本调用 |

因为触发面窄，L3 不参与"全量扫描"，只承担**解释与兜底**这两个职责。

## 指令裁判模式（instruct，推荐）

Qwen3Guard 这类护栏模型是「内容安全」口径（暴力/仇恨/越狱），对中文 PII 只会模糊地给
`Controversial`，无法直接服务本项目的四级分级。`instruct` 模式改用**指令模型当分级裁判**：
按写好的评分细则直接输出一行 JSON，既给级别建议也给中文理由与命中类别。

```powershell
$env:FLOWMASK_L3_MODE = "instruct"
$env:FLOWMASK_L3_MODEL_DIR = "models/qwen2.5-1.5b-instruct"   # ModelScope 下载
$env:FLOWMASK_L3_DEVICE = "cuda"                              # CPU 也能跑，只是慢
python -m l3_service                                          # 127.0.0.1:8092
```

- 输出契约：`{"level":"L3|L2|L1|SAFE","categories":["phone","id_card","..."],"reason":"一句话中文理由"}`
  → 归一化为 `risk`（L3→Unsafe、L2/L1→Controversial、SAFE→Safe）+ `level` 建议；
- 真权重实测（RTX 5060 8GB / CUDA bf16）：单条约 1.0~1.3 s；强敏感 PII 文本判 `Unsafe/L3`，
  「文档示例/仅用于联调」判 `Safe/SAFE`，只含姓名+地址判 `Controversial/L1`；
- 两个实现细节：① 判定结果按文本内容缓存（512 条），策略矩阵反复评测时不会重复推理；
  ② prompt 里 JSON 示例的花括号必须转义（`.format` 会把 `{"name"...}` 当成占位符，
  否则报 `KeyError: '"name"'`）。

> 是否让 L3 影响最终分级由主服务的策略决定（默认「只升不降 + 封顶 L2」，见 `server/README.md` 第 4 节）；
> 本服务只负责「给判定 + 给理由」。
