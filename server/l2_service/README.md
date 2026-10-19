# FlowMask L2 语义识别服务

> 独立进程的多标签语义识别服务：给定一段文本，返回**命中的敏感类别及置信度**，
> 以及**"这段像不像文档/测试示例"的置信度**（`benign`）。FlowMask 一体机按 HTTP 契约调用它，
> 用来做两件事：**补 L1 漏掉的语义类别**、**压掉 L1 被形状骗到的误报**。

## 1. 为什么要独立进程

| 理由 | 说明 |
| --- | --- |
| 体积 | 真模型（tokenizer + int8 权重）几十到几百 MB，不该塞进一体机镜像 |
| 依赖 | onnxruntime/torch 与 Web 服务的依赖树不同，混装容易把演示环境搞坏 |
| 演示 | 一体机没有模型也能跑 L1-only；答辩现场把 L2 服务启起来，当场演示"升级到 L2"的增益 |
| 降级 | 服务超时/挂掉，一体机标记 `degraded=True` 继续跑，推理服务不是单点 |

## 2. 快速开始（mock 后端，零模型依赖）

```powershell
cd server
python -m l2_service                       # 默认 127.0.0.1:8091
curl.exe http://127.0.0.1:8091/health
```

```powershell
# 单条/批量分类（批量上限 32）
$body = '{"items":[{"id":"1","content":"客户张伟住在福建省福州市鼓楼区五四路100号"}]}'
curl.exe -s -X POST http://127.0.0.1:8091/v1/classify -H "Content-Type: application/json" -d $body
```

返回：

```json
{
  "model": "mock-lexicon",
  "degraded": false,
  "latency_ms": 0.3,
  "items": [{
    "id": "1",
    "types": [{"type": "address", "confidence": 0.9}],
    "benign": 0.0,
    "notes": []
  }]
}
```

让一体机用上它：

```powershell
$env:FLOWMASK_ENABLE_L2 = "1"
$env:FLOWMASK_L2_ENDPOINT = "http://127.0.0.1:8091/v1/classify"
python -m uvicorn app.main:app --port 8090
# 或直接跑对比评测（不启服务，用内置 mock 后端）
python -m app.evaluate --compare-l2
```

## 3. 后端模式

| `FLOWMASK_L2_MODE` | 后端 | 用途 |
| --- | --- | --- |
| `mock`（默认） | 词表 + 上下文标记的启发式 | 跑通链路、离线增益对比；**不是真模型** |
| `onnx` | `model.int8.onnx` + `tokenizer.json` + `labels.json` | 接真 RoBERTa，CPU 推理约 5-20ms |
| `auto` | 有模型用 onnx，否则回落 mock | 演示机与开发机共用一套配置 |

其他环境变量：`FLOWMASK_L2_HOST`（默认 127.0.0.1）、`FLOWMASK_L2_PORT`（默认 8091）、
`FLOWMASK_L2_MODEL_DIR`（默认 `models/roberta-wwm-sens`）。

## 4. 接真实模型（RoBERTa-wwm-ext 多标签）

**为什么是 RoBERTa-wwm-ext**：中文全词掩码预训练，在简历/医疗/政务这类中文文本上，
同等数据量下比 bert-base-chinese 稳；12 类多标签（手机号/身份证/银行卡/邮箱/IP/密钥/口令/姓名/地址/医疗/…）
用 sigmoid 多标签头，一篇文章可同时命中多个类别。

```powershell
# 1) 下权重（本机 huggingface.co 不通，走 ModelScope）
pip install modelscope
modelscope download --model google-bert/bert-base-chinese --local_dir models/bert-base-chinese

# 2) 训练（数据：SenResume/MSRA/Resume/Weibo + 自建 12 类标注）
pip install torch transformers datasets
#    训练脚本沿用现有 pipeline 的标签定义；输出 config.json 里带 id2label

# 3) 导出 ONNX + int8 量化（产物目录与 OnnxBackend 约定一致）
pip install onnx onnxruntime onnxruntime-tools
python -m l2_service.export_onnx --model-dir models/roberta-wwm-sens --out models/roberta-wwm-sens-onnx

# 4) 用真模型起服务
$env:FLOWMASK_L2_MODE = "onnx"
$env:FLOWMASK_L2_MODEL_DIR = "models/roberta-wwm-sens-onnx"
python -m l2_service
```

> 训练数据与标签规范见 `eval/README.md`；当前仓库未内置模型权重（体积原因），
> `models/` 已在根 `.gitignore` 里。

## 5. 与一体机的协同逻辑（`app/identify/pipeline.py`）

1. **只对 L1 命中的样本调用**：没有规则命中就谈不上复核，避免把算力花在正常文本上；
2. **补类别**：L2 置信度 ≥0.70 且 L1 没有该类别时，补类别并加 `0.05 × confidence` 分；
3. **压制误报**：`benign ≥0.75` 时按乘性系数压分（上限 0.9），并把 `l2_benign` 写进置信度证据；
4. **高危直达类更保守**：证件/银行卡/密钥要 `benign ≥0.90` 才允许压制，且压制后取消 `direct` 标记；
5. **失败降级**：超时/异常返回空结果，一体机 `degraded=True`，结论继续用 L1；
6. **文档级上下文**：JSON/CSV/SQL 把整份文档文本作为上下文传给 L2（"示例"线索可能在别的字段里）。

## 6. 契约（与 Go 侧 DLP 推理契约保持一致）

```
POST /v1/classify
{"items": [{"id": "seg-1", "content": "……"}]}        # 批量 ≤ 32
->
{"model": "onnx-roberta", "degraded": false, "latency_ms": 6.2,
 "items": [{"id": "seg-1",
            "types": [{"type": "id_card", "confidence": 0.93}],
            "benign": 0.12,
            "notes": []}]}
```

任何内部异常都不会 500：该批返回 `degraded=true` 的空结果，让调用方降级继续。

## 7. 当前效果（评测集 75 条）

| 指标 | L1 only | L1 + L2（mock） |
| --- | ---: | ---: |
| 分级准确率 | 89.3% | **98.7%** |
| 安全误报率 | 25.9% | **0.0%** |
| 高危漏检率 | 0.0% | 0.0% |

> mock 后端只用于验证链路价值；接真模型后复跑 `python -m app.evaluate --enable-l2`，
> 才是"多模型协同"的真实增益。报告见 [`docs/评测报告.md`](../../docs/评测报告.md)。
