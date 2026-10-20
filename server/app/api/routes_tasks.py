"""任务接口：提交（文件/文本）→ 轮询状态 → 查结果 → 导出。

任务异步执行（后台线程），因为 Word/PDF 解析与 L2/L3 推理可能耗时数秒；
接口立刻返回 ``task_id``，前端用 1~2 秒轮询即可，演示时不会"页面卡住"。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status

from ..core.config import Settings
from ..core.db import Database
from ..identify import DetectionPipeline
from ..parsers.base import ParseError, ParseUnsupported
from ..parsers.registry import parse_bytes, parse_text_input
from ..tasks.export import EXPORT_FORMATS, export_findings
from ..tasks.service import create_task, get_task, list_tasks, page_findings
from .deps import build_options, db_dep, operator_dep, pipeline_dep, settings_dep
from .schemas import FindingOut, FindingPage, TaskCreateOut, TaskOut, TaskPage

router = APIRouter(tags=["tasks"])


@router.post("/tasks", response_model=TaskCreateOut, summary="提交任务：上传文件")
async def create_file_task(
    file: UploadFile = File(..., description="txt/json/csv/sql（Word/PDF 待接入）"),
    enable_l2: bool = False,
    enable_l3: bool = False,
    enable_ner: bool = False,
    mask_level: str | None = None,
    min_level: str = "SAFE",
    settings: Settings = Depends(settings_dep),
    db: Database = Depends(db_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
) -> TaskCreateOut:
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
    payload: dict,
    settings: Settings = Depends(settings_dep),
    db: Database = Depends(db_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
) -> TaskCreateOut:
    text = str(payload.get("text") or "")
    if not text.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="text 不能为空")
    document = parse_text_input(text, source="text")
    options = build_options(
        enable_l2=bool(payload.get("enable_l2", False)) or settings.enable_l2,
        enable_l3=bool(payload.get("enable_l3", False)) or settings.enable_l3,
        enable_ner=bool(payload.get("enable_ner", False)) or settings.enable_ner,
        mask_level=payload.get("mask_level"),
        min_level=str(payload.get("min_level") or "SAFE"),
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
