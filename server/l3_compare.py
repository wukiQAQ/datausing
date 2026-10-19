"""对评测集里会被 L3 触发的样本，逐条调用 L3 服务并记录判定/理由/延迟。

用法：
    python l3_compare.py <endpoint-url> <out.json>

触发条件与线上完全一致（直接复用 DetectionPipeline 的灰区门控）：
L1 命中、分数落在 0.25~0.85 灰区、非直达高危。
"""

import json
import sys
import time
from pathlib import Path

from app.identify.l3 import HttpJudgeClassifier
from app.identify.pipeline import DetectionPipeline, DetectOptions
from app.parsers import parse_text

DATASET = Path("eval/dataset.jsonl")


def main() -> None:
    endpoint, out_path = sys.argv[1], Path(sys.argv[2])
    rows = [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    judge = HttpJudgeClassifier(endpoint, model="l3-compare", timeout_s=30.0)
    pipeline = DetectionPipeline(guard=judge)
    records = []
    for row in rows:
        started = time.perf_counter()
        report = pipeline.analyze_document(
            parse_text(row["content"]), DetectOptions(enable_l3=True)
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        for finding in report.findings:
            if "l3" not in finding.model_chain:
                continue
            evidence = [
                item
                for item in finding.confidence_vector
                if isinstance(item, dict) and item.get("type") == "l3_guard"
            ]
            risk = evidence[0].get("risk") if evidence else "?"
            records.append(
                {
                    "id": row["id"],
                    "gold": row["level"],
                    "risk": risk,
                    "reason": finding.reason or "",
                    "final_level": finding.level.value,
                    "elapsed_ms": elapsed_ms,
                    "degraded": finding.degraded,
                }
            )
            break

    latencies = [r["elapsed_ms"] for r in records]
    summary = {
        "endpoint": endpoint,
        "triggered": len(records),
        "unsafe": sum(1 for r in records if r["risk"] == "Unsafe"),
        "controversial": sum(1 for r in records if r["risk"] == "Controversial"),
        "safe": sum(1 for r in records if r["risk"] == "Safe"),
        "degraded": sum(1 for r in records if r["degraded"]),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "max_latency_ms": max(latencies) if latencies else None,
    }
    out_path.write_text(
        json.dumps({"summary": summary, "records": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))

    # 看「会不会误升级」：判 Unsafe 但标注不是 L3 的样本
    for record in records:
        if record["risk"] == "Unsafe" and record["gold"] != "L3":
            print("可能误升：", record["id"], record["gold"], "->", record["final_level"], record["reason"][:60])


if __name__ == "__main__":
    main()
