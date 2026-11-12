"""FastAPI 依赖：配置、数据库、鉴权、操作人、流水线。

流水线对象在进程内只建一次（含 L2/L3 客户端，避免每次请求新建连接池）。
测试里要换配置时，调用 ``reset_pipeline_for_tests()``。
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, UploadFile, status

from ..core.config import Settings, get_settings
from ..core.db import Database, get_database
from ..identify import DetectionPipeline, DetectOptions, Level


def db_dep() -> Database:
    return get_database()


def settings_dep() -> Settings:
    return get_settings()


def require_token(
    settings: Settings = Depends(settings_dep),
    authorization: str | None = Header(default=None),
) -> None:
    """配置了 ``api_token`` 时校验 ``Authorization: Bearer <token>``；留空则本机演示不鉴权。"""
    token = settings.api_token
    if not token:
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少 Bearer Token")
    provided = authorization.split(" ", 1)[1].strip()
    if not hmac.compare_digest(provided, token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer Token 不正确")


@dataclass(frozen=True, slots=True)
class Operator:
    """审计记录里的操作人身份（从请求头取，缺省为 system/local）。"""

    operator_id: str
    user_id: str


def _clean_identity(value: str | None, header: str) -> str | None:
    """校验请求头里的操作人/用户标识：不静默截断，拒绝控制字符与超长值。"""
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > 64:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{header} 最长 64 个字符")
    if any(ch < " " or ch == "\x7f" for ch in cleaned):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{header} 不能包含控制字符")
    return cleaned


def operator_dep(
    x_operator_id: str | None = Header(default=None, alias="X-Operator-Id"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> Operator:
    return Operator(
        operator_id=_clean_identity(x_operator_id, "X-Operator-Id") or "system",
        user_id=_clean_identity(x_user_id, "X-User-Id") or "local",
    )


def reject_safe_mask_level(mask_level: str | None) -> None:
    """落库类接口禁止 ``mask_level=SAFE``：库里只存脱敏副本，SAFE 会把原文写进 findings。"""
    if (mask_level or "").strip().upper() == "SAFE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="任务会落库脱敏副本，mask_level 不能为 SAFE；不脱敏仅支持 /detect 即时检测",
        )


_UPLOAD_CHUNK = 64 * 1024
# 新版本 Starlette 把常量改名为 HTTP_413_CONTENT_TOO_LARGE，旧版本没有，取个兼容值
_PAYLOAD_TOO_LARGE = getattr(status, "HTTP_413_CONTENT_TOO_LARGE", 413)


async def read_upload_limited(upload: UploadFile, max_bytes: int) -> bytes:
    """分块读上传文件，超过 ``max_bytes`` 立即 413，不把超大文件整个读进内存。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=_PAYLOAD_TOO_LARGE,
                detail=f"文件超过上限 {max_bytes // (1024 * 1024)} MB",
            )
        chunks.append(chunk)
    return b"".join(chunks)


_pipeline: DetectionPipeline | None = None


def pipeline_dep(settings: Settings = Depends(settings_dep)) -> DetectionPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = DetectionPipeline.from_settings(settings)
    return _pipeline


def reset_pipeline_for_tests() -> None:
    global _pipeline
    _pipeline = None


def build_options(
    *,
    settings: Settings,
    enable_l2: bool | None = None,
    enable_l3: bool | None = None,
    enable_ner: bool = False,
    enable_keywords: bool | None = None,
    mask_level: str | None = None,
    min_level: str = "SAFE",
) -> DetectOptions:
    """把接口参数转成检测开关；级别字符串非法时返回 400 而不是 500。"""
    try:
        parsed_mask = Level.parse(mask_level) if mask_level else None
        parsed_min = Level.parse(min_level)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return DetectOptions(
        enable_l2=settings.enable_l2 if enable_l2 is None else enable_l2,
        enable_l3=settings.enable_l3 if enable_l3 is None else enable_l3,
        enable_ner=enable_ner,
        enable_keywords=enable_keywords,
        mask_level=parsed_mask,
        min_level=parsed_min,
    )
