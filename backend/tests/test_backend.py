import asyncio
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.database import create_database, migrate, immediate_write_transaction
from app.main import create_app
from app.schemas.agent import ExtractedTask, MatchBatchResult, MatchResultItem
from app.schemas.review import ConfirmRequest
from app.schemas.task import TaskContent
from app.services.matching_service import MatchingService
from app.services.review_service import confirm_import
from app.services.task_service import create_task

USER = '00000000-0000-4000-8000-000000000001'
OTHER = '00000000-0000-4000-8000-000000000002'
NOW = '2026-11-17T09:00:00Z'

def content(**kwargs):
    data = dict(title='实验报告', description=None, course_name='机器学习', external_ref='ML-03', due_kind='DATE', due_date='2026-11-20', due_at=None, timezone='Asia/Shanghai', requirements='上传 PDF')
    data.update(kwargs)
    return data

@pytest.fixture
def db(tmp_path):
    engine = create_database(f'sqlite:///{tmp_path}/test.db', enforce_sqlite_version=False)
    migrate(engine)
    with engine.begin() as conn:
        conn.execute(text('INSERT INTO users VALUES (:id, :name, :tz, :now)'), [dict(id=uid, name='用户', tz='Asia/Shanghai', now=NOW) for uid in (USER, OTHER)])
    return engine

def seed_import(db, count=1):
    iid = str(uuid.uuid4())
    drafts = [str(uuid.uuid4()) for _ in range(count)]
    with db.begin() as c:
        c.execute(text("INSERT INTO imports (id,user_id,input_type,sanitized_text,reference_time,timezone,status,version,created_at,updated_at) VALUES (:id,:u,'TEXT','safe',:now,'Asia/Shanghai','REVIEW_READY',3,:now,:now)"), dict(id=iid,u=USER,now=NOW))
        for i, draft in enumerate(drafts, 1):
            c.execute(text("INSERT INTO draft_items (id,import_id,user_id,ordinal,extracted_json,proposed_task_json,suggested_action,match_details_json,created_at,updated_at) VALUES (:id,:iid,:u,:i,'{}','{}','CREATE','{}',:now,:now)"), dict(id=draft,iid=iid,u=USER,i=i,now=NOW))
    return iid, drafts

def test_task_validation_and_utc_conversion():
    item = TaskContent(**content(due_kind='DATETIME', due_date='2026-11-20', due_at='2026-11-20T23:00:00+08:00'))
    assert item.model_dump(mode='json')['due_at'] == '2026-11-20T15:00:00Z'
    with pytest.raises(ValueError):
        TaskContent(**content(due_kind='DATETIME', due_at='2026-11-20T23:00:00'))
    with pytest.raises(ValueError):
        TaskContent(**content(due_kind='DATETIME', due_date='2026-11-21', due_at='2026-11-20T23:00:00+08:00'))

def test_confirm_idempotency_and_partial_batch(db):
    iid, drafts = seed_import(db, 2)
    req = ConfirmRequest.model_validate({'expected_import_version': 3, 'items': [{'draft_item_id': drafts[0], 'action': 'CREATE', 'final_task': content()}]})
    first = confirm_import(db, USER, iid, req, 'key')
    assert first['status'] == 'REVIEW_READY' and first['pending_count'] == 1
    assert confirm_import(db, USER, iid, req, 'key') == first
    with db.connect() as c:
        assert c.execute(text('SELECT count(*) FROM tasks')).scalar_one() == 1
    altered = ConfirmRequest.model_validate({'expected_import_version': 3, 'items': [{'draft_item_id': drafts[1], 'action': 'SKIP'}]})
    with pytest.raises(Exception, match='IDEMPOTENCY_KEY_REUSED'):
        confirm_import(db, USER, iid, altered, 'key')

def test_confirm_conflict_rolls_back_entire_batch(db):
    iid, drafts = seed_import(db, 2)
    with immediate_write_transaction(db) as session:
        task = create_task(session, USER, TaskContent(**content()))
    req = ConfirmRequest.model_validate({'expected_import_version': 3, 'items': [
        {'draft_item_id': drafts[0], 'action': 'CREATE', 'final_task': content(title='新任务')},
        {'draft_item_id': drafts[1], 'action': 'KEEP', 'target_task_id': task['id'], 'expected_task_version': 99}]})
    with pytest.raises(Exception, match='TASK_VERSION_CONFLICT'):
        confirm_import(db, USER, iid, req, 'other-key')
    with db.connect() as c:
        assert c.execute(text('SELECT count(*) FROM tasks')).scalar_one() == 1
        assert c.execute(text("SELECT count(*) FROM draft_items WHERE status='PENDING'")).scalar_one() == 2

def test_tasks_http_filters_and_version(db):
    with immediate_write_transaction(db) as session:
        task = create_task(session, USER, TaskContent(**content(title='100% 作业')))
        create_task(session, OTHER, TaskContent(**content(title='private')))
    client = TestClient(create_app(db, USER))
    assert client.get('/api/v1/tasks', params={'q':'%'}).json()['total'] == 1
    assert client.get('/api/v1/tasks', params={'q':'private'}).json()['total'] == 0
    response = client.patch('/api/v1/tasks/'+task['id'], json={'expected_version':1,'changes':{'requirements':None}})
    assert response.status_code == 200 and response.json()['version'] == 2 and response.json()['requirements'] is None
    assert client.patch('/api/v1/tasks/'+task['id'], json={'expected_version':1,'changes':{'title':'a'}}).status_code == 409
    assert client.patch('/api/v1/tasks/'+task['id'], json={'expected_version':2,'changes':{'due_date':'2026-12-01'}}).status_code == 422

def test_identity_match_preserves_missing_fields_and_ambiguity(db):
    with immediate_write_transaction(db) as session:
        create_task(session, USER, TaskContent(**content()))
    extracted = ExtractedTask.model_validate({**content(due_date='2026-11-23', requirements=None), 'observed_fields':['course_name','external_ref','deadline'], 'uncertain_fields':[], 'reason':'日期变化','source_excerpt':'日期改为 11 月 23 日'})
    result = asyncio.run(MatchingService(db).suggest(USER, [('draft', extracted)]))[0]
    assert result['suggested_action'] == 'UPDATE'
    assert result['proposed_task']['requirements'] == '上传 PDF'
    with immediate_write_transaction(db) as session:
        create_task(session, USER, TaskContent(**content(title='另一个')))
    result = asyncio.run(MatchingService(db).suggest(USER, [('draft', extracted)]))[0]
    assert result['suggested_action'] == 'REVIEW'

def test_database_foreign_keys_prevent_cross_user_task(db):
    iid, drafts = seed_import(db)
    with immediate_write_transaction(db) as session:
        task = create_task(session, OTHER, TaskContent(**content()))
    with pytest.raises(Exception):
        with db.begin() as c:
            c.execute(text('UPDATE draft_items SET matched_task_id=:id, matched_task_version=1 WHERE id=:draft'), dict(id=task['id'], draft=drafts[0]))

def test_agent_batch_uses_redacted_snapshots_and_validates_candidates(db):
    with immediate_write_transaction(db) as session:
        old = create_task(session, USER, TaskContent(**content(requirements='手机号 13800000000')))
    extracted = ExtractedTask.model_validate({**content(external_ref=None), 'observed_fields':['title','course_name'], 'uncertain_fields':[], 'reason':'','source_excerpt':'safe'})
    class Agent:
        async def match_tasks(self, request):
            assert request.items[0].candidate_tasks[0]['requirements'] == '[redacted]'
            return {'items':[{'draft_item_id':'draft', 'relation':'SAME', 'matched_task_id':old['id'], 'reason':'same', 'uncertain_fields':[]}]}
    def redact(candidate):
        candidate['requirements'] = '[redacted]'
        return candidate
    result = asyncio.run(MatchingService(db, Agent(), redact).suggest(USER, [('draft', extracted)]))[0]
    assert result['suggested_action'] == 'DUPLICATE'
    assert result['match_details']['candidate_tasks'][0]['requirements'] == '手机号 13800000000'
    class BadAgent:
        async def match_tasks(self, request):
            return {'items':[{'draft_item_id':'draft','relation':'SAME','matched_task_id':str(uuid.uuid4()),'reason':'bad','uncertain_fields':[]}]}
    with pytest.raises(Exception, match='LLM_INVALID_OUTPUT'):
        asyncio.run(MatchingService(db, BadAgent(), redact).suggest(USER, [('draft', extracted)]))


def test_agent_failure_is_not_classified_as_invalid_output(db):
    with immediate_write_transaction(db) as session:
        create_task(session, USER, TaskContent(**content()))
    extracted = ExtractedTask.model_validate({**content(external_ref=None), 'observed_fields':['title','course_name'], 'uncertain_fields':[], 'reason':'','source_excerpt':'safe'})
    class Agent:
        async def match_tasks(self, request):
            raise ConnectionError('service unavailable')
    with pytest.raises(Exception, match='LLM_UPSTREAM_ERROR'):
        asyncio.run(MatchingService(db, Agent(), lambda task: task).suggest(USER, [('draft', extracted)]))


def test_http_confirm_validates_and_replays(db):
    iid, drafts = seed_import(db)
    client = TestClient(create_app(db, USER))
    payload = {'expected_import_version':3, 'items':[{'draft_item_id':drafts[0], 'action':'CREATE', 'final_task':content()}]}
    endpoint = '/api/v1/imports/' + iid + '/confirm'
    assert client.post(endpoint, json=payload).status_code == 422
    first = client.post(endpoint, json=payload, headers={'Idempotency-Key':'a'})
    second = client.post(endpoint, json=payload, headers={'Idempotency-Key':'a'})
    assert first.status_code == 200 and first.json() == second.json()
    assert first.json()['status'] == 'COMPLETED'
    assert 'X-Request-Id' in first.headers


def test_truncated_candidates_cannot_be_classified_create(db):
    with immediate_write_transaction(db) as session:
        for i in range(6):
            create_task(session, USER, TaskContent(**content(title=f'作业 {i}', external_ref=f'编号 {i}')))
    extracted = ExtractedTask.model_validate({**content(title='作业', external_ref=None), 'observed_fields':['title','course_name'], 'uncertain_fields':[], 'reason':'','source_excerpt':'safe'})
    class Agent:
        async def match_tasks(self, request):
            return {'items':[{'draft_item_id':'draft', 'relation':'DIFFERENT', 'matched_task_id':None, 'reason':'不同', 'uncertain_fields':[]}]}
    result = asyncio.run(MatchingService(db, Agent(), lambda candidate:candidate).suggest(USER, [('draft', extracted)]))[0]
    assert result['suggested_action'] == 'REVIEW' and result['match_details']['candidates_truncated']

def test_idempotency_detects_changed_raw_request_even_if_normalized_values_match(db):
    iid, drafts = seed_import(db)
    client = TestClient(create_app(db, USER))
    endpoint = '/api/v1/imports/' + iid + '/confirm'
    payload = {'expected_import_version':3, 'items':[{'draft_item_id':drafts[0], 'action':'CREATE', 'final_task':content()}]}
    assert client.post(endpoint, json=payload, headers={'Idempotency-Key':'raw'}).status_code == 200
    payload['items'][0]['final_task']['title'] = ' 实验报告 '
    changed = client.post(endpoint, json=payload, headers={'Idempotency-Key':'raw'})
    assert changed.status_code == 409 and changed.json()['error']['code'] == 'IDEMPOTENCY_KEY_REUSED'

def test_normalized_content_hash_duplicate_does_not_suggest_update(db):
    with immediate_write_transaction(db) as session:
        create_task(session, USER, TaskContent(**content(title='Report', external_ref='ML-03')))
    task = ExtractedTask.model_validate({**content(title='report', external_ref='ml-03'), 'observed_fields':['title','description','course_name','external_ref','deadline','requirements'], 'uncertain_fields':[], 'reason':'','source_excerpt':'safe'})
    result = asyncio.run(MatchingService(db).suggest(USER, [('draft', task)]))[0]
    assert result['suggested_action'] == 'DUPLICATE'


def test_missing_title_and_unresolved_date_are_reviewable(db):
    task = ExtractedTask.model_validate({**content(title=None, due_kind='UNRESOLVED', due_date=None), 'observed_fields':[], 'uncertain_fields':['deadline'], 'reason':'缺少时间','source_excerpt':'safe'})
    result = asyncio.run(MatchingService(db).suggest(USER, [('draft', task)]))[0]
    assert result['suggested_action'] == 'REVIEW'

def test_reflected_models_and_migration_are_usable(db):
    from app.models import reflect_models
    from sqlalchemy.orm import Session
    models = reflect_models(db)
    with Session(db) as session:
        assert session.query(models.Users).count() == 2
    with db.connect() as connection:
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0001_initial'
        assert connection.exec_driver_sql('PRAGMA foreign_keys').scalar_one() == 1
        assert connection.exec_driver_sql('PRAGMA journal_mode').scalar_one().lower() == 'wal'

def test_concurrent_idempotent_confirms_write_once(db):
    from concurrent.futures import ThreadPoolExecutor
    iid, drafts = seed_import(db)
    req = ConfirmRequest.model_validate({'expected_import_version': 3, 'items': [{'draft_item_id': drafts[0], 'action':'CREATE', 'final_task':content()}]})
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(confirm_import, db, USER, iid, req, 'concurrent') for _ in range(2)]
        results = [f.result() for f in futures]
    assert results[0] == results[1]
    with db.connect() as c:
        assert c.execute(text('SELECT count(*) FROM tasks')).scalar_one() == 1
        assert c.execute(text('SELECT count(*) FROM review_batches')).scalar_one() == 1

def test_task_rejects_numeric_date_and_datetime_inputs():
    with pytest.raises(ValueError):
        TaskContent(**content(due_date=1795132800))
    with pytest.raises(ValueError):
        TaskContent(**content(due_kind='DATETIME', due_at=1795132800))
