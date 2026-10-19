"""RoBERTa/BERT 多标签微调（可选依赖：``torch`` + ``transformers``）。

这个模块刻意保持"能跑最小闭环"的形态：数据 → 训练 → 保存成 HuggingFace 格式 →
``python -m l2_service.export_onnx`` 导出 int8 → ``FLOWMASK_L2_MODE=onnx`` 接管。
权重是否下载、是否真的开训，交给使用者决定；代码本身不绑定任何模型。

没有 torch 的机器上依然可以跑 :func:`dry_run`（数据体检）与全部数据构造/测试，
因此 CI 与 20 天冲刺里"先把链路搭好、等权重到位再训"是可行的。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .dataset import Example, load_jsonl, split_examples
from .labels import BENIGN, LABELS


def _require(module: str, hint: str):
    try:
        return __import__(module)
    except ImportError as exc:  # pragma: no cover - 依赖缺失分支在 CI 里会走这里，下面有测试
        raise RuntimeError(f"缺少依赖 {module}：{hint}") from exc


def dry_run(dataset_path: str | Path, min_rows: int = 20) -> dict[str, object]:
    """不依赖 torch 的数据体检：样本量、类别分布、无正例样本、平均长度。"""
    examples = load_jsonl(dataset_path)
    if not examples:
        raise ValueError("数据集为空")
    counts = {name: 0 for name in LABELS}
    no_positive = 0
    lengths = []
    for example in examples:
        positives = example.positives()
        if not positives:
            no_positive += 1
        for name in positives:
            counts[name] += 1
        lengths.append(len(example.text))
    if len(examples) < min_rows:
        raise ValueError(f"样本太少（{len(examples)} < {min_rows}），训练前请先补数据")
    missing = [name for name, count in counts.items() if count == 0]
    return {
        "total": len(examples),
        "labels": list(LABELS),
        "positives": counts,
        "no_positive": no_positive,
        "no_positive_ratio": round(no_positive / len(examples), 4),
        "avg_chars": round(sum(lengths) / len(lengths), 1),
        "missing_labels": missing,
        "ready": not missing,
    }


def _prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "support": tp + fn}


def _evaluate(torch, model, loader, device, threshold: float = 0.5) -> dict[str, object]:
    model.eval()
    tp = [0] * len(LABELS)
    fp = [0] * len(LABELS)
    fn = [0] * len(LABELS)
    exact = 0
    total = 0
    with torch.no_grad():
        for inputs, targets in loader:
            inputs = {key: value.to(device) for key, value in inputs.items()}
            logits = model(**inputs).logits
            probs = torch.sigmoid(logits).cpu()
            preds = (probs >= threshold).int()
            targets = targets.int()
            for row_pred, row_true in zip(preds.tolist(), targets.tolist()):
                if row_pred == row_true:
                    exact += 1
                for index, (pred, truth) in enumerate(zip(row_pred, row_true)):
                    if pred and truth:
                        tp[index] += 1
                    elif pred and not truth:
                        fp[index] += 1
                    elif truth and not pred:
                        fn[index] += 1
            total += len(preds)

    per_label = {
        name: _prf(tp[i], fp[i], fn[i]) for i, name in enumerate(LABELS) if tp[i] + fn[i] > 0
    }
    macro_f1 = sum(item["f1"] for item in per_label.values()) / len(per_label) if per_label else 0.0
    micro_tp = sum(tp)
    micro_fp = sum(fp)
    micro_fn = sum(fn)
    micro_f1 = _prf(micro_tp, micro_fp, micro_fn)["f1"]
    return {
        "macro_f1": round(macro_f1, 4),
        "micro_f1": round(micro_f1, 4),
        "exact_match": round(exact / total, 4) if total else 0.0,
        "per_label": {name: {key: round(value, 4) for key, value in item.items()} for name, item in per_label.items()},
    }


def train(
    dataset_path: str | Path,
    out_dir: str | Path = "l2_models/roberta-wwm-sens",
    base_model: str = "bert-base-chinese",
    epochs: int = 3,
    batch_size: int = 16,
    lr: float = 2e-5,
    max_length: int = 128,
    val_ratio: float = 0.15,
    seed: int = 42,
    threshold: float = 0.5,
) -> dict[str, object]:
    """多标签微调入口；返回最优验证指标并落盘模型/分词器/labels.json/metrics.json。"""
    torch = _require("torch", "pip install torch --index-url https://download.pytorch.org/whl/cpu")
    _require("transformers", "pip install transformers")
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    import torch as _torch  # noqa: PLC0415 - 明确这里的 torch 已可用

    examples = load_jsonl(dataset_path)
    train_examples, val_examples = split_examples(examples, val_ratio=val_ratio, seed=seed)
    if not val_examples:
        raise ValueError("验证集为空：请把 val_ratio 设为 > 0 或提供更多样本")

    tokenizer = AutoTokenizer.from_pretrained(base_model)
    id2label = {index: name for index, name in enumerate(LABELS)}
    label2id = {name: index for index, name in enumerate(LABELS)}
    model = AutoModelForSequenceClassification.from_pretrained(
        base_model,
        num_labels=len(LABELS),
        problem_type="multi_label_classification",
        id2label=id2label,
        label2id=label2id,
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)

    def _encode(sample: list[Example]) -> dict[str, object]:
        encoded = tokenizer(
            [example.text for example in sample],
            truncation=True,
            max_length=max_length,
            padding=True,
            return_tensors="pt",
        )
        targets = torch.tensor(
            [[1.0 if example.labels.get(name) else 0.0 for name in LABELS] for example in sample]
        )
        return {"inputs": encoded, "targets": targets}

    class _Dataset(torch.utils.data.Dataset):
        def __init__(self, payload: dict[str, object]) -> None:
            self.inputs = payload["inputs"]
            self.targets = payload["targets"]

        def __len__(self) -> int:
            return len(self.targets)

        def __getitem__(self, index: int):
            return {key: value[index] for key, value in self.inputs.items()}, self.targets[index]

    train_loader = torch.utils.data.DataLoader(
        _Dataset(_encode(train_examples)), batch_size=batch_size, shuffle=True
    )
    val_loader = torch.utils.data.DataLoader(_Dataset(_encode(val_examples)), batch_size=batch_size)

    optimizer = torch.optim.AdamW(model.parameters(), lr=lr)
    loss_fn = torch.nn.BCEWithLogitsLoss()
    best: dict[str, object] = {}
    history: list[dict[str, object]] = []
    started = time.time()

    for epoch in range(1, epochs + 1):
        model.train()
        running = 0.0
        for inputs, targets in train_loader:
            inputs = {key: value.to(device) for key, value in inputs.items()}
            targets = targets.to(device)
            optimizer.zero_grad()
            logits = model(**inputs).logits
            loss = loss_fn(logits, targets)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            running += float(loss.item()) * len(targets)

        metrics = _evaluate(torch, model, val_loader, device, threshold=threshold)
        metrics["epoch"] = epoch
        metrics["train_loss"] = round(running / max(1, len(train_examples)), 4)
        history.append(metrics)
        print(
            f"[epoch {epoch}/{epochs}] loss={metrics['train_loss']} "
            f"macro_f1={metrics['macro_f1']} micro_f1={metrics['micro_f1']}"
        )
        if not best or float(metrics["macro_f1"]) > float(best.get("macro_f1", 0.0)):
            best = metrics

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out)
    tokenizer.save_pretrained(out)
    (out / "labels.json").write_text(json.dumps(list(LABELS), ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "base_model": base_model,
        "epochs": epochs,
        "batch_size": batch_size,
        "lr": lr,
        "max_length": max_length,
        "train_rows": len(train_examples),
        "val_rows": len(val_examples),
        "elapsed_s": round(time.time() - started, 1),
        "best": best,
        "history": history,
    }
    (out / "metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
