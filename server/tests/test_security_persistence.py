"""任务存储和任务清单导出不能复用会保留原值的L1预览。"""

import time

import pytest

from app.api.deps import pipeline_dep
from app.core.config import get_settings
from app.core.db import get_database

PHONE = '13800138000'
CARD = '11010519491231002X'
EMAIL = 'foo@example.com'
TEXT = f'{CARD}，{PHONE}，{EMAIL}'


def wait_task(client, task_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        task = client.get(f'/api/v1/tasks/{task_id}').json()
        if task['status'] in ('done', 'failed'):
            return task
        time.sleep(.01)
    pytest.fail('isolated task did not finish')


@pytest.mark.parametrize('entry', ['text', 'file', 'batch'])
@pytest.mark.parametrize('level', ['L1', None])
def test_task_persistence_has_safe_floor(client, entry, level):
    text = EMAIL if level is None else TEXT
    if entry == 'text':
        ids = [client.post('/api/v1/tasks/text', json={'text': text, 'mask_level': level}).json()['task_id']]
    elif entry == 'file':
        response = client.post('/api/v1/tasks', params={'mask_level': level} if level else {},
                               files={'file': ('test.txt', text.encode())})
        ids = [response.json()['task_id']]
    else:
        response = client.post('/api/v1/tasks/batch', params={'mask_level': level} if level else {}, files=[
            ('files', ('first.txt', text.encode())), ('files', ('second.txt', text.encode())),
        ])
        ids = [item['task_id'] for item in response.json()['items']]
    for task_id in ids:
        task = wait_task(client, task_id)
        assert task['status'] == 'done'
        rows = client.get(f'/api/v1/tasks/{task_id}/findings').json()['items']
        assert rows
        assert rows[0]['level'] == ('L3' if level else 'L1')
        outputs = ''.join(row['masked'] for row in rows)
        outputs += client.get(f'/api/v1/tasks/{task_id}/export').text
        outputs += client.get(f'/api/v1/tasks/{task_id}/export/privacy?format=json').text
        for raw in (CARD, PHONE, EMAIL):
            assert raw not in outputs
        with get_database().connect() as conn:
            stored = conn.execute('select masked from findings where task_id=?', (task_id,)).fetchall()
        assert all(EMAIL not in row['masked'] for row in stored)


def test_task_exception_does_not_persist_or_log_input(client, monkeypatch, caplog):
    pipeline = pipeline_dep(get_settings())
    def fail(*args, **kwargs):
        raise RuntimeError(CARD)
    monkeypatch.setattr(pipeline, 'analyze_document', fail)
    task_id = client.post('/api/v1/tasks/text', json={'text': TEXT}).json()['task_id']
    task = wait_task(client, task_id)
    assert task['status'] == 'failed'
    assert task['error'].startswith('TASK_DETECTION_FAILED:')
    assert CARD not in str(task) + caplog.text
    assert client.get(f'/api/v1/tasks/{task_id}/findings').json()['total'] == 0


def test_legacy_low_warning_is_protected_on_read_without_rewriting_history(client):
    task_id = client.post('/api/v1/tasks/text', json={'text': EMAIL}).json()['task_id']
    assert wait_task(client, task_id)['status'] == 'done'
    db = get_database()
    with db.write() as conn:
        conn.execute('update findings set masked=? where task_id=?', ('[低危]' + EMAIL, task_id))
        finding_id = conn.execute('select id from findings where task_id=?', (task_id,)).fetchone()[0]
    for path in (f'/findings/{finding_id}', '/findings', f'/tasks/{task_id}/findings',
                 f'/tasks/{task_id}/export', f'/tasks/{task_id}/export/privacy?format=json'):
        assert EMAIL not in client.get('/api/v1' + path).text
    with db.connect() as conn:
        assert conn.execute('select masked from findings where id=?', (finding_id,)).fetchone()[0] == '[低危]' + EMAIL
