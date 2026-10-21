"""LoRA 微调 Qwen2.5-Instruct 做 L3 分级裁判。

设计取舍：
- 训练目标 = ``l3_service`` 推理时的同一段 INSTRUCT_PROMPT + 同一行 JSON，
  这样微调权重可以直接被 ``FLOWMASK_L3_MODE=instruct`` 加载，不需要改线上代码；
- 可选依赖（torch/transformers/peft）全部在函数内懒导入，缺依赖时给可照做的报错，
  这样 ``dry-run`` 与数据集构建在没有 GPU 的机器上也能跑；
- 手写训练循环而不是 ``Trainer``：依赖更少、对 transformers 版本漂移更稳。
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

from .labels import LEVELS, parse_target

SERVER_DIR = Path(__file__).resolve().parents[1]
DEFAULT_BASE = "Qwen/Qwen2.5-1.5B-Instruct"
DEFAULT_OUT = SERVER_DIR / "l3_models" / "qwen2.5-1.5b-sens"
DEFAULT_DATASET = SERVER_DIR / "data" / "l3_training.jsonl"


def _require():
    """懒导入训练依赖；缺了给出精确的安装命令。"""
    try:
        import torch
        from peft import LoraConfig, get_peft_model  # type: ignore[import-not-found]
        from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - 依赖缺失分支
        raise RuntimeError(
            "L3 微调需要 torch/transformers/peft：\n"
            "  pip install -r requirements-ml.txt\n"
            "  pip install peft\n"
            f"（原始错误：{exc}）"
        ) from exc
    return torch, LoraConfig, get_peft_model, AutoModelForCausalLM, AutoTokenizer


def load_samples(dataset_path: Path | str = DEFAULT_DATASET) -> list[dict]:
    path = Path(dataset_path)
    if not path.is_file():
        raise SystemExit(f"缺少训练数据：{path}（先跑 python -m l3_training dataset）")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    if not rows:
        raise SystemExit(f"训练数据为空：{path}")
    return rows


def build_prompt(tokenizer, text: str) -> str:
    """与 ``l3_service.backends.InstructJudgeBackend._build_prompt`` 完全一致。"""
    from l3_service.backends import INSTRUCT_PROMPT

    content = INSTRUCT_PROMPT.format(content=text[:1500])
    messages = [{"role": "user", "content": content}]
    return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)


def encode_sample(tokenizer, prompt: str, target: str, max_length: int) -> tuple[list[int], list[int]]:
    """prompt 部分不计损失（-100），只在目标 JSON 上算交叉熵。"""
    prompt_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
    eos = tokenizer.eos_token or ""
    target_ids = tokenizer(target + eos, add_special_tokens=False)["input_ids"]
    # 长文本时保留 prompt 尾部：正文在末尾，截头会丢掉要判的内容
    keep = max(max_length - len(target_ids), 16)
    prompt_ids = prompt_ids[-keep:]
    ids = (prompt_ids + target_ids)[:max_length]
    labels = ([-100] * len(prompt_ids) + target_ids)[:max_length]
    return ids, labels


def dry_run(dataset_path: Path | str = DEFAULT_DATASET, *, min_rows: int = 50) -> dict[str, object]:
    """不依赖 torch 的数据体检：级别/类别覆盖、长度、目标可解析性。"""
    rows = load_samples(dataset_path)
    by_level: dict[str, int] = {}
    invalid = 0
    for row in rows:
        level = str(row.get("level", ""))
        by_level[level] = by_level.get(level, 0) + 1
        if parse_target(str(row.get("target", ""))) is None:
            invalid += 1
    missing = [level for level in LEVELS if level not in by_level]
    avg_chars = round(sum(len(str(row.get("text", ""))) for row in rows) / max(len(rows), 1), 1)
    return {
        "dataset": str(dataset_path),
        "rows": len(rows),
        "by_level": by_level,
        "missing_levels": missing,
        "invalid_targets": invalid,
        "avg_text_chars": avg_chars,
        "train": sum(1 for row in rows if row.get("split") == "train"),
        "val": sum(1 for row in rows if row.get("split") == "val"),
        "ready": len(rows) >= min_rows and not missing and invalid == 0,
    }


def train(
    dataset_path: Path | str = DEFAULT_DATASET,
    out_dir: Path | str = DEFAULT_OUT,
    *,
    base_model: str = DEFAULT_BASE,
    epochs: int = 2,
    lr: float = 1e-4,
    batch_size: int = 1,
    grad_accum: int = 8,
    max_length: int = 1024,
    lora_r: int = 8,
    lora_alpha: int = 16,
    lora_dropout: float = 0.05,
    device: str = "cuda",
    seed: int = 42,
) -> dict[str, object]:
    """LoRA 微调；返回指标并落盘 adapter（``out_dir`` 可直接给 peft 合并）。"""
    torch, LoraConfig, get_peft_model, AutoModelForCausalLM, AutoTokenizer = _require()
    rows = load_samples(dataset_path)
    train_rows = [row for row in rows if row.get("split", "train") == "train"]
    val_rows = [row for row in rows if row.get("split") == "val"]
    if not train_rows:
        raise SystemExit("训练集为空：dataset 里没有 split=train 的行")

    torch.manual_seed(seed)
    target_device = device
    if device == "cuda" and not torch.cuda.is_available():
        target_device = "cpu"

    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    dtype = torch.bfloat16 if target_device == "cuda" else torch.float32
    model = AutoModelForCausalLM.from_pretrained(
        base_model, torch_dtype=dtype, trust_remote_code=True
    ).to(target_device)
    model.config.use_cache = False
    peft_model = get_peft_model(
        model,
        LoraConfig(
            r=lora_r,
            lora_alpha=lora_alpha,
            lora_dropout=lora_dropout,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        ),
    )
    optimizer = torch.optim.AdamW(
        [p for p in peft_model.parameters() if p.requires_grad], lr=lr
    )

    def batches(rows: list[dict], shuffle: bool):
        order = list(range(len(rows)))
        if shuffle:
            random.Random(seed).shuffle(order)
        for start in range(0, len(order), batch_size):
            chunk = [rows[i] for i in order[start : start + batch_size]]
            encoded = [
                encode_sample(
                    tokenizer,
                    build_prompt(tokenizer, str(row.get("text", ""))),
                    str(row.get("target", "")),
                    max_length,
                )
                for row in chunk
            ]
            width = max(len(ids) for ids, _ in encoded)
            pad = tokenizer.pad_token_id
            input_ids = [ids + [pad] * (width - len(ids)) for ids, _ in encoded]
            labels = [lab + [-100] * (width - len(lab)) for _, lab in encoded]
            mask = [[1] * len(ids) + [0] * (width - len(ids)) for ids, _ in encoded]
            yield (
                torch.tensor(input_ids, device=target_device),
                torch.tensor(mask, device=target_device),
                torch.tensor(labels, device=target_device),
            )

    def evaluate() -> float | None:
        if not val_rows:
            return None
        peft_model.eval()
        total, count = 0.0, 0
        with torch.no_grad():
            for ids, mask, labels in batches(val_rows, shuffle=False):
                loss = peft_model(input_ids=ids, attention_mask=mask, labels=labels).loss
                total += float(loss.detach())
                count += 1
        peft_model.train()
        return round(total / max(count, 1), 4)

    history: list[dict[str, object]] = []
    started = time.perf_counter()
    peft_model.train()
    for epoch in range(1, epochs + 1):
        total, steps = 0.0, 0
        optimizer.zero_grad()
        for index, (ids, mask, labels) in enumerate(batches(train_rows, shuffle=True), start=1):
            loss = peft_model(input_ids=ids, attention_mask=mask, labels=labels).loss
            (loss / max(grad_accum, 1)).backward()
            if index % max(grad_accum, 1) == 0:
                torch.nn.utils.clip_grad_norm_(
                    [p for p in peft_model.parameters() if p.requires_grad], 1.0
                )
                optimizer.step()
                optimizer.zero_grad()
            total += float(loss.detach())
            steps += 1
        optimizer.step()
        optimizer.zero_grad()
        history.append(
            {"epoch": epoch, "train_loss": round(total / max(steps, 1), 4), "val_loss": evaluate()}
        )

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    peft_model.save_pretrained(out)
    tokenizer.save_pretrained(out)
    metrics = {
        "base_model": base_model,
        "device": target_device,
        "rows": len(rows),
        "train": len(train_rows),
        "val": len(val_rows),
        "epochs": epochs,
        "lr": lr,
        "lora_r": lora_r,
        "lora_alpha": lora_alpha,
        "max_length": max_length,
        "elapsed_s": round(time.perf_counter() - started, 1),
        "history": history,
        "adapter_dir": str(out),
    }
    (out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metrics


def merge_adapter(
    base_model: str = DEFAULT_BASE,
    adapter_dir: Path | str = DEFAULT_OUT,
    out_dir: Path | str | None = None,
) -> Path:
    """把 LoRA adapter 合并进基座并落成完整模型目录（给 l3_service 直接加载）。"""
    adapter = Path(adapter_dir)
    if not (adapter / "adapter_config.json").is_file():
        raise SystemExit(f"不是 LoRA 目录（缺 adapter_config.json）：{adapter}")
    torch, _, _, AutoModelForCausalLM, AutoTokenizer = _require()
    from peft import PeftModel  # type: ignore[import-not-found]

    target = Path(out_dir) if out_dir else adapter.parent / f"{adapter.name}-merged"
    dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
    model = AutoModelForCausalLM.from_pretrained(
        base_model, torch_dtype=dtype, trust_remote_code=True
    )
    merged = PeftModel.from_pretrained(model, adapter).merge_and_unload()
    target.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(target, safe_serialization=True)
    AutoTokenizer.from_pretrained(adapter, trust_remote_code=True).save_pretrained(target)
    return target
