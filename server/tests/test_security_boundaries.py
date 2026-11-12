"""核心隐私边界回归；仅使用合成数据，不将敏感值写入日志。"""

import pytest

from app.api.deps import pipeline_dep
from app.core.config import get_settings
from app.identify.pipeline import DetectOptions

PHONE = "13800138000"
CARD = "11010519491231002X"
TEXT = f"联系电话 {PHONE}，身份证 {CARD}"


@pytest.mark.parametrize("level", ["SAFE", "L1", None])
@pytest.mark.parametrize("text", [TEXT, "联系 foo@example.com"])
def test_unauthorized_mask_has_safe_floor(client, level, text):
    response = client.post('/api/v1/dynamic/render', json={
        'text': text, 'authorized': False, 'mask_level': level,
    })
    assert response.status_code == 200
    body = response.json()
    assert body['authorized'] is False
    for value in (PHONE, CARD, 'foo@example.com'):
        assert value not in body['text']
        assert value not in body['masked']


def test_dynamic_detection_failure_is_safe_and_traceable(client, monkeypatch, caplog):
    pipeline = pipeline_dep(get_settings())
    def fail(*args, **kwargs):
        raise RuntimeError(PHONE)
    monkeypatch.setattr(pipeline, 'analyze_text', fail)
    response = client.post('/api/v1/dynamic/render', json={'text': TEXT})
    assert response.status_code == 503
    body = response.json()
    assert body['code'] == 'DETECTION_FAILED'
    assert body['error_id']
    assert PHONE not in response.text
    assert PHONE not in caplog.text


def test_invalid_mask_location_cannot_fall_back_to_original(client, monkeypatch, caplog):
    pipeline = pipeline_dep(get_settings())
    finding = pipeline.analyze_text(TEXT, DetectOptions())
    finding.spans = [{'start': -1, 'end': 999, 'type': 'phone'}]
    finding.masked = TEXT
    monkeypatch.setattr(pipeline, 'analyze_text', lambda *args, **kwargs: finding)
    response = client.post('/api/v1/dynamic/render', json={'text': TEXT})
    assert response.status_code == 422
    assert response.json()['code'] == 'MASK_LOCATION_INVALID'
    assert PHONE not in response.text + caplog.text


def test_nonallowlisted_actor_stays_masked_with_override(client, monkeypatch):
    monkeypatch.setenv('FLOWMASK_REVEAL_OPERATORS', 'owner')
    get_settings.cache_clear()
    body = client.post('/api/v1/dynamic/render', json={
        'text': TEXT, 'authorized': True, 'mask_level': 'SAFE',
    }, headers={'X-Operator-Id': 'visitor'}).json()
    assert body['authorized'] is False
    assert CARD not in body['text']


def test_lawful_original_access_is_preserved(client, monkeypatch):
    monkeypatch.setenv('FLOWMASK_REVEAL_OPERATORS', 'owner')
    get_settings.cache_clear()
    body = client.post('/api/v1/dynamic/render', json={
        'text': TEXT, 'authorized': True,
    }, headers={'X-Operator-Id': 'owner'}).json()
    assert body['authorized'] is True
    assert body['text'] == TEXT
    assert CARD not in body['masked']
