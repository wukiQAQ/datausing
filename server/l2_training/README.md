# L2 训练侧（RoBERTa/BERT 多标签）

L1 只能看"长得像不像"，L2 要看"这段文本到底是不是在处理敏感信息"，并专门识别
**示例/测试/占位数据**（`benign`），把企业内部文档里最常见的一类误报压下去。

本目录负责：**标签体系 → 数据构造 → 微调 → 导出 HuggingFace 模型**。
训练产物交给 `l2_service.export_onnx` 压成 int8 ONNX，再以 `FLOWMASK_L2_MODE=onnx` 接进服务。

## 1. 标签

| 标签 | 含义 |
| --- | --- |
| `phone` / `id_card` / `bank_card` / `email` / `ip` | 与 L1、存储层完全一致的类别键 |
| `secret` / `password` | 密钥、凭据、口令 |
| `name` / `address` / `medical` | 姓名、地址、医疗健康 |
| `benign` | 正常内容，或"带示例标记的演练数据"（关键：抑制误报） |

顺序固定在 `labels.py:LABELS`，导出时写成 `labels.json`，推理端不猜顺序。

## 2. 数据从哪来

20 天周期里不可能从零标几千条，因此分三层：

1. **自举合成**（`python -m l2_training dataset`）：模板 + 合法校验位生成，
   默认 800 条，覆盖 10 类 + benign + 组合样本，用于把训练/导出/推理链路先跑通；
2. **人工评测集转训练**（`--with-eval eval/dataset.jsonl`）：75 条金标直接并入，
   SAFE 行自动作为 `benign` 正例；
3. **公开语料**（有时间再加，需人工抽查）：SenResume / MSRA 命名实体 / Resume / Weibo，
   清洗后落成同样的 jsonl 即可混训。

> 合规提示：所有合成样本里的号码都是算法生成的假数据（身份证走 MOD 11-2、银行卡走 Luhn），
> 不含任何真实个人信息。

### 模型权重（HuggingFace 不可达时走魔搭）

```powershell
pip install modelscope
modelscope download --model google-bert/bert-base-chinese --local_dir models/bert-base-chinese
# 或中文 RoBERTa（效果更好、显存需求相近）
modelscope download --model hfl/chinese-roberta-wwm-ext --local_dir models/chinese-roberta-wwm-ext
```

`--base-model models/bert-base-chinese` 可以直接指向本地目录。

## 3. 三步跑通

```powershell
cd server

# ① 造数据（自举 800 + 评测集 75）并体检（不需要 torch）
python -m l2_training dataset --out data/l2_training.jsonl --count 800
python -m l2_training inspect --dataset data/l2_training.jsonl

# ② 微调（需要 torch + transformers；8GB 显存跑 bert-base-chinese 绰绰有余）
pip install torch --index-url https://download.pytorch.org/whl/cu128
pip install transformers
python -m l2_training train `
  --dataset data/l2_training.jsonl `
  --base-model models/chinese-roberta-wwm-ext `
  --out l2_models/roberta-wwm-sens --epochs 3 --batch-size 16 --lr 2e-5

# ③ 导出 int8 ONNX 并接进服务
python -m l2_service.export_onnx --model-dir l2_models/roberta-wwm-sens --out l2_models/roberta-wwm-sens-onnx
$env:FLOWMASK_L2_MODE = "onnx"
$env:FLOWMASK_L2_MODEL_DIR = "l2_models/roberta-wwm-sens-onnx"
python -m l2_service                      # 127.0.0.1:8091
$env:FLOWMASK_ENABLE_L2 = "1"             # 主服务打开 L2，走 /v1/classify 协同
python -m uvicorn app.main:app --port 8090
```

## 4. 数据格式

一行一条 JSON：

```json
{"text": "客户张三，身份证 11010519491231002X（工单号 123456）", "labels": {"id_card": true, "benign": false}, "source": "synthetic"}
```

要求：

- `labels` 必须包含 `labels.py:LABELS` 的全部键（缺省按 `false` 处理）；
- **不能有标签全为 false 的样本**——"完全正常"的文本请标 `benign: true`；
- 文本建议 ≤ 512 字（训练 `max_length=128` token，超长会截断）。

## 5. 训练与指标口径

- 任务形式：多标签 sigmoid 分类（`BCEWithLogitsLoss`），不是 softmax 单标签；
- 每轮在验证集上算 **per-label P/R/F1、Macro-F1、Micro-F1、完全匹配率**，保存 Macro-F1 最优轮；
- 阈值默认 0.5，运行时 `l2_service` 与 `pipeline` 用同一阈值（`L2_MIN_CONFIDENCE=0.70` 用于"采纳建议"，
  详见 `app/identify/pipeline.py`）；
- 产物：`pytorch_model.bin`/`model.safetensors` + `tokenizer.json` + `labels.json` + `metrics.json`。

真实数据上预期的量级（供答辩时给区间，不要当承诺）：L2 单条 CPU int8 推理 5–20ms，
在 75 条人工评测集上 Macro-F1 目标 ≥ 0.90；实际写报告时以重新评测的数字为准。

## 6. 与在线服务的关系

```text
l2_training（离线）  →  l2_models/*（HF 格式）  →  export_onnx  →  l2_models/*-onnx
                                                                    ↑
                                            FLOWMASK_L2_MODE=onnx 加载
                                                                    ↑
l2_service（/v1/classify） ← HTTP ← app/identify/l2.py ← pipeline（只复核 L1 命中/高危样本）
```

没有训练好的权重时，`FLOWMASK_L2_MODE=mock`（默认）用词典启发式返回同类结构，
整条协同链路、评测脚本与页面都能先跑通——权重到位只换后端，不改上层。
