"""任务接口：提交（文件/文本）→ 轮询状态 → 查结果 → 导出。

任务异步执行（后台线程），因为 Word/PDF 解析与 L2/L3 推理可能耗时数秒；
接口立刻返回 ``task_id``，前端用 1~2 秒轮询即可，演示时不会"页面卡住"。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status

from ..core.config import Settings
from ..core.db import Database
from ..identify import DetectionPipeline
from ..parsers.base import ParseError, ParseUnsupported
from ..parsers.registry import parse_bytes, parse_text_input
from ..privacy import DEFAULT_EPSILON, DEFAULT_L, export_with_privacy
from ..tasks.export import EXPORT_FORMATS, export_findings
from ..tasks.service import create_task, get_task, list_tasks, page_findings
from .deps import build_options, db_dep, operator_dep, pipeline_dep, read_upload_limited, reject_safe_mask_level, settings_dep
from .schemas import BatchItemOut, BatchTasksOut, FindingOut, FindingPage, TaskCreateOut, TaskOut, TaskPage, TextTaskIn

router = APIRouter(tags=["tasks"])

# 批量上传一次最多接受的文件数：再多就让用户分批，避免一个请求里排队几十个后台任务
MAX_BATCH_FILES = 20


@router.post("/tasks", response_model=TaskCreateOut, summary="提交任务：上传文件")
async def create_file_task(
    file: UploadFile = File(..., description="txt/json/csv/sql（Word/PDF 待接入）"),
    enable_l2: bool | None = None,
    enable_l3: bool | None = None,
    enable_ner: bool = False,
    mask_level: str | None = None,
    min_level: str = "SAFE",
    settings: Settings = Depends(settings_dep),
    db: Database = Depends(db_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
) -> TaskCreateOut:
    reject_safe_mask_level(mask_level)
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
        enable_ner=enable_ner or settings.enable_ner,
        mask_level=mask_level,
        min_level=min_level,
    )
    task_id = create_task(
        db,
        source=file.filename or "upload",
        content_type=document.content_type,
        document=document,
        options=options,
        pipeline=pipeline,
    )
    return TaskCreateOut(task_id=task_id, status="pending")


@router.post("/tasks/text", response_model=TaskCreateOut, summary="提交任务：粘贴文本")
def create_text_task(
    payload: TextTaskIn,
    settings: Settings = Depends(settings_dep),
    db: Database = Depends(db_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
) -> TaskCreateOut:
    reject_safe_mask_level(payload.mask_level)
    text = payload.text
    if not text.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="text 不能为空")
    document = parse_text_input(text, source="text")
    options = build_options(
        settings=settings,
        enable_l2=payload.enable_l2,
        enable_l3=payload.enable_l3,
        enable_ner=payload.enable_ner or settings.enable_ner,
        mask_level=payload.mask_level,
        min_level=payload.min_level,
    )
    task_id = create_task(
        db,
        source="text",
        content_type=document.content_type,
        document=document,
        options=options,
        pipeline=pipeline,
    )
    return TaskCreateOut(task_id=task_id, status="pending")


@router.post(
    "/tasks/batch",
    response_model=BatchTasksOut,
    status_code=status.HTTP_201_CREATED,
    summary="批量提交任务：一次上传多个文件",
)
async def create_batch_tasks(
    request: Request,
    response: Response,
    files: list[UploadFile] = File(..., description=f"一次最多 {MAX_BATCH_FILES} 个文件"),
    enable_l2: bool | None = None,
    enable_l3: bool | None = None,
    enable_ner: bool = False,
    mask_level: str | None = None,
    min_level: str = "SAFE",
    settings: Settings = Depends(settings_dep),
    db: Database = Depends(db_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
) -> BatchTasksOut:
    """批量提交：每个文件一个任务，互不影响。

    - 单个文件解析失败只在结果里记 `error`，不影响同批其它文件（批量场景最怕一个坏文件全挂）；
    - 每个任务仍然走异步线程 + 落库 + 审计链，前端可用 `GET /tasks/{id}` 轮询进度。
    """
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="至少上传一个文件")
    reject_safe_mask_level(mask_level)
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"一次最多 {MAX_BATCH_FILES} 个文件，请分批提交",
        )

    options = build_options(
        settings=settings,
        enable_l2=enable_l2,
        enable_l3=enable_l3,
        enable_ner=enable_ner or settings.enable_ner,
        mask_level=mask_level,
        min_level=min_level,
    )
    limit = settings.max_upload_mb * 1024 * 1024
    from .routes_detect import _owner
    comparison_owner = _owner(request, response)
    items: list[BatchItemOut] = []
    for upload in files:
        name = upload.filename or "upload"
        try:
            data = await read_upload_limited(upload, limit)
        except HTTPException as exc:
            items.append(BatchItemOut(filename=name, error=str(exc.detail)))
            continue
        try:
            document = parse_bytes(name, data)
        except ParseUnsupported as exc:
            items.append(BatchItemOut(filename=name, error=str(exc)))
            continue
        except ParseError as exc:
            items.append(BatchItemOut(filename=name, error=f"解析失败：{exc}"))
            continue
        task_id = create_task(
            db,
            source=name,
            content_type=document.content_type,
            document=document,
            options=options,
            pipeline=pipeline,
            comparison_owner=comparison_owner,
            comparison_secret=settings.export_secret,
        )
        items.append(
            BatchItemOut(
                filename=name,
                task_id=task_id,
                content_type=document.content_type,
                segments=len(document.segments),
            )
        )

    accepted = sum(1 for item in items if item.task_id)
    return BatchTasksOut(items=items, total=len(items), accepted=accepted)


@router.get("/tasks", response_model=TaskPage, summary="任务列表（新的在前）")
def list_task(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Database = Depends(db_dep),
) -> TaskPage:
    items, total = list_tasks(db, limit=page_size, offset=(page - 1) * page_size)
    return TaskPage(items=[TaskOut(**item) for item in items], total=total, page=page, page_size=page_size)


@router.get("/tasks/{task_id}", response_model=TaskOut, summary="任务状态")
def task_detail(task_id: str, db: Database = Depends(db_dep)) -> TaskOut:
    task = get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return TaskOut(**task)


@router.get("/tasks/{task_id}/findings", response_model=FindingPage, summary="任务的检测结果")
def task_findings(
    task_id: str,
    level: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Database = Depends(db_dep),
) -> FindingPage:
    if get_task(db, task_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    items, total = page_findings(db, task_id=task_id, level=level, page=page, page_size=page_size)
    return FindingPage(items=[FindingOut(**item) for item in items], total=total, page=page, page_size=page_size)


@router.get(
    "/tasks/{task_id}/export/privacy",
    summary="隐私增强导出（Laplace 差分隐私计数 + l-多样性校验）",
)
def export_task_privacy(
    task_id: str,
    fmt: str = Query(default="jsonl", alias="format", description="jsonl / json"),
    epsilon: float = Query(default=DEFAULT_EPSILON, description="差分隐私预算（0.05~10，越小噪声越大）"),
    l: int = Query(default=DEFAULT_L, description="l-多样性下限（1~20）"),
    db: Database = Depends(db_dep),
) -> Response:
    if get_task(db, task_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    try:
        content, media_type, filename = export_with_privacy(db, task_id, fmt, epsilon, l)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-FlowMask-Privacy": "laplace+l-diversity",
        },
    )


@router.get("/tasks/{task_id}/export", summary="导出审计日志（jsonl/json/csv/sql）")
def export_task(
    task_id: str,
    fmt: str = Query(default="jsonl", alias="format", description="jsonl / json / csv / sql"),
    db: Database = Depends(db_dep),
) -> Response:
    if get_task(db, task_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的格式: {fmt}，可选 {', '.join(EXPORT_FORMATS)}",
        )
    content, media_type, filename = export_findings(db, task_id, fmt)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# 对比响应可能含原文，成功与失败路径都不得被缓存（错误路径见 security_error_response）。
_COMPARE_HEADERS = {'Cache-Control': 'no-store', 'Pragma': 'no-cache'}


@router.get('/tasks/{task_id}/comparison', summary='当前会话的临时文档对比')
def task_comparison(task_id: str, request: Request, response: Response,
                    settings: Settings = Depends(settings_dep), db: Database = Depends(db_dep)):
    from .routes_detect import _owner
    from ..identify.snapshots import get_snapshot_store
    from ..tasks.comparison import comparison_payload
    task = get_task(db, task_id)
    if task is None:
        raise HTTPException(404, '任务不存在', headers=_COMPARE_HEADERS)
    if task['status'] != 'done':
        raise HTTPException(409, '任务未完成或脱敏失败', headers=_COMPARE_HEADERS)
    response.headers.update(_COMPARE_HEADERS)
    snapshot = get_snapshot_store().get('task:' + task_id, _owner(request), settings.export_secret)
    return comparison_payload(snapshot)


@router.get('/tasks/{task_id}/comparison/download', summary='下载对比中的完整脱敏文本')
def download_comparison(task_id: str, request: Request, response: Response,
                        settings: Settings = Depends(settings_dep), db: Database = Depends(db_dep)):
    payload = task_comparison(task_id, request, response, settings, db)
    # 文本与对比片段逐字一致；结构化/Office 文件按解析片段顺序输出，不伪造原始排版。
    return Response('\n'.join(s['masked'] for s in payload['segments']), media_type='text/plain; charset=utf-8',
                    headers={**_COMPARE_HEADERS, 'Content-Disposition':
                             'attachment; filename="flowmask-masked.txt"'})
