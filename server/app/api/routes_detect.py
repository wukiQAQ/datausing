"""即时检测与即时导出：文本粘贴 / 文件上传 / 脱敏文件下载 / 密文还原。

这些接口**都不落库**，用于"脱敏前/后左右对照"的现场演示；需要留痕与审计时走
``POST /api/v1/tasks``（后台任务，写 findings + 审计链）。脱敏文件导出同理：原文只在
同一个请求的内存里，用脱敏策略重建后直接下载，服务器不保存原文。
"""

from __future__ import annotations

from datetime import datetime
from dataclasses import asdict
import json
import secrets
from pathlib import PurePath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status

from ..core.config import Settings
from ..identify import DetectionPipeline
from ..identify.masking import (
    DEFAULT_STRATEGY,
    ENCRYPTED_PATTERN,
    MASK_STRATEGIES,
    decrypt_encrypted_text,
)
from ..identify.pipeline import DocumentReport
from ..identify.rules import freeze_active_rules
from ..identify.snapshots import fingerprint, get_snapshot_store
from ..security import SecurityError
from ..parsers.base import Document, ParseError, ParseUnsupported
from ..parsers.registry import (
    IMAGE_EXTENSIONS,
    parse_bytes,
    parse_csv,
    parse_json,
    parse_sql,
    parse_text,
    parse_text_input,
    supported_formats,
)
from ..tasks.document_export import EXPORT_FORMATS, build_masked_bytes
from ..tasks.export import masked_document
from .deps import build_options, pipeline_dep, read_upload_limited, settings_dep
from .schemas import (
    DetectDecryptIn,
    DetectDecryptOut,
    DetectExportRequest,
    DetectOut,
    DetectRequest,
    MaskStrategiesOut,
    MaskStrategyOut,
    SegmentOut,
    SpanOut,
)

router = APIRouter(tags=["detect"])


def _to_out(report: DocumentReport, document: Document, snapshot=None) -> DetectOut:
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
                engine_runs=finding.engine_runs,
                reason=finding.reason,
                degraded=finding.degraded,
                direct=finding.direct,
                requires_full_mask=finding.requires_full_mask,
                spans=[SpanOut(**span) for span in finding.spans],
            )
            for finding in report.findings
        ],
        by_level=report.by_level,
        duration_ms=report.duration_ms,
        degraded=report.degraded,
        engine_runs=report.engine_runs,
        coverage=report.coverage,
        masked_document=masked_document(document, report),
        **({
            'result_id': snapshot.result_id, 'source_fingerprint': snapshot.source_fingerprint,
            'ruleset_revision': snapshot.ruleset_revision, 'policy_version': snapshot.policy_version,
            'effective_options': snapshot.options,
        } if snapshot else {}),
    )


def _owner(request: Request, response: Response | None = None) -> str:
    session = request.cookies.get('flowmask_detection_session', '')
    if not session and response is not None:
        session = secrets.token_urlsafe(32)
        response.set_cookie('flowmask_detection_session', session, httponly=True,
                            samesite='strict', path='/api/v1', secure=request.url.scheme == 'https')
    return fingerprint(session + '|' + request.headers.get('authorization', '')) if session else ''


def _analyze_snapshot(document, options, pipeline, source_hash, request, response, settings):
    try:
        with freeze_active_rules() as rules:
            revision = fingerprint(json.dumps([
                (r.name, r.type.value, r.pattern.pattern, r.weight, r.confidence) for r in rules
            ], ensure_ascii=False))
            report = pipeline.analyze_document(document, options)
        values = asdict(options)
        values['mask_level'] = options.mask_level.value if options.mask_level else None
        values['min_level'] = options.min_level.value
        snapshot = get_snapshot_store().put(document, report, values, source_hash, revision,
                                           _owner(request, response), settings.export_secret)
        return _to_out(report, document, snapshot)
    except SecurityError:
        raise
    except Exception:
        raise SecurityError('DETECTION_FAILED', '检测失败，未返回原文', 503) from None


@router.post("/detect", response_model=DetectOut, summary="即时检测：粘贴文本")
def detect_text(
    payload: DetectRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(settings_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
) -> DetectOut:
    try:
        kind = payload.content_type.strip().lower()
        document = (_EXPORT_PARSERS[kind](payload.text) if kind in ('json', 'csv', 'sql')
                    else parse_text_input(payload.text, source='text'))
    except (ParseError, ValueError):
        raise SecurityError('SOURCE_PARSE_FAILED', '输入不符合指定格式') from None
    options = build_options(
        settings=settings,
        enable_l2=payload.enable_l2,
        enable_l3=payload.enable_l3,
        enable_ner=settings.enable_ner if payload.enable_ner is None else payload.enable_ner,
        enable_keywords=payload.enable_keywords,
        mask_level=payload.mask_level,
        min_level=payload.min_level,
    )
    return _analyze_snapshot(document, options, pipeline, fingerprint(payload.text), request, response, settings)


@router.post("/detect/file", response_model=DetectOut, summary="即时检测：上传文件（txt/json/csv/sql/docx/pdf/xlsx）")
async def detect_file(
    request: Request,
    response: Response,
    file: UploadFile = File(..., description="待检测文件"),
    enable_l2: bool | None = None,
    enable_l3: bool | None = None,
    enable_ner: bool | None = None,
    enable_keywords: bool | None = None,
    mask_level: str | None = None,
    min_level: str = "SAFE",
    settings: Settings = Depends(settings_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
) -> DetectOut:
    data = await read_upload_limited(file, settings.max_upload_mb * 1024 * 1024)
    try:
        document = parse_bytes(file.filename or "", data)
    except ParseUnsupported as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from exc
    except ParseError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    options = build_options(
        settings=settings,
        enable_l2=enable_l2,
        enable_l3=enable_l3,
        enable_ner=settings.enable_ner if enable_ner is None else enable_ner,
        enable_keywords=enable_keywords,
        mask_level=mask_level,
        min_level=min_level,
    )
    return _analyze_snapshot(document, options, pipeline, fingerprint(data), request, response, settings)


@router.get("/formats", summary="支持的输入格式", response_model=None)
def formats() -> dict[str, object]:
    return supported_formats()


# ---------------------------------------------------------------------------
# 第一次优化：脱敏策略清单 / 脱敏文件导出 / 密文还原
# ---------------------------------------------------------------------------

#: 导出请求里 ``content_type`` → 对应解析器（docx/pdf 只能走上传入口）
_EXPORT_PARSERS = {
    "text": lambda text: parse_text(text, source="export.txt"),
    "json": lambda text: parse_json(text, source="export.json"),
    "csv": lambda text: parse_csv(text, source="export.csv"),
    "sql": lambda text: parse_sql(text, source="export.sql"),
}

_UNPROCESSABLE_EXPORT_DETAIL = "请求内容无法处理（格式与 content_type 不一致或为空）"


@router.get(
    "/mask-strategies",
    response_model=MaskStrategiesOut,
    summary="支持的脱敏策略（前端下拉框文案来源）",
)
def mask_strategies() -> MaskStrategiesOut:
    """六种脱敏策略的元数据；示例值均为虚构数据，可直接展示给用户。"""
    return MaskStrategiesOut(
        strategies=[MaskStrategyOut(**meta) for meta in MASK_STRATEGIES.values()],
        default=DEFAULT_STRATEGY,
    )


def _validate_export_options(strategy: str, fmt: str) -> tuple[str, str]:
    """校验导出的策略 / 格式（返回归一化后的键）。未知值直接 400，不回显请求内容。"""
    strategy_key = (strategy or "").strip().lower()
    if strategy_key not in MASK_STRATEGIES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的脱敏策略")
    fmt_key = (fmt or "auto").strip().lower()
    if fmt_key != "auto" and fmt_key not in EXPORT_FORMATS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的输出格式")
    return strategy_key, fmt_key


def _export_secret_or_400(settings: Settings, strategy: str) -> str:
    """可逆加密策略必须有导出密钥；没有就 400（这是服务端配置问题，不静默降级）。"""
    if strategy == "encrypt" and not settings.export_secret:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="脱敏导出密钥未配置")
    return settings.export_secret


def _export_download(content: bytes, media_type: str, ext: str) -> Response:
    """包装成浏览器下载响应：附件名带时间戳，避免同名覆盖。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="flowmask-export-{stamp}.{ext}"',
                 'X-FlowMask-Detection-Mode': 'fresh'},
    )


def _analyze_export_document(
    document: Document,
    *,
    settings: Settings,
    pipeline: DetectionPipeline,
    enable_l2: bool | None,
    enable_l3: bool | None,
    enable_ner: bool | None,
    enable_keywords: bool | None,
) -> DocumentReport:
    """导出用的在线分析：``min_level="SAFE"`` 保证拿回全量 finding，不受级别筛选影响。"""
    options = build_options(
        settings=settings,
        enable_l2=enable_l2,
        enable_l3=enable_l3,
        enable_ner=settings.enable_ner if enable_ner is None else enable_ner,
        enable_keywords=enable_keywords,
        mask_level=None,
        min_level="SAFE",
    )
    try:
        with freeze_active_rules():
            return pipeline.analyze_document(document, options)
    except SecurityError:
        raise
    except Exception:
        raise SecurityError('DETECTION_FAILED', '检测失败，未返回原文', 503) from None


def _snapshot_download(request, settings, result_id, policy_version, strategy, fmt, source_hash=None, payload=None, requested_options=None):
    snapshot, masked = get_snapshot_store().render(
        result_id, _owner(request), settings.export_secret, strategy, policy_version)
    if source_hash is not None and source_hash != snapshot.source_fingerprint:
        raise SecurityError('SNAPSHOT_SOURCE_CHANGED', '输入与检测快照不一致，请重新检测', 409)
    for key, value in (requested_options or {}).items():
        if value is not None and value != snapshot.options.get(key):
            raise SecurityError('SNAPSHOT_OPTIONS_CHANGED', '检测选项已变化，请重新检测', 409)
    if payload is not None:
        for key in ('enable_l2', 'enable_l3', 'enable_ner', 'enable_keywords', 'min_level'):
            value = getattr(payload, key)
            if key in ('enable_l2', 'enable_l3') and value is None:
                continue
            if key in payload.model_fields_set and value != snapshot.options.get(key):
                raise SecurityError('SNAPSHOT_OPTIONS_CHANGED', '检测选项已变化，请重新检测', 409)
        if 'content_type' in payload.model_fields_set and payload.content_type != snapshot.document.content_type:
            raise SecurityError('SNAPSHOT_SOURCE_CHANGED', '输入格式与检测快照不一致', 409)
    content, media_type, ext = build_masked_bytes(snapshot.document, snapshot.report, strategy=strategy,
                                                fmt=fmt, secret=settings.export_secret, masked_values=masked)
    response = _export_download(content, media_type, ext)
    response.headers['X-FlowMask-Result-Id'] = snapshot.result_id
    response.headers['X-FlowMask-Policy-Version'] = snapshot.policy_version
    response.headers['X-FlowMask-Detection-Mode'] = 'snapshot'
    return response


@router.post('/detect/results/{result_id}/preview', response_model=DetectOut)
def preview_snapshot(result_id: str, payload: DetectExportRequest, request: Request,
                     settings: Settings = Depends(settings_dep)):
    strategy, _ = _validate_export_options(payload.strategy, 'auto')
    snapshot, masked = get_snapshot_store().render(
        result_id, _owner(request), settings.export_secret, strategy, payload.policy_version)
    for finding in snapshot.report.all_findings or snapshot.report.findings:
        finding.masked = masked[finding.path]
    return _to_out(snapshot.report, snapshot.document, snapshot)


@router.post("/detect/export", summary="即时导出脱敏文件：粘贴文本/JSON/CSV/SQL")
def detect_export(
    payload: DetectExportRequest,
    request: Request,
    settings: Settings = Depends(settings_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
) -> Response:
    """同一请求内完成"分析 → 按策略重建 → 下载"，原文不落库。"""
    strategy_key, fmt_key = _validate_export_options(payload.strategy, payload.fmt)
    secret = _export_secret_or_400(settings, strategy_key)
    if payload.result_id:
        return _snapshot_download(request, settings, payload.result_id, payload.policy_version,
                                  strategy_key, fmt_key, fingerprint(payload.text) if payload.text is not None else None,
                                  payload)
    if payload.text is None:
        raise SecurityError('SOURCE_REQUIRED', '请提供原文或有效检测快照')
    parser = _EXPORT_PARSERS.get((payload.content_type or "text").strip().lower())
    if parser is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_UNPROCESSABLE_EXPORT_DETAIL)
    try:
        document = parser(payload.text)
    except (ParseError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_UNPROCESSABLE_EXPORT_DETAIL) from exc
    report = _analyze_export_document(
        document,
        settings=settings,
        pipeline=pipeline,
        enable_l2=payload.enable_l2,
        enable_l3=payload.enable_l3,
        enable_ner=payload.enable_ner,
        enable_keywords=payload.enable_keywords,
    )
    try:
        content, media_type, ext = build_masked_bytes(
            document, report, strategy=strategy_key, fmt=fmt_key, secret=secret
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    response = _export_download(content, media_type, ext)
    response.headers['X-FlowMask-Detection-Mode'] = 'fresh'
    return response


@router.post("/detect/decrypt", response_model=DetectDecryptOut, summary="还原加密策略导出的密文片段")
def detect_decrypt(
    payload: DetectDecryptIn,
    settings: Settings = Depends(settings_dep),
) -> DetectDecryptOut:
    """只还原本机导出密钥加密的 ``enc:`` 片段；错误信息不回显密文或明文。"""
    if not ENCRYPTED_PATTERN.search(payload.ciphertext):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="没有可还原的密文片段")
    try:
        text, replaced = decrypt_encrypted_text(payload.ciphertext, settings.export_secret)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return DetectDecryptOut(text=text, replaced=replaced)


@router.post("/detect/file/export", summary="即时导出脱敏文件：上传文件")
async def detect_file_export(
    request: Request,
    file: UploadFile | None = File(default=None, description="待脱敏导出的文件"),
    strategy: str = Form("partial", description="脱敏策略：block / partial / hash / generalize / pseudonym / encrypt"),
    fmt: str = Form("auto", description="输出格式：auto / txt / json / csv / sql / docx / xlsx"),
    enable_l2: bool | None = Form(None),
    enable_l3: bool | None = Form(None),
    enable_ner: bool | None = Form(None),
    enable_keywords: bool | None = Form(None),
    result_id: str | None = Form(default=None, max_length=128),
    policy_version: str | None = Form(default=None, max_length=128),
    settings: Settings = Depends(settings_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
) -> Response:
    """上传 → 解析 → 分析 → 按策略重建 → 下载；图片需要先装 OCR 依赖。"""
    strategy_key, fmt_key = _validate_export_options(strategy, fmt)
    if result_id:
        data = await read_upload_limited(file, settings.max_upload_mb * 1024 * 1024) if file else None
        submitted = await request.form()
        options = {'enable_l2': enable_l2, 'enable_l3': enable_l3,
                   'enable_ner': enable_ner, 'enable_keywords': enable_keywords}
        return _snapshot_download(request, settings, result_id, policy_version, strategy_key, fmt_key,
                                  fingerprint(data) if data is not None else None,
                                  requested_options={key: value for key, value in options.items() if key in submitted})
    if file is None:
        raise SecurityError('SOURCE_REQUIRED', '请提供文件或有效检测快照')
    suffix = PurePath(file.filename or "").suffix.lower()
    if suffix in IMAGE_EXTENSIONS or (file.content_type or "").startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="图片文件暂不支持导出（需先安装 OCR 可选依赖）",
        )
    strategy_key, fmt_key = _validate_export_options(strategy, fmt)
    secret = _export_secret_or_400(settings, strategy_key)
    data = await read_upload_limited(file, settings.max_upload_mb * 1024 * 1024)
    try:
        document = parse_bytes(file.filename or "", data)
    except ParseUnsupported as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="不支持的文件类型") from exc
    except ParseError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    report = _analyze_export_document(
        document,
        settings=settings,
        pipeline=pipeline,
        enable_l2=enable_l2,
        enable_l3=enable_l3,
        enable_ner=enable_ner,
        enable_keywords=enable_keywords,
    )
    try:
        content, media_type, ext = build_masked_bytes(
            document, report, strategy=strategy_key, fmt=fmt_key, secret=secret
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _export_download(content, media_type, ext)
