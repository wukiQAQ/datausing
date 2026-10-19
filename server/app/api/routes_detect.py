"""即时检测：文本粘贴 / 文件上传。

这两个接口**不落库**，用于"脱敏前/后左右对照"的现场演示；
需要留痕与审计时走 ``POST /api/v1/tasks``（后台任务，写 findings + 审计链）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from ..core.config import Settings
from ..identify import DetectionPipeline
from ..identify.pipeline import DocumentReport
from ..parsers.base import Document, ParseError, ParseUnsupported
from ..parsers.registry import parse_bytes, parse_text_input, supported_formats
from ..tasks.export import masked_document
from .deps import build_options, pipeline_dep, settings_dep
from .schemas import DetectOut, DetectRequest, SegmentOut

router = APIRouter(tags=["detect"])


def _to_out(report: DocumentReport, document: Document) -> DetectOut:
    return DetectOut(
        content_type=document.content_type,
        segments=report.segments,
        findings=[
            SegmentOut(
                path=finding.path,
                level=finding.level.value,
                score=finding.score,
                types=finding.types,
                hits=finding.hits,
                masked=finding.masked,
                model_chain=finding.model_chain,
                reason=finding.reason,
                degraded=finding.degraded,
                direct=finding.direct,
            )
            for finding in report.findings
        ],
        by_level=report.by_level,
        duration_ms=report.duration_ms,
        degraded=report.degraded,
        masked_document=masked_document(document, report),
    )


@router.post("/detect", response_model=DetectOut, summary="即时检测：粘贴文本")
def detect_text(
    payload: DetectRequest,
    settings: Settings = Depends(settings_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
) -> DetectOut:
    document = parse_text_input(payload.text, source="text")
    options = build_options(
        enable_l2=payload.enable_l2 or settings.enable_l2,
        enable_l3=payload.enable_l3 or settings.enable_l3,
        mask_level=payload.mask_level,
        min_level=payload.min_level,
    )
    return _to_out(pipeline.analyze_document(document, options), document)


@router.post("/detect/file", response_model=DetectOut, summary="即时检测：上传文件（txt/json/csv/sql）")
async def detect_file(
    file: UploadFile = File(..., description="待检测文件"),
    enable_l2: bool = False,
    enable_l3: bool = False,
    mask_level: str | None = None,
    min_level: str = "SAFE",
    settings: Settings = Depends(settings_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
) -> DetectOut:
    data = await file.read()
    limit = settings.max_upload_mb * 1024 * 1024
    if len(data) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件超过上限 {settings.max_upload_mb} MB",
        )
    try:
        document = parse_bytes(file.filename or "", data)
    except ParseUnsupported as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from exc
    except ParseError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    options = build_options(
        enable_l2=enable_l2 or settings.enable_l2,
        enable_l3=enable_l3 or settings.enable_l3,
        mask_level=mask_level,
        min_level=min_level,
    )
    return _to_out(pipeline.analyze_document(document, options), document)


@router.get("/formats", summary="支持的输入格式")
def formats() -> dict[str, list[str]]:
    return supported_formats()
