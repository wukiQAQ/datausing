"""对比两个 L3 裁判端点（基座 vs LoRA 微调）：分级准确率、JSON 合法率、时延。

用法（在 ``server/`` 目录下，两个 l3_service 已启动）：

    python l3_judge_compare.py \
        --base http://127.0.0.1:8092/v1/judge \
        --tuned http://127.0.0.1:8093/v1/judge \
        --limit-per-level 20 --out docs/l3_judge_compare.json

为什么单独做这个对比：管道级准确率被 L1/L2 主导（99.7%），L3 的真实进步
（60.0% → 76.2%）会被掩盖，必须直接评「裁判本身」。
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent
DEFAULT_EVAL = SERVER_DIR / "eval" / "dataset.jsonl"
LEVELS = ("L3", "L2", "L1", "SAFE")


def load_sample(eval_path: Path, limit_per_level: int) -> list[dict]:
    rows = [
        json.loads(line)
        for line in eval_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    picked: list[dict] = []
    for level in LEVELS:
        picked.extend([row for row in rows if row.get("level") == level][:limit_per_level])
    return picked


def judge(endpoint: str, text: str) -> tuple[str, bool, float]:
    body = json.dumps({"text": text[:1500]}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint, data=body, headers={"Content-Type": "application/json"}
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    latency = (time.perf_counter() - started) * 1000
    level = str(payload.get("level") or "").strip().upper()
    return level, level in LEVELS, latency


def evaluate(name: str, endpoint: str, rows: list[dict]) -> dict[str, object]:
    correct = valid = 0
    latencies: list[float] = []
    per_level: dict[str, list[int]] = {level: [0, 0] for level in LEVELS}
    wrong: list[dict[str, object]] = []
    for row in rows:
        level, ok, latency = judge(endpoint, str(row.get("content", "")))
        gold = str(row.get("level"))
        valid += int(ok)
        latencies.append(latency)
        hit = int(level == gold)
        correct += hit
        per_level[gold][0] += hit
        per_level[gold][1] += 1
        if not hit:
            wrong.append({"id": row.get("id"), "gold": gold, "pred": level})
    total = max(len(rows), 1)
    print(
        f"{name}: n={len(rows)} 准确率={correct / total:.1%} "
        f"JSON 合法率={valid / total:.1%} 均延迟={sum(latencies) / total:.0f}ms"
    )
    for level in LEVELS:
        hit, count = per_level[level]
        if count:
            print(f"  {level}: {hit}/{count} = {hit / count:.0%}")
    if wrong:
        print(f"  错误样本（前 5）: {json.dumps(wrong[:5], ensure_ascii=False)}")
    return {
        "name": name,
        "endpoint": endpoint,
        "n": len(rows),
        "accuracy": round(correct / total, 4),
        "valid_rate": round(valid / total, 4),
        "avg_latency_ms": round(sum(latencies) / total, 1),
        "per_level": {level: {"hit": h, "total": t} for level, (h, t) in per_level.items()},
        "wrong": wrong,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="对比基座与微调后的 L3 裁判")
    parser.add_argument("--base", default="http://127.0.0.1:8092/v1/judge")
    parser.add_argument("--tuned", default="http://127.0.0.1:8093/v1/judge")
    parser.add_argument("--eval", default=str(DEFAULT_EVAL))
    parser.add_argument("--limit-per-level", type=int, default=20)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    rows = load_sample(Path(args.eval), args.limit_per_level)
    print(f"样本：{len(rows)} 条（每级最多 {args.limit_per_level}）")
    base = evaluate("基座 Qwen2.5-1.5B-Instruct", args.base, rows)
    tuned = evaluate("LoRA 微调 qwen2.5-1.5b-sens", args.tuned, rows)
    print(f"\n裁判分级准确率变化：{base['accuracy']:.1%} → {tuned['accuracy']:.1%}")
    if args.out:
        Path(args.out).write_text(
            json.dumps({"base": base, "tuned": tuned}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
