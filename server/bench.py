"""FlowMask 性能基准：L1 规则、HTTP 端到端、L2 ONNX 批处理、L3 判定（可选）。

只依赖标准库 + 项目自身依赖，跑出来的数字直接写进 ``docs/性能报告.md``，
方式是“同机、同一条命令、可复现”，避免报告里出现拍脑袋的指标。

用法（在 ``server/`` 目录下）：

    python -m bench                                  # L1 + 端到端 + L2（有 ONNX 模型时）
    python -m bench --skip-l2 --skip-detect           # 只测 L1
    python -m bench --l3-endpoint http://127.0.0.1:8092/v1/judge
    python -m bench --markdown-out ../docs/性能报告.md

口径说明：
- L1 / 端到端 / L2 都按“单条文本”为单位计时，吞吐 = 1000 / 单条均值（ms）；
- 端到端走 ``TestClient``（进程内 HTTP，不含网络往返），比真实浏览器请求略快；
- L2 直接加载 ONNX int8 模型（``l2_models/bert-sens-onnx``），批大小 1/8/32 分开测；
- L3 需要先 ``python -m l3_service`` 起服务，否则该项自动跳过。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent

SENSITIVE_TEXT = (
    "客户张三，手机 13800138000，身份证 11010519491231002X，"
    "收货地址：福建省福州市鼓楼区五四路 100 号。"
)
SAFE_TEXT = "2026-10-20 09:12:33 INFO service healthy, requests=18234, latency_ms=3.1"
L2_MODEL_DIR = SERVER_DIR / "l2_models" / "bert-sens-onnx"


# --------------------------------------------------------------------------- 统计


def percentile(values: list[float], p: float) -> float:
    """线性插值求分位数（和 Prometheus histogram_quantile 的直觉一致）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (p / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def summarize(name: str, latencies_ms: list[float], unit: str = "单条") -> dict[str, object]:
    """把一组时延样本汇总成一行指标。"""
    mean = statistics.fmean(latencies_ms) if latencies_ms else 0.0
    return {
        "name": name,
        "unit": unit,
        "count": len(latencies_ms),
        "mean_ms": round(mean, 3),
        "p50_ms": round(percentile(latencies_ms, 50), 3),
        "p95_ms": round(percentile(latencies_ms, 95), 3),
        "p99_ms": round(percentile(latencies_ms, 99), 3),
        "max_ms": round(max(latencies_ms), 3) if latencies_ms else 0.0,
        "throughput_per_s": round(1000.0 / mean, 2) if mean > 0 else 0.0,
    }


def _time_ms(fn) -> tuple[float, object]:
    started = time.perf_counter()
    value = fn()
    return (time.perf_counter() - started) * 1000.0, value


# --------------------------------------------------------------------------- 各项基准


def bench_l1(iterations: int = 2000) -> dict[str, object]:
    """L1 只做“正则 + 校验 + 评分”，不含任何网络与模型。"""
    from app.identify import analyze  # noqa: PLC0415

    for _ in range(20):  # 预热：让 re/正则缓存与分支都稳定下来
        analyze(SENSITIVE_TEXT)
    samples: list[float] = []
    for _ in range(iterations):
        elapsed, _ = _time_ms(lambda: analyze(SENSITIVE_TEXT))
        samples.append(elapsed)
    return summarize("L1 规则识别（12 条规则 + 评分分级）", samples)


def bench_detect(iterations: int = 200) -> dict[str, object]:
    """端到端：进程内 HTTP POST /api/v1/detect（含 FastAPI 校验与 JSON 序列化）。"""
    workdir = Path(tempfile.mkdtemp(prefix="flowmask-bench-"))
    os.environ["FLOWMASK_DATA_DIR"] = str(workdir)
    os.environ["FLOWMASK_DB_PATH"] = str(workdir / "bench.db")
    os.environ["FLOWMASK_ENABLE_L2"] = "0"
    os.environ["FLOWMASK_ENABLE_L3"] = "0"

    from fastapi.testclient import TestClient  # noqa: PLC0415

    from app.main import create_app  # noqa: PLC0415

    payload = {"text": SENSITIVE_TEXT, "mask_level": "L3"}
    with TestClient(create_app()) as client:
        for _ in range(10):
            client.post("/api/v1/detect", json=payload)
        samples: list[float] = []
        for _ in range(iterations):
            elapsed, response = _time_ms(lambda: client.post("/api/v1/detect", json=payload))
            if response.status_code != 200:
                raise SystemExit(f"/detect 返回 {response.status_code}: {response.text[:200]}")
            samples.append(elapsed)
    return summarize("端到端 POST /api/v1/detect（进程内 HTTP）", samples)


def bench_l2(batch_sizes: tuple[int, ...] = (1, 8, 32), repeats: int = 20) -> list[dict[str, object]]:
    """L2 ONNX int8：分别测批大小 1/8/32 的单条成本（CPU 推理）。"""
    if not (L2_MODEL_DIR / "model.int8.onnx").exists():
        print(f"[skip] 没有 ONNX 模型：{L2_MODEL_DIR}")
        return []
    from l2_service.backends import OnnxBackend  # noqa: PLC0415

    backend = OnnxBackend(L2_MODEL_DIR, max_length=128)
    texts = [SENSITIVE_TEXT if i % 2 == 0 else SAFE_TEXT for i in range(32)]
    results: list[dict[str, object]] = []
    for size in batch_sizes:
        batch = texts[:size]
        for _ in range(3):  # 预热
            backend.predict(batch)
        totals: list[float] = []
        for _ in range(repeats):
            elapsed, _ = _time_ms(lambda: backend.predict(batch))
            totals.append(elapsed)
        per_item = [total / size for total in totals]
        row = summarize(f"L2 ONNX int8 批大小 {size}", per_item, unit=f"单条（batch={size}）")
        row["batch_total_mean_ms"] = round(statistics.fmean(totals), 3)
        results.append(row)
    return results


def bench_l3(endpoint: str, iterations: int = 3) -> dict[str, object] | None:
    """L3 判定：需要先起 ``python -m l3_service``；每次调用是一次回环 HTTP。

    每次换一段文本（加不同工单号），避免模型服务或客户端的缓存把时延测成“假的低”。
    拿不到可用判定就直接报错退出，宁可缺一项也不写一个看起来更好的数。
    """
    from app.identify.l3 import HttpJudgeClassifier  # noqa: PLC0415

    classifier = HttpJudgeClassifier(endpoint, model="bench", timeout_s=30.0)
    warm = classifier.judge(f"{SENSITIVE_TEXT}（预热）")
    if warm is None:
        print(f"[skip] L3 服务不可用：{endpoint}")
        return None
    samples: list[float] = []
    risks: list[str] = []
    for index in range(iterations):
        text = f"{SENSITIVE_TEXT}（工单号 BENCH-{time.time_ns()}-{index}）"
        elapsed, verdict = _time_ms(lambda: classifier.judge(text))
        if verdict is None:
            raise SystemExit(f"L3 第 {index + 1} 次调用没有拿到判定（endpoint={endpoint}）")
        samples.append(elapsed)
        risks.append(verdict.risk)
    row = summarize("L3 指令裁判（1.5B，回环 HTTP + GPU 推理）", samples, unit="单条")
    row["l3_risks"] = ",".join(risks)
    return row



# --------------------------------------------------------------------------- 输出


def _machine() -> dict[str, str]:
    return {
        "time": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
        "os": f"{platform.system()} {platform.release()}",
        "cpu": platform.processor() or platform.machine(),
        "cores": str(os.cpu_count()),
        "python": platform.python_version(),
    }


def render_markdown(rows: list[dict[str, object]], machine: dict[str, str]) -> str:
    lines = [
        "# FlowMask 性能报告（本机实测）",
        "",
        "> 本文件由 `python -m bench --markdown-out ../docs/性能报告.md` 生成，数字来自一次真实运行的样本，",
        "> 换机器/换负载会变；对比时请用同一台机器、同一条命令复跑。",
        "",
        "## 1. 环境",
        "",
        "| 项 | 值 |",
        "| --- | --- |",
    ]
    for key, label in (("time", "运行时间"), ("os", "操作系统"), ("cpu", "CPU"), ("cores", "逻辑核数"), ("python", "Python")):
        lines.append(f"| {label} | {machine[key]} |")
    lines += [
        "",
        "## 2. 结果",
        "",
        "| 项 | 样本数 | 均值 | P50 | P95 | P99 | 最大 | 吞吐（条/秒） |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {name} | {count} | {mean_ms} ms | {p50_ms} ms | {p95_ms} ms | {p99_ms} ms | {max_ms} ms | {throughput_per_s} |".format(
                **row
            )
        )
    lines += [
        "",
        "## 3. 口径与边界",
        "",
        "- **L1**：`analyze()` 全链路（detect → 评分 → 分级），无网络、无模型调用；",
        "- **端到端**：`TestClient` 进程内直连 FastAPI（不含 TCP/浏览器开销），单条文本，已脱敏输出；",
        "- **L2**：ONNX int8 直接推理（CPU），批大小 1/8/32；单条成本 = 批次总耗时 / 批大小；",
        "- **L3**：需要单独起 `python -m l3_service`（默认不启用；这里只测单次判定的回环时延）；",
        "- 样本数默认 L1=2000、端到端=200、L2 每档 20 批、L3=3 次，预热样本不计入。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FlowMask 性能基准")
    parser.add_argument("--skip-l1", action="store_true", help="跳过 L1")
    parser.add_argument("--skip-detect", action="store_true", help="跳过端到端 /detect")
    parser.add_argument("--skip-l2", action="store_true", help="跳过 L2 ONNX 批处理")
    parser.add_argument("--l3-endpoint", default="", help="L3 服务地址（如 http://127.0.0.1:8092/v1/judge）；留空跳过")
    parser.add_argument("--l1-iterations", type=int, default=2000)
    parser.add_argument("--detect-iterations", type=int, default=200)
    parser.add_argument("--l2-repeats", type=int, default=20)
    parser.add_argument("--markdown-out", default="", help="把结果写成 markdown（如 ../docs/性能报告.md）")
    parser.add_argument("--json-out", default="", help="把结果写成 JSON")
    args = parser.parse_args(argv)

    rows: list[dict[str, object]] = []
    if not args.skip_l1:
        print("[1/4] L1 规则识别 …", flush=True)
        rows.append(bench_l1(args.l1_iterations))
    if not args.skip_detect:
        print("[2/4] 端到端 /detect …", flush=True)
        rows.append(bench_detect(args.detect_iterations))
    if not args.skip_l2:
        print("[3/4] L2 ONNX 批处理 …", flush=True)
        rows.extend(bench_l2(repeats=args.l2_repeats))
    if args.l3_endpoint:
        print("[4/4] L3 判定 …", flush=True)
        row = bench_l3(args.l3_endpoint)
        if row is not None:
            rows.append(row)

    if not rows:
        print("没有可输出的指标", file=sys.stderr)
        return 1

    machine = _machine()
    print()
    for row in rows:
        print(
            "{name}: 均值 {mean_ms} ms / P95 {p95_ms} ms / 吞吐 {throughput_per_s} 条每秒（样本 {count}）".format(**row)
        )
    if args.markdown_out:
        target = Path(args.markdown_out)
        if not target.is_absolute():
            target = (SERVER_DIR / target).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_markdown(rows, machine), encoding="utf-8")
        print(f"\n已写入 {target}")
    if args.json_out:
        target = Path(args.json_out)
        if not target.is_absolute():
            target = (SERVER_DIR / target).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"machine": machine, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
