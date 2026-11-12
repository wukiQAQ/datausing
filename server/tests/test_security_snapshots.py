"""检测快照的授权、不可变证据、策略一致性、寿命和并发回归。"""

from concurrent.futures import ThreadPoolExecutor
import json

from app.api.deps import pipeline_dep
from app.core.config import get_settings

PHONE = '13800138000'


def detect(client, text=PHONE):
    response = client.post('/api/v1/detect', json={'text': text})
    assert response.status_code == 200
    result = response.json()
    assert result.get('result_id'), 'detection must return a snapshot handle'
    return result


def download(client, result, **extra):
    return client.post('/api/v1/detect/export', json={
        'result_id': result['result_id'], 'policy_version': result['policy_version'],
        'strategy': 'block', 'fmt': 'txt', **extra,
    })


def test_rule_change_does_not_reidentify_snapshot(client, monkeypatch):
    rule = client.post('/api/v1/rules', json={
        'id': 'snapshot_secret', 'name': 'snapshot', 'type': 'secret',
        'pattern': r'SEC_(\d{6})', 'weight': .9, 'confidence': .9,
    })
    assert rule.status_code == 201
    result = detect(client, 'SEC_123456')
    client.patch('/api/v1/rules/snapshot_secret', json={'enabled': False})
    pipeline = pipeline_dep(get_settings())
    monkeypatch.setattr(pipeline, 'analyze_document', lambda *a: (_ for _ in ()).throw(AssertionError('must not infer again')))
    response = download(client, result)
    assert response.status_code == 200
    assert '123456' not in response.text
    assert response.headers['x-flowmask-result-id'] == result['result_id']


def test_policy_version_change_rejects_old_snapshot(client, monkeypatch):
    from app import security
    result = detect(client)
    monkeypatch.setattr(security, 'POLICY_VERSION', 'changed-policy')
    response = download(client, result)
    assert response.status_code == 409
    assert response.json()['code'] == 'SNAPSHOT_POLICY_CHANGED'
    assert PHONE not in response.text


def test_changed_input_or_options_rejected(client):
    result = detect(client)
    assert download(client, result, text='different').status_code == 409
    assert download(client, result, enable_l3=True).status_code == 409


def test_snapshot_cannot_be_retrieved_by_another_session(client):
    result = detect(client)
    client.cookies.clear()
    response = download(client, result)
    assert response.status_code == 403
    assert PHONE not in response.text


def test_snapshot_expiry_is_explicit_and_never_falls_back(client, monkeypatch):
    from app.identify.snapshots import get_snapshot_store
    result = detect(client)
    store = get_snapshot_store()
    clock = store.clock
    monkeypatch.setattr(store, 'clock', lambda: clock() + 301)
    response = download(client, result, text=PHONE)
    assert response.status_code == 410
    assert response.json()['code'] == 'SNAPSHOT_EXPIRED'
    assert PHONE not in response.text


def test_concurrent_duplicate_exports_are_stable_without_inference(client, monkeypatch):
    result = detect(client)
    pipeline = pipeline_dep(get_settings())
    monkeypatch.setattr(pipeline, 'analyze_document', lambda *a: (_ for _ in ()).throw(AssertionError('must not infer again')))
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: download(client, result, strategy='encrypt'), range(8)))
    assert all(r.status_code == 200 for r in responses)
    assert len({r.content for r in responses}) == 1
    assert PHONE not in responses[0].text


def test_strategy_preview_and_export_share_generated_replacements(client):
    result = detect(client)
    for strategy in ('partial', 'block', 'hash', 'generalize', 'pseudonym', 'encrypt'):
        preview = client.post(f"/api/v1/detect/results/{result['result_id']}/preview", json={
            'strategy': strategy, 'policy_version': result['policy_version'],
        })
        assert preview.status_code == 200
        response = download(client, result, strategy=strategy)
        assert response.status_code == 200
        assert response.text == preview.json()['masked_document']['masked']


def test_file_snapshot_does_not_reparse_changed_upload(client):
    result = client.post('/api/v1/detect/file', files={'file': ('test.json', json.dumps({'phone': PHONE}).encode())}).json()
    assert result.get('result_id')
    response = client.post('/api/v1/detect/file/export', data={
        'result_id': result['result_id'], 'strategy': 'block', 'fmt': 'json',
    })
    assert response.status_code == 200
    assert PHONE not in response.text
    response = client.post('/api/v1/detect/file/export', data={'result_id': result['result_id']},
                           files={'file': ('changed.json', b'{"phone":"different"}')})
    assert response.status_code == 409


def test_snapshot_capacity_is_bounded_and_eviction_is_explicit(client, monkeypatch):
    from app.identify.snapshots import get_snapshot_store
    store = get_snapshot_store()
    monkeypatch.setattr(store, 'max_items', 1)
    first = detect(client)
    second = detect(client, PHONE + ',hello')
    assert download(client, first).status_code == 410
    assert download(client, second).status_code == 200
    assert store.total_bytes <= store.max_bytes


def test_explicit_structured_detection_and_snapshot_export_use_same_parser(client):
    response = client.post('/api/v1/detect', json={'text': 'password\nabcdef123456', 'content_type': 'csv'})
    result = response.json()
    assert result['content_type'] == 'csv'
    assert result['findings']
    exported = download(client, result, fmt='csv')
    assert exported.status_code == 200
    assert 'abcdef123456' not in exported.text


def test_one_detection_freezes_rules_during_concurrent_change(client, monkeypatch):
    from app.identify.rules import set_active_rules
    pipeline = pipeline_dep(get_settings())
    original = pipeline.analyze_segment
    def analyze(*args, **kwargs):
        finding = original(*args, **kwargs)
        set_active_rules(())
        return finding
    monkeypatch.setattr(pipeline, 'analyze_segment', analyze)
    result = detect(client, '{"a":"13800138000","b":"13900139000"}')
    assert len(result['findings']) == 2


def test_file_snapshot_rejects_detection_option_change(client):
    result = client.post('/api/v1/detect/file', files={'file': ('t.txt', PHONE.encode())}).json()
    response = client.post('/api/v1/detect/file/export', data={
        'result_id': result['result_id'], 'enable_l3': 'true',
    })
    assert response.status_code == 409


def test_snapshot_byte_limit_fails_without_plaintext_response(client, monkeypatch):
    from app.identify.snapshots import get_snapshot_store
    store = get_snapshot_store()
    monkeypatch.setattr(store, 'max_bytes', 1)
    response = client.post('/api/v1/detect', json={'text': PHONE})
    assert response.status_code == 503
    assert response.json()['code'] == 'SNAPSHOT_CAPACITY'
    assert PHONE not in response.text


def test_request_validation_does_not_echo_sensitive_input(client):
    response = client.post('/api/v1/dynamic/render', json={'text': PHONE * 2000})
    assert response.status_code == 422
    assert response.json()['code'] == 'VALIDATION_FAILED'
    assert PHONE not in response.text


def test_snapshot_mask_failure_is_safe_and_not_logged(client, monkeypatch, caplog):
    from app.tasks import document_export
    result = detect(client)
    def fail(*args, **kwargs):
        raise RuntimeError(PHONE)
    monkeypatch.setattr(document_export, 'mask_spans_with_strategy', fail)
    response = download(client, result)
    assert response.status_code == 422
    assert response.json()['code'] == 'MASKING_FAILED'
    assert PHONE not in response.text + caplog.text


def test_inactive_snapshots_are_actually_reaped(client):
    from app.identify.snapshots import SnapshotStore
    from app.identify.pipeline import DetectionPipeline, DetectOptions
    from app.parsers.text import parse_text
    import time
    store = SnapshotStore(ttl=.01, reap_interval=.01)
    document = parse_text(PHONE)
    report = DetectionPipeline().analyze_document(document, DetectOptions())
    store.start()
    try:
        store.put(document, report, {}, 'source', 'rules', 'owner', 'key')
        assert store.total_bytes > 0
        deadline = time.monotonic() + 2
        while store.total_bytes and time.monotonic() < deadline:
            time.sleep(.01)
        assert store.total_bytes == 0
    finally:
        store.close()


def test_legacy_direct_exports_explicitly_identify_fresh_detection(client):
    text = client.post('/api/v1/detect/export', json={'text': PHONE})
    file = client.post('/api/v1/detect/file/export', files={'file': ('t.txt', PHONE.encode())})
    assert text.status_code == file.status_code == 200
    assert text.headers['x-flowmask-detection-mode'] == 'fresh'
    assert file.headers['x-flowmask-detection-mode'] == 'fresh'
