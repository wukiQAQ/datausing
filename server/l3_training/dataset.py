"""把评测集金标转成 L3 指令微调样本（可选依赖零负担，纯标准库即可跑）。"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from .labels import categories_from_row, level_from_row, reason_for, target_json

SERVER_DIR = Path(__file__).resolve().parents[1]
DEFAULT_EVAL = SERVER_DIR / "eval" / "dataset.jsonl"
DEFAULT_OUT = SERVER_DIR / "data" / "l3_training.jsonl"

# 与 l3_service 推理时的截断保持一致（INSTRUCT_PROMPT 里 text[:1500]）
MAX_TEXT_CHARS = 1500


def text_sha1(text: str) -> str:
    """文本指纹，用来去重与排查「训练集/评测集是否重叠」。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def sample_from_row(row: dict[str, object]) -> dict[str, object] | None:
    """一行评测集 → 一条指令样本；级别或正文缺失返回 ``None``。"""
    text = str(row.get("content") or "").strip()
    level = level_from_row(row)
    if not text or not level:
        return None
    text = text[:MAX_TEXT_CHARS]
    categories = categories_from_row(row)
    return {
        "id": str(row.get("id") or text_sha1(text)[:12]),
        "text": text,
        "target": target_json(level, categories, reason_for(level, categories)),
        "level": level,
        "categories": categories,
        "source": str(row.get("source") or "curated"),
    }


def build_samples(eval_path: Path | str = DEFAULT_EVAL) -> list[dict[str, object]]:
    """读评测集并转成样本（按内容去重，保持首次出现顺序）。"""
    path = Path(eval_path)
    if not path.is_file():
        raise SystemExit(f"缺少评测集：{path}（先跑 python eval/build_dataset.py）")
    samples: list[dict[str, object]] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        sample = sample_from_row(json.loads(line))
        if sample is None:
            continue
        digest = text_sha1(str(sample["text"]))
        if digest in seen:
            continue
        seen.add(digest)
        sample["text_sha1"] = digest
        samples.append(sample)
    return samples


def split_samples(
    samples: list[dict[str, object]], *, val_ratio: float = 0.1, seed: int = 42
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """确定性划分 train/val（按内容去重后随机打乱，val 至少 1 条）。"""
    if not 0.0 <= val_ratio < 1.0:
        raise ValueError(f"val_ratio 必须在 [0,1)：{val_ratio}")
    ordered = sorted(samples, key=lambda item: str(item.get("text_sha1") or item.get("id")))
    rng = random.Random(seed)
    rng.shuffle(ordered)
    val_count = max(1, int(round(len(ordered) * val_ratio))) if val_ratio > 0 else 0
    val_count = min(val_count, max(len(ordered) - 1, 0))
    return ordered[val_count:], ordered[:val_count]


def summarize(samples: list[dict[str, object]]) -> dict[str, object]:
    """样本画像：级别/类别分布、平均长度，给小样本训练做体检。"""
    by_level: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for sample in samples:
        level = str(sample.get("level", ""))
        by_level[level] = by_level.get(level, 0) + 1
        for category in sample.get("categories") or []:  # type: ignore[union-attr]
            key = str(category)
            by_category[key] = by_category.get(key, 0) + 1
    lengths = [len(str(sample.get("text", ""))) for sample in samples]
    return {
        "total": len(samples),
        "by_level": by_level,
        "by_category": by_category,
        "avg_text_chars": round(sum(lengths) / len(lengths), 1) if lengths else 0.0,
        "unique_ids": len({str(sample.get("id", "")) for sample in samples}),
    }


def build_dataset(
    out_path: Path | str = DEFAULT_OUT,
    *,
    eval_path: Path | str = DEFAULT_EVAL,
    val_ratio: float = 0.1,
    seed: int = 42,
) -> dict[str, object]:
    """构建并写出 JSONL（每行带 split 字段），返回画像。"""
    samples = build_samples(eval_path)
    train, val = split_samples(samples, val_ratio=val_ratio, seed=seed)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as handle:
        for split, rows in (("train", train), ("val", val)):
            for row in rows:
                handle.write(json.dumps({**row, "split": split}, ensure_ascii=False) + "\n")
    stats = summarize(samples)
    stats.update({"train": len(train), "val": len(val), "out": str(out)})
    return stats
