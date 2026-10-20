"""把 HuggingFace 多标签分类模型导出为 L2 服务可用的 ONNX（含 int8 量化）。

本脚本不参与默认安装（需要 torch/transformers/onnx），只在准备真模型时用一次：

    pip install torch transformers onnx onnxruntime onnxruntime-tools
    python -m l2_service.export_onnx \
        --model-dir D:/models/roberta-wwm-sens \
        --out D:/models/roberta-wwm-sens-onnx

导出产物（与 ``OnnxBackend`` 约定的目录结构一致）：

    model.onnx / model.int8.onnx   ONNX 计算图
    tokenizer.json                 fast tokenizer（可与权重同目录复制）
    labels.json                    类别顺序，与模型输出维度对齐

模型输入约定：``input_ids`` + ``attention_mask``（自动带 ``token_type_ids``），
输出：``logits`` [batch, num_labels]；多标签用 sigmoid，单标签为 softmax。
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="导出 L2 多标签分类模型为 ONNX（可选 int8）")
    parser.add_argument("--model-dir", type=Path, required=True, help="HF 权重目录（含 config.json）")
    parser.add_argument("--out", type=Path, required=True, help="导出目录")
    parser.add_argument("--max-length", type=int, default=128)
    parser.add_argument("--no-quantize", action="store_true", help="跳过 int8 动态量化")
    parser.add_argument("--labels", default="", help="逗号分隔的类别顺序；留空则读 config.json 的 id2label")
    return parser


def _load_labels(args: argparse.Namespace, config: dict) -> list[str]:
    if args.labels:
        return [item.strip() for item in args.labels.split(",") if item.strip()]
    # 训练脚本会把类别顺序写成 labels.json，优先用它：
    # 自己微调出来的 config.id2label 可能是「类别名 -> 序号」，不能假定键是数字。
    labels_file = args.model_dir / "labels.json"
    if labels_file.is_file():
        try:
            data = json.loads(labels_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, list) and data:
            return [str(item) for item in data]
    id2label = config.get("id2label") or {}
    if id2label:
        try:
            return [id2label[str(index)] for index in sorted(int(key) for key in id2label)]
        except (KeyError, ValueError):
            raise SystemExit(
                "config.json 的 id2label 不是「序号 -> 类别名」格式；"
                "请在模型目录补 labels.json，或用 --labels 指定类别顺序"
            ) from None
    raise SystemExit("无法确定类别顺序：请用 --labels 或保证 config.json 里有 id2label")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        import torch  # noqa: PLC0415
        from transformers import AutoConfig, AutoModelForSequenceClassification, AutoTokenizer  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - 可选依赖
        raise SystemExit(
            f"缺少导出依赖（{exc}）。先执行：pip install torch transformers onnx onnxruntime onnxruntime-tools"
        ) from exc

    args.out.mkdir(parents=True, exist_ok=True)
    config = AutoConfig.from_pretrained(str(args.model_dir))
    labels = _load_labels(args, config.to_dict())
    tokenizer = AutoTokenizer.from_pretrained(str(args.model_dir))
    model = AutoModelForSequenceClassification.from_pretrained(str(args.model_dir))
    model.eval()

    dummy = tokenizer(
        ["测试文本"], return_tensors="pt", truncation=True, max_length=args.max_length, padding="max_length"
    )
    onnx_path = args.out / "model.onnx"
    torch.onnx.export(
        model,
        (dummy["input_ids"], dummy["attention_mask"]),
        str(onnx_path),
        input_names=["input_ids", "attention_mask"],
        output_names=["logits"],
        dynamic_axes={"input_ids": {0: "batch", 1: "seq"}, "attention_mask": {0: "batch", 1: "seq"}},
        opset_version=17,
        # 用旧版追踪式导出器：动态量化（quantize_dynamic）对 dynamo 导出的
        # MatMul/外部权重格式不兼容，会报 ShapeInferenceError。
        dynamo=False,
    )
    print(f"导出完成：{onnx_path}")

    if not args.no_quantize:  # pragma: no cover - 可选依赖
        from onnxruntime.quantization import QuantType, quantize_dynamic  # noqa: PLC0415

        int8_path = args.out / "model.int8.onnx"
        # per_channel 不能省：按张量（per-tensor）量化在中文 RoBERTa-wwm 上会把手写
        # bridge/query 权重压坏，实测输出全空（phone/id_card 置信度掉到 0.0x），
        # 而 per_channel 后与 fp32 基本一致（phone 0.915 vs 0.917）。
        quantize_dynamic(
            str(onnx_path),
            str(int8_path),
            weight_type=QuantType.QInt8,
            per_channel=True,
        )
        print(f"int8 量化完成：{int8_path}（体积约缩为 1/4，CPU 推理约 5-20ms）")

    tokenizer.backend_tokenizer.save(str(args.out / "tokenizer.json"))
    (args.out / "labels.json").write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    # 便于回溯：把配置一起带上
    shutil.copy2(args.model_dir / "config.json", args.out / "config.json")
    print(f"类别顺序：{labels}")
    print(f"产物目录：{args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
