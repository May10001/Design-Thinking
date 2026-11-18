import json
from uuid import uuid4
from sqlalchemy import text
from app.core.database import immediate_write_transaction
from app.core.errors import BusinessError, not_found
from app.services.common import canonical_json, digest, utc_now
from app.services.task_service import create_task, get_task, task_view, update_task


def confirm_import(engine, user_id, import_id, request, idempotency_key, raw_request=None):
    if not idempotency_key or len(idempotency_key) > 128:
        raise BusinessError('VALIDATION_ERROR', '幂等键长度须为 1–128', 422)
    request_hash = digest({'import_id':str(import_id), 'request':raw_request if raw_request is not None else request.model_dump(mode='json', exclude_unset=True)})
    with immediate_write_transaction(engine) as session:
        saved = session.execute(text('SELECT * FROM review_batches WHERE user_id=:u AND idempotency_key=:k'), {'u':user_id, 'k':idempotency_key}).mappings().first()
        if saved:
            if saved['request_hash'] != request_hash:
                raise BusinessError('IDEMPOTENCY_KEY_REUSED', '幂等键已用于其他请求')
            return json.loads(saved['result_json'])
        record = session.execute(text('SELECT * FROM imports WHERE id=:i AND user_id=:u'), {'i':str(import_id), 'u':user_id}).mappings().first()
        if not record:
            raise not_found()
        if record['status'] != 'REVIEW_READY':
            raise BusinessError('IMPORT_STATE_INVALID', '当前状态不能确认')
        if record['version'] != request.expected_import_version:
            raise BusinessError('IMPORT_VERSION_CONFLICT', '通知已更新，请刷新', details={'current_version':record['version']})
        results = []
        now = utc_now()
        for item in request.items:
            draft = session.execute(text('SELECT * FROM draft_items WHERE id=:d AND import_id=:i AND user_id=:u'), {'d':str(item.draft_item_id),'i':str(import_id),'u':user_id}).mappings().first()
            if not draft:
                raise not_found()
            if draft['status'] != 'PENDING':
                raise BusinessError('DRAFT_ALREADY_REVIEWED', '草稿已经确认')
            task = None
            if item.action == 'CREATE':
                task = create_task(session, user_id, item.final_task)
            elif item.action in ('UPDATE','KEEP'):
                existing = get_task(session, user_id, item.target_task_id)
                if existing['version'] != item.expected_task_version:
                    raise BusinessError('TASK_VERSION_CONFLICT', '任务已被修改，请重新确认', details={'task_id':str(item.target_task_id), 'current_version':existing['version']})
                task = update_task(session, user_id, item.target_task_id, item.expected_task_version, item.final_task) if item.action == 'UPDATE' else task_view(existing)
            session.execute(text('''UPDATE draft_items SET status=:status,final_action=:action,final_task_id=:task_id,finalized_task_json=:snapshot,confirmed_at=:now,updated_at=:now WHERE id=:id AND user_id=:user AND status='PENDING' '''), {'status':'SKIPPED' if item.action == 'SKIP' else 'CONFIRMED', 'action':item.action, 'task_id':task['id'] if task else None, 'snapshot':canonical_json(task) if task else None, 'now':now, 'id':str(item.draft_item_id), 'user':user_id})
            results.append({'draft_item_id':str(item.draft_item_id),'action':item.action,'task_id':task['id'] if task else None,'task_version':task['version'] if task else None})
        pending = session.execute(text("SELECT count(*) FROM draft_items WHERE import_id=:i AND user_id=:u AND status='PENDING'"), {'i':str(import_id),'u':user_id}).scalar_one()
        status = 'REVIEW_READY' if pending else 'COMPLETED'
        updated = session.execute(text('UPDATE imports SET status=:s,version=version+1,updated_at=:now WHERE id=:i AND user_id=:u AND version=:v'), {'s':status,'now':now,'i':str(import_id),'u':user_id,'v':request.expected_import_version})
        if updated.rowcount != 1:
            raise BusinessError('IMPORT_VERSION_CONFLICT','通知已更新，请刷新')
        batch_id = str(uuid4())
        result = {'confirmation_id':batch_id, 'import_id':str(import_id), 'status':status, 'version':record['version']+1, 'pending_count':pending, 'results':results}
        session.execute(text('INSERT INTO review_batches (id,import_id,user_id,idempotency_key,request_hash,result_json,created_at) VALUES (:id,:i,:u,:k,:h,:r,:now)'), {'id':batch_id,'i':str(import_id),'u':user_id,'k':idempotency_key,'h':request_hash,'r':canonical_json(result),'now':now})
        return result
