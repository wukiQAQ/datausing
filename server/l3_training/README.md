# L3 训练侧：指令微调做「分级裁判」

L2 训练的是一个**分类器**（这段文本有没有敏感信息、属于哪几类）；
L3 训练的是一个**裁判**（按本项目四级标准给出分级、类别与一句理由）。

- 输出契约与 [`../l3_service/README.md`](../l3_service/README.md) 的 `instruct` 模式完全一致：
  同一段 `INSTRUCT_PROMPT`、同一行 JSON `{"level","categories","reason"}`；
- 因此微调产物可以直接被 `FLOWMASK_L3_MODE=instruct` 加载，**线上代码零改动**，
  用 `l3_measure.py` / `app.evaluate --l3-endpoint` 就能量化"微调前 vs 微调后"。

## 1. 数据来源

| 来源 | 说明 |
| --- | --- |
| 评测集金标 | `eval/dataset.jsonl`（351 条）：级别取 `level`，类别取 `types`，理由由模板生成（不走模型，避免幻觉） |
| 公开语料 | 需要更大规模时把 `weibo_ner` 等样本按同样方式包装（见 `dataset.py` 的 `sample_from_row`） |

构建命令（不需要 GPU / torch）：

```powershell
cd server
python -m l3_training dataset          # → data/l3_training.jsonl（train/val 已划分）
python -m l3_training inspect          # 级别/类别分布画像
python -m l3_training dry-run          # 数据体检：缺级别、目标不可解析都会报出来
```

样本长这样（`text` 是原文，`target` 是训练目标）：

```json
{"id":"L3-001","text":"客户身份证 11010519491231002X，手机 13800138000",
 "target":"{\"level\":\"L3\",\"categories\":[\"phone\",\"id_card\"],\"reason\":\"命中手机号、身份证号；强敏感标识（证件号/银行卡/密钥/病历），泄露可直接定位自然人\"}",
 "level":"L3","categories":["phone","id_card"],"source":"curated","split":"train"}
```

## 2. LoRA 微调

依赖（可选）：

```powershell
pip install -r requirements-ml.txt
pip install peft
```

训练 + 合并：

```powershell
# 1) LoRA 微调（8GB 显存够跑 1.5B：batch 1 + 梯度累积 8 + max_length 1024）
python -m l3_training train --epochs 2 --lora-r 8 --lora-alpha 16

# 2) 合并成完整模型目录（l3_service 不能直接加载 adapter）
python -m l3_training merge --out models/qwen2.5-1.5b-sens
```

产物：

```
l3_models/qwen2.5-1.5b-sens/     LoRA adapter + tokenizer + metrics.json
models/qwen2.5-1.5b-sens/        合并后的完整权重（喂给 l3_service）
```

## 3. 接回推理与评测

```powershell
# 用微调后的权重起 L3 服务
$env:FLOWMASK_L3_MODE='instruct'
$env:FLOWMASK_L3_MODEL_DIR='D:\dsh-project\datausing\server\models\qwen2.5-1.5b-sens'
$env:FLOWMASK_L3_DEVICE='cuda'
python -m l3_service

# 对比微调前后（同一套 351 条评测集）
python -m app.evaluate --compare-l2 --compare-l3 `
  --l2-endpoint http://127.0.0.1:8091/v1/classify `
  --l3-endpoint http://127.0.0.1:8092/v1/judge
```

## 4. 设计要点与边界

- **理由用模板生成**：训练目标里的 `reason` 由金标类别拼出来，不是模型自述，避免把幻觉当监督信号；
- **只在目标 JSON 上算损失**：prompt 部分 `labels=-100`，模型学的是"怎么输出判定"而不是复述 rubric；
- **长文本保留尾部**：截断时保留 prompt 结尾（正文在最后），避免把要判的内容切掉；
- **现状**：默认仍用基座 + 保守策略（只升不降、封顶 L2）。微调的价值在「自由文本 / 边缘样本」，
  结构化数据上 L1+L2 已经 99.7%，L3 再涨空间很小——评测要分场景看，别拿整体准确率掩盖单类召回。
