"""按需下载 FlowMask 需要的模型权重（权重不进仓库、也不打进 exe）。

为什么单独做脚本：
- 仓库体积敏感（权重合计 5 GB+），git 只提交代码与配置；
- Windows exe 只有 50 MB，模型权重放在 exe 同级的 `models/`、`l2_models/` 即可自动识别；
- 本机 huggingface.co 不可直连，脚本默认走 ModelScope，NER 权重走 hf-mirror。

用法（在 `server/` 目录下）::

    python scripts/download_models.py --list          # 看有哪些目标、多大、给谁用
    python scripts/download_models.py --only l3 ner   # 只下 L3 与 NER
    python scripts/download_models.py --all           # 全部（含训练基座，约 6.4 GB）
    python scripts/download_models.py --only l3 --dry-run
    python scripts/download_models.py --all --dest D:\\FlowMask\\models

依赖：ModelScope 目标需 `pip install modelscope`；HuggingFace 目标需
`pip install huggingface_hub`（脚本会自动设置 `HF_ENDPOINT=https://hf-mirror.com`）。
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
WEIGHT_PATTERNS = (
    "model.safetensors",
    "pytorch_model.bin",
    "*.safetensors",
    "*.bin",
)
# 小于该值视为“没下完/占位文件”（真实权重都是百 MB 级）
MIN_WEIGHT_BYTES = 1 << 20


@dataclass(frozen=True, slots=True)
class ModelTarget:
    """一个可下载的模型：名字、来源、仓库 id、落地目录、用途。"""

    name: str
    title: str
    source: str  # "modelscope" | "huggingface"
    model_id: str
    target_dir: str  # 相对 server/ 的目录
    approx_mb: int
    usage: str


PLAN: tuple[ModelTarget, ...] = (
    ModelTarget(
        name="l3",
        title="Qwen2.5-1.5B-Instruct（L3 指令裁判）",
        source="modelscope",
        model_id="Qwen/Qwen2.5-1.5B-Instruct",
        target_dir="models/qwen2.5-1.5b-instruct",
        approx_mb=2955,
        usage="L3 解释与分级复核（开 FLOWMASK_ENABLE_L3=1 才用）",
    ),
    ModelTarget(
        name="ner",
        title="bert4ner-base-chinese（L1.5 实体识别）",
        source="huggingface",
        model_id="shibing624/bert4ner-base-chinese",
        target_dir="models/bert4ner",
        approx_mb=388,
        usage="自由文本里的人名/地址（请求级开关 enable_ner）",
    ),
    ModelTarget(
        name="l2-base",
        title="bert-base-chinese（L2 训练基座）",
        source="modelscope",
        model_id="google-bert/bert-base-chinese",
        target_dir="models/bert-base-chinese",
        approx_mb=1632,
        usage="重训 L2 用；线上推理只需导出的 l2_models/bert-sens-onnx",
    ),
    ModelTarget(
        name="l3-guard",
        title="Qwen3Guard-Gen-0.6B（L3 护栏后端，可选）",
        source="modelscope",
        model_id="Qwen/Qwen3Guard-Gen-0.6B",
        target_dir="models/qwen3guard-gen-0.6b",
        approx_mb=1449,
        usage="内容安全护栏口径的备选后端（FLOWMASK_L3_MODE=hf）",
    ),
)

BY_NAME = {target.name: target for target in PLAN}


def resolve_target_dir(target: ModelTarget, base: Path | None = None) -> Path:
    """把目标目录解析成绝对路径；`base` 非空时替换默认的 server/ 前缀。"""
    if base is None:
        return SERVER_DIR / target.target_dir
    return Path(base) / Path(target.target_dir).relative_to("models")


def _weight_files(path: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in WEIGHT_PATTERNS:
        files.extend(path.glob(pattern))
    return files


def is_downloaded(target: ModelTarget, base: Path | None = None, *, min_bytes: int = MIN_WEIGHT_BYTES) -> bool:
    """目录里是否有“看起来完整”的权重文件（有 config.json + 足够大的权重）。"""
    path = resolve_target_dir(target, base)
    if not (path / "config.json").is_file():
        return False
    return any(item.stat().st_size >= min_bytes for item in _weight_files(path) if item.is_file())


def _download_modelscope(target: ModelTarget, dest: Path) -> Path:
    try:
        from modelscope import snapshot_download
    except ImportError as exc:  # pragma: no cover - 依赖缺失分支用测试注入覆盖
        raise RuntimeError("缺少 modelscope，请先 pip install modelscope") from exc
    dest.mkdir(parents=True, exist_ok=True)
    snapshot_download(target.model_id, local_dir=str(dest), cache_dir=str(SERVER_DIR / "models" / "_cache"))
    return dest


def _download_huggingface(target: ModelTarget, dest: Path) -> Path:
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:  # pragma: no cover - 依赖缺失分支用测试注入覆盖
        raise RuntimeError("缺少 huggingface_hub，请先 pip install huggingface_hub") from exc
    dest.mkdir(parents=True, exist_ok=True)
    snapshot_download(target.model_id, local_dir=str(dest))
    return dest


DOWNLOADERS = {"modelscope": _download_modelscope, "huggingface": _download_huggingface}


def download(target: ModelTarget, base: Path | None = None, *, force: bool = False, downloader=None) -> Path:
    """下载单个目标；已存在且完整时直接跳过（force=True 强制重下）。"""
    dest = resolve_target_dir(target, base)
    if not force and is_downloaded(target, base):
        return dest
    func = downloader or DOWNLOADERS[target.source]
    return func(target, dest)


def select_targets(only: list[str] | None, everything: bool) -> list[ModelTarget]:
    """按 --only / --all 选目标；名字未知时报错。"""
    if everything:
        return list(PLAN)
    if not only:
        # 默认只下“开箱即用必需”的两个：L3 + NER（训练基座与护栏备选按需）
        return [BY_NAME["l3"], BY_NAME["ner"]]
    unknown = [name for name in only if name not in BY_NAME]
    if unknown:
        raise SystemExit(f"未知的模型名：{', '.join(unknown)}（可用：{', '.join(BY_NAME)}）")
    return [BY_NAME[name] for name in only]


def render_plan(base: Path | None = None) -> str:
    """渲染目标清单（--list 与日志共用）。"""
    lines = [f"{'名称':<9}{'大小':>8}  目录 / 用途"]
    total = 0
    for target in PLAN:
        dest = resolve_target_dir(target, base)
        # 用 ASCII 标记：Windows 控制台默认 GBK，✅ 会抛 UnicodeEncodeError
        mark = "[ok]" if is_downloaded(target, base) else "    "
        total += target.approx_mb
        lines.append(f"{mark} {target.name:<8}{target.approx_mb:>6} MB  {target.target_dir}")
        lines.append(f"{'':<9}{'':>8}  └ {target.title}：{target.usage}")
    lines.append(f"合计约 {total} MB（--only 可只下其中一个）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下载 FlowMask 需要的模型权重（可选、按需）")
    parser.add_argument("--list", action="store_true", help="列出可下载的模型与用途后退出")
    parser.add_argument("--only", nargs="+", metavar="NAME", help="只下指定模型（l3 / ner / l2-base / l3-guard）")
    parser.add_argument("--all", action="store_true", help="下载全部目标（含训练基座，约 6.4 GB）")
    parser.add_argument("--dest", metavar="DIR", help="自定义落地目录（默认 server/models/…）")
    parser.add_argument("--force", action="store_true", help="已存在也重新下载")
    parser.add_argument("--dry-run", action="store_true", help="只显示将要做什么，不实际下载")
    args = parser.parse_args(argv)

    base = Path(args.dest).resolve() if args.dest else None
    if args.list:
        print(render_plan(base))
        return 0

    targets = select_targets(args.only, args.all)
    print(render_plan(base))
    failures = 0
    for target in targets:
        dest = resolve_target_dir(target, base)
        if not args.force and is_downloaded(target, base):
            print(f"[skip] {target.name} 已存在：{dest}")
            continue
        if args.dry_run:
            print(f"[dry]  将下载 {target.model_id}（{target.source}）→ {dest}")
            continue
        print(f"[get]  {target.model_id}（约 {target.approx_mb} MB）→ {dest}")
        try:
            download(target, base, force=args.force)
        except RuntimeError as exc:
            failures += 1
            print(f"[fail] {target.name}: {exc}", file=sys.stderr)
            continue
        print(f"[ok]   {target.name} → {dest}")
    if failures:
        print(f"{failures} 个目标未完成，请按上面的提示装依赖或手动下载。", file=sys.stderr)
        return 2
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
