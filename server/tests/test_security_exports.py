"""结构化输出必须回写每个受保护值；不支持和无效定位必须失败关闭。"""

import csv
import io
import json

import pytest

from app.identify.pipeline import DetectionPipeline, DetectOptions
from app.parsers.registry import parse_csv, parse_json, parse_sql
from app.security import SecurityError
from app.tasks.document_export import build_masked_bytes

PHONE = '13800138000'
OTHER = '13900139000'


def export(document, strategy='block', fmt='auto'):
    report = DetectionPipeline().analyze_document(document, DetectOptions())
    assert report.all_findings
    return build_masked_bytes(document, report, strategy=strategy, fmt=fmt, secret='test-only')[0]


@pytest.mark.parametrize('key', ['a.b', 'a[0]', '', 'quote"key', 'x].y', 'normal'])
@pytest.mark.parametrize('strategy', ['block', 'partial', 'hash', 'generalize', 'pseudonym', 'encrypt'])
def test_json_special_keys_and_nested_collision_are_masked(client, key, strategy):
    document = parse_json(json.dumps({key: PHONE, 'a': {'b': OTHER}, 'count': 42}))
    result = export(document, strategy)
    assert PHONE.encode() not in result
    assert OTHER.encode() not in result
    assert json.loads(result)['count'] == 42


def test_numeric_json_sensitive_scalar_is_detected_and_masked(client):
    result = json.loads(export(parse_json('{"phone":13800138000,"note":"hello","count":42,"ok":true}')))
    assert str(result['phone']) != PHONE
    assert result['count'] == 42
    assert result['ok'] is True


@pytest.mark.parametrize('header', ['phone,phone', ',phone', 'phone,col2,col2'])
def test_csv_coordinates_survive_duplicate_or_empty_headers(client, header):
    source = header + '\n' + ','.join([PHONE, OTHER] + (['hello'] if header.count(',') == 2 else []))
    result = export(parse_csv(source)).decode()
    assert PHONE not in result and OTHER not in result
    assert len(list(csv.reader(io.StringIO(result)))[1]) == header.count(',') + 1


@pytest.mark.parametrize('sql', [
    "INSERT INTO u(id,password) VALUES(1,'abcdef123456'),(NULL,'othersecret123456');",
    "INSERT INTO u(id,password) VALUES(fn(1,2),'abcdef123456'),(3,'othersecret123456');",
])
def test_sql_values_columns_are_not_string_literal_indexes(client, sql):
    result = export(parse_sql(sql)).decode()
    assert 'abcdef123456' not in result
    assert 'othersecret123456' not in result
    import sqlite3
    if 'fn(' not in result:
        conn = sqlite3.connect(':memory:')
        conn.execute('create table u(id integer, password text)')
        conn.executescript(result)
        assert conn.execute('select count(*) from u').fetchone()[0] == 2


def test_cross_format_export_does_not_return_raw_json(client):
    response = client.post('/api/v1/detect/export', json={
        'text': json.dumps({'phone': PHONE}), 'content_type': 'json', 'fmt': 'sql', 'strategy': 'block',
    })
    assert response.status_code == 422
    assert response.json()['code'] == 'EXPORT_FORMAT_UNSUPPORTED'
    assert PHONE not in response.text


@pytest.mark.parametrize('kind', ['json', 'csv', 'sql'])
def test_unknown_finding_location_cannot_export_original(client, kind):
    document = {'json': lambda: parse_json('{"phone":"' + PHONE + '"}'),
                'csv': lambda: parse_csv('phone\n' + PHONE),
                'sql': lambda: parse_sql("insert into u(phone) values('" + PHONE + "')")}[kind]()
    report = DetectionPipeline().analyze_document(document, DetectOptions())
    report.all_findings[0].path = 'nonexistent'
    with pytest.raises(SecurityError, match='MASK_LOCATION_INVALID'):
        build_masked_bytes(document, report, strategy='block', fmt='auto', secret='test')


def test_export_merges_overlapping_ranges_without_leaking_tail(client):
    document = parse_json('{"phone":"' + PHONE + '"}')
    report = DetectionPipeline().analyze_document(document, DetectOptions())
    report.all_findings[0].spans = [
        {'start': 0, 'end': 7, 'type': 'phone'}, {'start': 4, 'end': 11, 'type': 'phone'},
    ]
    result = build_masked_bytes(document, report, strategy='block', fmt='auto', secret='test')[0]
    assert PHONE.encode() not in result
    assert PHONE[:4].encode() not in result


def test_conflicting_rules_do_not_leave_sensitive_overlap_tail(client):
    for id_, pattern, type_, weight in [('overlap_low', '(ABC123)', 'phone', .3),
                                        ('overlap_high', '(123SECRET)', 'secret', .99)]:
        assert client.post('/api/v1/rules', json={
            'id': id_, 'name': id_, 'pattern': pattern, 'type': type_, 'weight': weight, 'confidence': .99,
        }).status_code == 201
    response = client.post('/api/v1/detect/export', json={'text': 'ABC123SECRET', 'strategy': 'block'})
    assert response.status_code == 200
    assert 'SECRET' not in response.text


def test_nested_secret_does_not_survive_email_generalization(client):
    text = 'me@sk-abcdefghijklmnop.com'
    response = client.post('/api/v1/detect/export', json={'text': text, 'strategy': 'generalize'})
    assert response.status_code == 200
    assert 'sk-abcdefghijklmnop' not in response.text


def test_stronger_custom_email_cannot_expose_nested_secret(client):
    client.post('/api/v1/rules', json={'id': 'strong_email', 'name': 'strong', 'type': 'email',
                'pattern': '(me@sk-[a-z]+\\.com)', 'weight': 1, 'confidence': 1})
    response = client.post('/api/v1/detect/export', json={
        'text': 'me@sk-abcdefghijklmnop.com', 'strategy': 'generalize'})
    assert response.status_code == 200
    assert 'sk-abcdefghijklmnop' not in response.text


@pytest.mark.parametrize('kind,value', [('address', '北京'), ('name', '李'), ('phone', '123'),
                                      ('bank_card', '1234'), ('id_card', '123456')])
def test_short_generalization_does_not_preserve_complete_value(kind, value):
    from app.identify.masking import mask_value_strategy
    assert value not in mask_value_strategy(value, kind, 'generalize')


def test_nonpositional_sensitive_evidence_requires_full_protection(client):
    from app.identify.l2 import SemanticHit
    from app.identify.types import EntityType
    from app.parsers.text import parse_text
    from app.security import protected_text
    class Classifier:
        def available(self): return True
        def predict(self, text): return [SemanticHit(EntityType.SECRET, .99)]
    document = parse_text(PHONE + ' PRIVATE_TOKEN_UNLOCATED')
    report = DetectionPipeline(semantic=Classifier()).analyze_document(document, DetectOptions(enable_l2=True))
    result = build_masked_bytes(document, report, strategy='block', fmt='txt', secret='test')[0]
    assert b'PRIVATE_TOKEN_UNLOCATED' not in result
    assert 'PRIVATE_TOKEN_UNLOCATED' not in protected_text(document.raw, report.findings[0])


def test_ner_overlap_does_not_leave_uncovered_tail(client):
    from app.identify.ner import NerSpan
    from app.parsers.text import parse_text
    class Ner:
        def available(self): return True
        def detect(self, text): return [NerSpan(0, len(text), 'LOC')]
    document = parse_text(PHONE + ' PRIVATE_ADDRESS_TAIL')
    report = DetectionPipeline(ner=Ner()).analyze_document(document, DetectOptions(enable_ner=True))
    result = build_masked_bytes(document, report, strategy='block', fmt='txt', secret='test')[0]
    assert b'PRIVATE_ADDRESS_TAIL' not in result
