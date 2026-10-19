"""流量导入接口：上传 pcap → 解析正文 → 走同一套识别/脱敏/落库/审计。

设计取舍：不是"另起一套流量存储"，而是把每个 HTTP 正文当成文档片段（Segment），
直接复用 ``tasks.create_task``。好处是检测结果天然出现在任务页、看板与审计链里，
前端也只需要一套展示逻辑。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status

from ..core.config import Settings
from ..core.db import Database
from ..identify import DetectionPipeline
from ..tasks.service import create_task
from ..traffic import PcapError, TrafficParseError, build_document
from .deps import build_options, db_dep, operator_dep, pipeline_dep, settings_dep
from .schemas import TrafficFormatsOut, TrafficImportOut

router = APIRouter(tags=["traffic"])

# 自带示例抓包（server/samples/demo-traffic.pcap）：前端「导入示例」与文档演示共用
SAMPLE_PCAP = Path(__file__).resolve().parents[2] / "samples" / "demo-traffic.pcap"


@router.get("/traffic/formats", response_model=TrafficFormatsOut, summary="流量导入支持的格式")
def traffic_formats(settings: Settings = Depends(settings_dep)) -> TrafficFormatsOut:
    return TrafficFormatsOut(
        formats=["pcap", "pcapng"],
        max_upload_mb=settings.max_upload_mb,
        note="按 HTTP/明文正文检测；HTTPS、压缩体与分块传输暂不解析",
    )


@router.get("/traffic/sample", summary="下载自带示例 pcap")
def traffic_sample() -> Response:
    if not SAMPLE_PCAP.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="示例抓包文件缺失")
    return Response(
        content=SAMPLE_PCAP.read_bytes(),
        media_type="application/vnd.tcpdump.pcap",
        headers={"Content-Disposition": 'attachment; filename="demo-traffic.pcap"'},
    )


@router.post(
    "/traffic/import",
    response_model=TrafficImportOut,
    status_code=status.HTTP_201_CREATED,
    summary="导入 pcap：解析正文并创建检测任务",
)
async def import_traffic(
    pcap: UploadFile = File(..., description="pcap / pcapng 文件"),
    enable_l2: bool = False,
    enable_l3: bool = False,
    mask_level: str | None = None,
    min_level: str = "SAFE",
    settings: Settings = Depends(settings_dep),
    db: Database = Depends(db_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
    operator=Depends(operator_dep),
) -> TrafficImportOut:
    data = await pcap.read()
    limit = settings.max_upload_mb * 1024 * 1024
    if len(data) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件超过上限 {settings.max_upload_mb} MB",
        )
    if not data:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="文件内容为空")

    name = pcap.filename or "upload.pcap"
    try:
        document, summary = build_document(data, source_name=name)
    except PcapError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
        ) from exc
    except TrafficParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    options = build_options(
        enable_l2=enable_l2, enable_l3=enable_l3, mask_level=mask_level, min_level=min_level
    )
    task_id = create_task(
        db,
        source=f"pcap:{name}",
        content_type="pcap",
        document=document,
        options=options,
        pipeline=pipeline,
    )
    return TrafficImportOut(
        task_id=task_id,
        source=f"pcap:{name}",
        packets=summary.packets,
        decoded_frames=summary.decoded,
        sessions=summary.sessions,
        messages=summary.messages,
        segments=summary.segments,
        operator_id=operator.operator_id,
    )
