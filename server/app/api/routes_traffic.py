"""实时旁路：接收外部采集器推送的正文（ingest），以及跟随持续写入的 pcap（live）。

设计取舍：
- **ingest** 给外部采集端（Go netmon / 自研 agent / tcpdump+解析脚本）用：只要按契约推
  `{path, text}` 列表，就能复用同一套识别/脱敏/落库/审计，不需要在采集侧重复实现规则。
- **live** 是服务端自己跟随一个持续增长的 pcap：适合"Wireshark 持续写盘 / dumpcap -w"的场景，
  与离线导入同构（每窗口一个任务），演示时能看到任务/看板/审计实时长出来。
- 安全边界：跟随的文件路径必须位于数据目录或自带示例目录下，避免变成任意文件读取接口。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status

from ..core.config import Settings
from ..core.db import Database
from ..identify import DetectionPipeline
from ..parsers.base import Document, Segment
from ..tasks.service import create_task
from ..traffic import PcapError, TrafficParseError, build_document
from ..traffic.live import LiveError, LiveStats, get_live_manager
from .deps import build_options, db_dep, operator_dep, pipeline_dep, settings_dep
from .schemas import (
    LiveListOut,
    LiveOut,
    LiveStartIn,
    TrafficFormatsOut,
    TrafficImportOut,
    TrafficIngestIn,
    TrafficIngestOut,
)

router = APIRouter(tags=["traffic"])

SERVER_DIR = Path(__file__).resolve().parents[2]
# 自带示例抓包（server/samples/demo-traffic.pcap）：前端「导入示例」与文档演示共用
SAMPLE_PCAP = SERVER_DIR / "samples" / "demo-traffic.pcap"
# 实时跟随允许的目录：数据目录（默认 server/data）与自带示例目录
_SAMPLES_DIR = SERVER_DIR / "samples"


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


@router.post(
    "/traffic/ingest",
    response_model=TrafficIngestOut,
    status_code=status.HTTP_201_CREATED,
    summary="接收外部采集器推送的正文（旁路 agent / Go 采集端）",
)
def ingest_traffic(
    payload: TrafficIngestIn,
    db: Database = Depends(db_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
    operator=Depends(operator_dep),
) -> TrafficIngestOut:
    segments = [
        Segment(path=item.path, text=item.text, hint=item.hint) for item in payload.segments
    ]
    document = Document(
        source=f"ingest:{payload.source}",
        content_type=payload.content_type,
        segments=segments,
        raw="",
        meta={"format": "ingest", "source": payload.source, "segments": len(segments)},
    )
    options = build_options(
        enable_l2=payload.enable_l2,
        enable_l3=payload.enable_l3,
        mask_level=payload.mask_level,
        min_level=payload.min_level,
    )
    task_id = create_task(
        db,
        source=f"ingest:{payload.source}",
        content_type=payload.content_type,
        document=document,
        options=options,
        pipeline=pipeline,
    )
    return TrafficIngestOut(
        task_id=task_id,
        source=payload.source,
        segments=len(segments),
        operator_id=operator.operator_id,
    )


def _resolve_live_path(raw: str, settings: Settings) -> Path:
    """把请求里的路径限制在数据目录/示例目录内，避免变成任意文件读取。"""
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = settings.data_dir / candidate
    resolved = candidate.resolve()
    allowed_roots = [settings.data_dir.resolve(), _SAMPLES_DIR.resolve()]
    for root in allowed_roots:
        if resolved == root or root in resolved.parents:
            return resolved
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="实时跟随时抓包文件必须位于数据目录或其子目录下（如 data/live.pcap）",
    )


def _live_out(stats: LiveStats) -> LiveOut:
    return LiveOut(
        live_id=stats.live_id,
        pcap_path=stats.pcap_path,
        source=stats.source,
        running=stats.running,
        started_at=datetime.fromtimestamp(stats.started_at, tz=timezone.utc),
        stopped_at=(
            datetime.fromtimestamp(stats.stopped_at, tz=timezone.utc)
            if stats.stopped_at is not None
            else None
        ),
        interval_ms=stats.interval_ms,
        window_s=stats.window_s,
        packets=stats.packets,
        segments=stats.segments,
        tasks=stats.tasks,
        last_task_id=stats.last_task_id,
        last_error=stats.last_error,
    )


@router.get("/traffic/live", response_model=LiveListOut, summary="实时旁路跟随状态")
def live_list() -> LiveListOut:
    manager = get_live_manager()
    return LiveListOut(sessions=[_live_out(item) for item in manager.list()])


@router.post(
    "/traffic/live/start",
    response_model=LiveOut,
    status_code=status.HTTP_201_CREATED,
    summary="开始跟随持续写入的 pcap（每 window_s 秒生成一个检测任务）",
)
def live_start(
    payload: LiveStartIn,
    settings: Settings = Depends(settings_dep),
    db: Database = Depends(db_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
) -> LiveOut:
    path = _resolve_live_path(payload.pcap_path, settings)
    if path.exists() and not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="该路径不是文件"
        )
    options = build_options(
        enable_l2=payload.enable_l2,
        enable_l3=payload.enable_l3,
        mask_level=payload.mask_level,
        min_level=payload.min_level,
    )
    try:
        stats = get_live_manager().start(
            db=db,
            pipeline=pipeline,
            pcap_path=path,
            interval_ms=payload.interval_ms,
            window_s=payload.window_s,
            options=options,
        )
    except LiveError as exc:
        # 已在跟随 → 409；其余（如写路径不可用）→ 400
        code = (
            status.HTTP_409_CONFLICT
            if "已在跟随中" in str(exc)
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return _live_out(stats)


@router.post("/traffic/live/{live_id}/stop", response_model=LiveOut, summary="停止实时跟随")
def live_stop(live_id: str) -> LiveOut:
    stats = get_live_manager().stop(live_id)
    if stats is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="没有这个实时跟随会话")
    return _live_out(stats)


@router.delete("/traffic/live", summary="清空已停止的实时跟随记录")
def live_clear() -> dict[str, int]:
    return {"cleared": get_live_manager().clear_stopped()}
