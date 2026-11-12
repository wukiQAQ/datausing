"""识别与输出之间的安全边界：失败关闭，只记录稳定错误码和随机关联号。"""

from __future__ import annotations

import logging
import uuid

from fastapi.responses import JSONResponse

POLICY_VERSION = "protected-v1"
logger = logging.getLogger(__name__)


def safe_stored_mask(value: str) -> str:
    """旧L1警示不是脱敏副本；查询/导出时遮蔽，不改写用户历史库。"""
    return '[已屏蔽:历史低危副本]' if '[低危]' in value else value


class SecurityError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.error_id = uuid.uuid4().hex
        super().__init__(code)


async def security_error_response(_request, exc: SecurityError):
    logger.warning("%s error_id=%s", exc.code, exc.error_id)
    # 安全错误响应只含稳定错误码/关联号，不含原文；统一禁止缓存，
    # 避免 4xx（如 410 快照过期）被浏览器或代理启发式缓存后影响重试。
    return JSONResponse(status_code=exc.status_code, content={
        "detail": exc.message, "code": exc.code, "error_id": exc.error_id,
    }, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


async def validation_error_response(_request, exc):
    """Pydantic的input/ctx可能含整段原文，错误响应只保留字段位置和错误类型。"""
    error_id = uuid.uuid4().hex
    logger.warning('VALIDATION_FAILED error_id=%s', error_id)
    return JSONResponse(status_code=422, content={
        'code': 'VALIDATION_FAILED', 'error_id': error_id,
        'detail': [{'loc': list(error['loc']), 'type': error['type'],
                    'msg': ('正则表达式无效，请检查语法和捕获组'
                            if error['loc'][-1:] == ('pattern',) else '请求参数无效')}
                   for error in exc.errors()],
    })


def protected_spans(text: str, spans: list[dict]) -> list[dict]:
    """验证并合并交叠范围；不能因忽略无效位置或重叠尾部而输出原值。"""
    ordered = []
    for span in spans:
        start, end = span.get('start'), span.get('end')
        if (type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(text)):
            raise SecurityError('MASK_LOCATION_INVALID', '脱敏位置无效，请重新检测')
        ordered.append(dict(span))
    merged = []
    for span in sorted(ordered, key=lambda item: (item['start'], item['end'])):
        if merged and span['start'] < merged[-1]['end']:
            merged[-1]['end'] = max(merged[-1]['end'], span['end'])
        else:
            merged.append(span)
    return merged


def protected_text(text: str, finding, requested_level=None, mappings=None) -> str:
    """持久化和未授权视图至少partial；高危或更强请求使用block。"""
    from .identify.masking import mask_spans_with_strategy
    from .identify.types import Level

    spans = protected_spans(text, finding.spans)
    if not spans or finding.requires_full_mask:
        if mappings is not None and text != '[已屏蔽:敏感信息]':
            mappings.append(dict(start=0, end=len(text), masked_start=0, masked_end=len('[已屏蔽:敏感信息]'),
                                 type=','.join(finding.types), strategy='block', full_segment=True))
        return '[已屏蔽:敏感信息]'  # 有类别证据但无精确位置时，不沿用可能含原值的副本
    high = finding.level == Level.HIGH or requested_level == Level.HIGH
    try:
        return mask_spans_with_strategy(text, spans, 'block' if high else 'partial', mappings=mappings)
    except SecurityError:
        raise
    except Exception:
        raise SecurityError('MASKING_FAILED', '脱敏处理失败，请重新检测') from None
