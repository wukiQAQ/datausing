"""FastAPI 依赖：配置、数据库、鉴权、操作人、流水线。

流水线对象在进程内只建一次（含 L2/L3 客户端，避免每次请求新建连接池）。
测试里要换配置时，调用 ``reset_pipeline_for_tests()``。
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status

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


def operator_dep(
    x_operator_id: str | None = Header(default=None, alias="X-Operator-Id"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> Operator:
    return Operator(operator_id=(x_operator_id or "system")[:64], user_id=(x_user_id or "local")[:64])


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
    enable_l2: bool = False,
    enable_l3: bool = False,
    enable_ner: bool = False,
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
        enable_l2=enable_l2,
        enable_l3=enable_l3,
        enable_ner=enable_ner,
        mask_level=parsed_mask,
        min_level=parsed_min,
    )
